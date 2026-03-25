"""PEP 517 build-backend wrapper for ddtrace.

Acts as the ``[build-system] build-backend`` for pyproject.toml.  Before
delegating every PEP 517 hook to ``scikit_build_core.build``, it ensures that
a persistent per-Python build environment exists under ``build/build-env-
{wheel_tag}/`` and injects its ``bin/`` and ``site-packages/`` into the
current process so that cmake, ninja, and scikit-build-core are always
available at stable paths.

This makes both invocation styles work transparently:

    pip install -e .                       # with build isolation (default)
    pip install -e --no-build-isolation .  # without build isolation

In the no-isolation case the build env is the *only* mechanism ensuring the
build tools are present — so it is created here, not externally.

Serverless builds
-----------------
When ``DD_SERVERLESS_BUILD=1`` is set the wheel is published as
``ddtrace-serverless`` instead of ``ddtrace``.  This is handled transparently:
hooks that produce named artefacts (wheel, sdist, metadata) temporarily patch
``pyproject.toml`` with the alternate name for the duration of the call, then
restore the file unconditionally.  No manual pre/post steps are required.
"""

from __future__ import annotations

import contextlib
import functools
import glob as _glob
import os
from pathlib import Path
import re
import subprocess  # nosec B404
import sys
import sysconfig


_REPO_ROOT = Path(__file__).parent.resolve()

_CANONICAL_NAME = "ddtrace"
_SERVERLESS_NAME = "ddtrace-serverless"


def _read_build_requires() -> list[str]:
    """Return ``[build-system].requires`` from pyproject.toml.

    Uses ``tomllib`` (stdlib ≥ 3.11) when available; falls back to a minimal
    regex extractor so no third-party package is needed during early bootstrap.
    """
    pyproject = _REPO_ROOT / "pyproject.toml"

    try:
        import tomllib  # Python 3.11+
    except ImportError:
        tomllib = None

    if tomllib is not None:
        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
        return data.get("build-system", {}).get("requires", [])

    # Minimal fallback: extract quoted strings from the requires = [...] block.
    text = pyproject.read_text()
    m = re.search(r"\[build-system\].*?requires\s*=\s*\[([^\]]*)\]", text, re.DOTALL)
    if not m:
        return []
    return re.findall(r'"([^"]+)"', m.group(1))


# Build deps are authoritative in pyproject.toml [build-system].requires.
_BUILD_DEPS: list[str] = _read_build_requires()
_BUILD_DEPS_KEY = "|".join(sorted(_BUILD_DEPS))


def _wheel_tag() -> str:
    """Compute the wheel tag used to name the build-env directory."""
    major, minor = sys.version_info[:2]
    pt = f"cp{major}{minor}"
    at = f"{pt}t" if getattr(sys, "_is_gil_disabled", False) else pt
    plat = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    return f"{pt}-{at}-{plat}"


def _ensure_build_env() -> None:
    """Create (or reuse) ``build/build-env-{wheel_tag}/`` and inject it.

    Uses ``pip install --prefix`` to populate the directory with the packages
    listed in ``[build-system].requires`` in pyproject.toml.  A marker file
    tracks the installed set so the install step is skipped on subsequent
    invocations.

    After setup, the directory's ``bin/`` is prepended to ``os.environ["PATH"]``
    and its ``site-packages/`` is prepended to ``sys.path`` so that all
    subsequent imports and subprocess calls find the stable-path tools.

    When pip builds with isolation (the default ``pip wheel`` / ``pip install``
    without ``--no-build-isolation``), it creates its own isolated environment
    that already contains all ``[build-system].requires`` packages.  In that
    environment ``pip`` itself is absent, so the ``pip install --prefix`` call
    below would fail.  We detect this case and return early — the tools are
    already present and no further setup is required.
    """
    import importlib.util

    if importlib.util.find_spec("pip") is None:
        # Running inside pip's own isolated build environment: all build deps
        # are already installed by pip before invoking any hook.  Skip setup.
        return

    build_env = _REPO_ROOT / "build" / f"build-env-{_wheel_tag()}"
    marker = build_env / ".build_deps_installed"

    if not (build_env.exists() and marker.exists() and marker.read_text().strip() == _BUILD_DEPS_KEY):
        build_env.mkdir(parents=True, exist_ok=True)
        subprocess.run(  # nosec B603
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--prefix",
                str(build_env),
                "--quiet",
                "--upgrade",
            ]
            + _BUILD_DEPS,
            check=True,
        )
        marker.write_text(_BUILD_DEPS_KEY + "\n")

    # ── Inject into current process ──────────────────────────────────────────
    # site-packages: prepend to sys.path so scikit_build_core is importable.
    candidates = _glob.glob(str(build_env / "lib" / "python*" / "site-packages"))
    candidates += _glob.glob(str(build_env / "Lib" / "site-packages"))  # Windows
    if candidates:
        site_pkgs = candidates[0]
        if site_pkgs not in sys.path:
            sys.path.insert(0, site_pkgs)

    # bin/: prepend to PATH so cmake/ninja subprocesses use the stable copies.
    bin_dir = str(build_env / "bin")
    path = os.environ.get("PATH", "")
    if bin_dir not in path.split(os.pathsep):
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{path}"

    # PYTHONPATH: the pip-generated entry-point scripts in bin/ (cmake, ninja,
    # cython) have a shebang pointing to whichever Python ran `pip install
    # --prefix` (e.g. a riot venv Python).  When those scripts are later
    # invoked as subprocesses, that Python's sys.path doesn't include the
    # build-env site-packages, so `from cmake import cmake` etc. fail.
    # Prepending site-packages to PYTHONPATH ensures the wrapper scripts can
    # import their packages regardless of which Python the shebang names.
    if candidates:
        pythonpath = os.environ.get("PYTHONPATH", "")
        if site_pkgs not in pythonpath.split(os.pathsep):
            os.environ["PYTHONPATH"] = f"{site_pkgs}{os.pathsep}{pythonpath}" if pythonpath else site_pkgs


def _ensure_dedup_headers() -> None:
    """Install ``dedup_headers`` from libdatadog if not already on PATH.

    ``dedup_headers`` is a libdatadog tool that post-processes the C headers
    generated by the Rust build (via Corrosion) to remove duplicate type
    definitions.  Without it the dd_wrapper and _memalloc C++ compilation
    fails with "redefinition of struct …" errors.

    The tool is installed into the user's Cargo bin directory
    (``~/.cargo/bin/``) and persists across builds, so it is only
    compiled once per machine.  Skipped on Windows and on 32-bit platforms
    where the profiling feature (and thus Rust-generated profiling headers)
    is not built.
    """
    import struct

    # Only the profiling build path needs dedup_headers.
    if sys.platform == "win32":
        return
    if struct.calcsize("P") != 8:
        return

    # Already available — nothing to do.
    cargo_bin = Path.home() / ".cargo" / "bin"
    dedup_exe = cargo_bin / "dedup_headers"
    if dedup_exe.exists():
        # Ensure it is on PATH for the cmake POST_BUILD step.
        path = os.environ.get("PATH", "")
        if str(cargo_bin) not in path.split(os.pathsep):
            os.environ["PATH"] = f"{cargo_bin}{os.pathsep}{path}"
        return

    # Find cargo.
    import shutil

    cargo_exe = shutil.which("cargo") or str(cargo_bin.parent / "bin" / "cargo")
    if not Path(cargo_exe).exists():
        # cargo not available — cmake will emit a STATUS warning and skip dedup.
        return

    try:
        subprocess.run(  # nosec B603
            [
                cargo_exe,
                "install",
                "--git",
                "https://github.com/DataDog/libdatadog",
                "--bin",
                "dedup_headers",
                "tools",
            ],
            check=True,
        )
    except subprocess.CalledProcessError:
        # Non-fatal: cmake will skip dedup_headers and warn.
        return

    # Make the freshly installed binary visible to cmake.
    path = os.environ.get("PATH", "")
    if str(cargo_bin) not in path.split(os.pathsep):
        os.environ["PATH"] = f"{cargo_bin}{os.pathsep}{path}"


# Run at import time — before scikit_build_core is imported — so that by the
# time pip calls any hook, the tools are already in place.
_ensure_build_env()
_ensure_dedup_headers()


# ── Serverless package renaming ───────────────────────────────────────────────


def _is_serverless() -> bool:
    return os.environ.get("DD_SERVERLESS_BUILD", "").lower() in ("1", "yes", "on", "true")


@contextlib.contextmanager
def _serverless_name_patch():
    """Temporarily rename the package in pyproject.toml for a serverless build.

    scikit-build-core reads the project name from pyproject.toml at hook
    invocation time, so patching the file before delegation is the correct
    override point.  The file is restored unconditionally in the finally block.
    """
    if not _is_serverless():
        yield
        return

    pyproject = _REPO_ROOT / "pyproject.toml"
    original = pyproject.read_text(encoding="utf-8")
    patched = re.sub(
        rf'^(name\s*=\s*)"{re.escape(_CANONICAL_NAME)}"',
        rf'\1"{_SERVERLESS_NAME}"',
        original,
        count=1,
        flags=re.MULTILINE,
    )
    if patched == original:
        # Pattern not found — nothing to patch (already renamed or unexpected format).
        yield
        return

    try:
        pyproject.write_text(patched, encoding="utf-8")
        yield
    finally:
        pyproject.write_text(original, encoding="utf-8")


def _with_serverless_name(fn):
    """Decorator: wrap a PEP 517 hook with the serverless name patch."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _serverless_name_patch():
            return fn(*args, **kwargs)

    return wrapper


def _install_extensions_to_source() -> None:
    """Install compiled extensions into the source tree for editable builds.

    riot's test venvs find ``ddtrace`` via pytest's conftest.py mechanism
    (the project root is added to ``sys.path``) rather than through an
    editable-install finder in the venv's site-packages.  The compiled
    extensions must therefore exist inside the source tree alongside the
    ``.py`` files — otherwise ``import ddtrace.internal.native._native``
    (and every other compiled extension) fails.

    This mirrors the behaviour of ``setup.py build_ext --inplace`` that the
    old build system provided automatically.

    The function runs ``cmake --install <build_dir> --prefix <repo_root>``
    which copies each compiled extension to its source-tree location
    (e.g. ``ddtrace/internal/native/_native.cpython-3xx-*.so``) without
    placing any cmake artefacts (CMakeFiles/, CMakeCache.txt, …) in the
    source tree — those stay inside the persistent cmake binary directory.

    The call is non-fatal: if cmake is not yet on PATH or the build
    directory does not exist the warning is printed and the hook returns
    normally.  The extensions will be present after the first successful
    cmake build.
    """
    import shutil

    cmake_exe = shutil.which("cmake")
    if not cmake_exe:
        print(
            "build_backend: cmake not found — skipping source-tree extension install.",
            file=sys.stderr,
        )
        return

    build_dir = _REPO_ROOT / "build" / f"cmake-{_wheel_tag()}"
    if not build_dir.exists():
        return

    result = subprocess.run(  # nosec B603
        [cmake_exe, "--install", str(build_dir), "--prefix", str(_REPO_ROOT)],
        check=False,
    )
    if result.returncode != 0:
        print(
            f"build_backend: cmake --install to source tree exited {result.returncode} "
            "— editable imports may fail in test venvs.",
            file=sys.stderr,
        )


def _with_source_install(fn):
    """Decorator: after the editable build hook, also install into the source tree."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        _install_extensions_to_source()
        return result

    return wrapper


# ── Delegate all PEP 517 hooks to scikit_build_core.build ────────────────────
from scikit_build_core.build import build_editable as _skb_build_editable  # noqa: E402  (import after sys.path patch)
from scikit_build_core.build import build_sdist as _skb_build_sdist  # noqa: E402  (import after sys.path patch)
from scikit_build_core.build import build_wheel as _skb_build_wheel  # noqa: E402  (import after sys.path patch)
from scikit_build_core.build import get_requires_for_build_editable  # noqa: E402  (import after sys.path patch)
from scikit_build_core.build import get_requires_for_build_sdist  # noqa: E402  (import after sys.path patch)
from scikit_build_core.build import get_requires_for_build_wheel  # noqa: E402  (import after sys.path patch)
from scikit_build_core.build import (  # noqa: E402  (import after sys.path patch)
    prepare_metadata_for_build_editable as _skb_prepare_metadata_for_build_editable,
)
from scikit_build_core.build import (  # noqa: E402  (import after sys.path patch)
    prepare_metadata_for_build_wheel as _skb_prepare_metadata_for_build_wheel,
)


# Hooks that produce named artefacts are wrapped with the serverless name patch.
# Editable hooks additionally install extensions into the source tree so that
# riot test venvs (which find ddtrace via sys.path, not an editable finder) can
# import compiled extensions.  get_requires_* hooks return dependency lists
# unrelated to the package name, so they are re-exported as-is.
build_wheel = _with_serverless_name(_skb_build_wheel)
build_editable = _with_source_install(_with_serverless_name(_skb_build_editable))
build_sdist = _with_serverless_name(_skb_build_sdist)
prepare_metadata_for_build_wheel = _with_serverless_name(_skb_prepare_metadata_for_build_wheel)
prepare_metadata_for_build_editable = _with_source_install(
    _with_serverless_name(_skb_prepare_metadata_for_build_editable)
)

__all__ = [
    "build_editable",
    "build_sdist",
    "build_wheel",
    "get_requires_for_build_editable",
    "get_requires_for_build_sdist",
    "get_requires_for_build_wheel",
    "prepare_metadata_for_build_editable",
    "prepare_metadata_for_build_wheel",
]
