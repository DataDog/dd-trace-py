from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import sysconfig
from typing import Any
import zipfile

import mesonpy
import mesonpy._tags


_ROOT = Path(__file__).resolve().parent
_BUILD_SYSTEM_FILES = (
    "meson.build",
    "scripts/meson_build_ext.py",
    "scripts/meson_install_iast_native.py",
    "ddtrace/profiling/collector/CMakeLists.txt",
    "ddtrace/appsec/_iast/_taint_tracking/CMakeLists.txt",
    "ddtrace/internal/datadog/profiling/cmake/FindLibNative.cmake",
    "ddtrace/internal/datadog/profiling/dd_wrapper/CMakeLists.txt",
    "ddtrace/internal/datadog/profiling/ddup/CMakeLists.txt",
    "ddtrace/internal/datadog/profiling/stack/CMakeLists.txt",
    "ddtrace_build_backend.py",
    "pyproject.toml",
)

_WHEEL_DEBUG_SETUP_ARGS = (
    "-Dbuildtype=custom",
    "-Ddebug=true",
    "-Doptimization=3",
    "-Db_ndebug=true",
)


def _build_system_cache_key() -> str:
    digest = hashlib.sha256()
    for relpath in _BUILD_SYSTEM_FILES:
        path = _ROOT / relpath
        digest.update(relpath.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def _python_build_cache_key() -> str:
    digest = hashlib.sha256()
    for value in (
        sys.version,
        str(Path(sys.executable).resolve()),
        sys.implementation.cache_tag or "",
        sysconfig.get_config_var("SOABI") or "",
        sysconfig.get_config_var("EXT_SUFFIX") or "",
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def _config_value_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _config_settings_cache_key(config_settings: dict[Any, Any] | None) -> str:
    digest = hashlib.sha256()
    for key in sorted((config_settings or ()), key=str):
        if key in ("build-dir", "builddir"):
            continue

        digest.update(str(key).encode("utf-8"))
        digest.update(b"\0")
        for value in _config_value_list(config_settings[key]):
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
    return digest.hexdigest()[:12]


def _has_explicit_wheel_build_setup(config_settings: dict[Any, Any]) -> bool:
    for arg in _config_value_list(config_settings.get("setup-args")):
        if any(
            arg == f"-D{name}" or arg.startswith(f"-D{name}=")
            for name in ("buildtype", "debug", "optimization", "b_ndebug")
        ):
            return True
    return False


def _with_default_wheel_build_settings(config_settings: dict[Any, Any] | None) -> dict[Any, Any]:
    settings = dict(config_settings or {})

    # AIDEV-NOTE: Linux and macOS wheel packaging split debug symbols into a
    # separate artifact in CI. setup.py used Python's default compiler flags,
    # which typically included -g alongside optimization, so meson wheel
    # builds need an equivalent default without changing editable/dev builds.
    # setup.py historically built non-Windows wheels with release-like optimization
    # plus debug info so CI could split symbols out into a separate artifact.
    if sys.platform == "win32" or _has_explicit_wheel_build_setup(settings):
        return settings

    setup_args = _config_value_list(settings.get("setup-args"))
    setup_args.extend(_WHEEL_DEBUG_SETUP_ARGS)
    settings["setup-args"] = setup_args
    return settings


def _with_default_build_dir(config_settings: dict[Any, Any] | None) -> dict[Any, Any]:
    settings = dict(config_settings or {})
    if "build-dir" not in settings and "builddir" not in settings:
        settings["build-dir"] = (
            "build/mesonpy-"
            + mesonpy._tags.get_abi_tag()
            + "-"
            + _python_build_cache_key()
            + "-"
            + _build_system_cache_key()
            + "-"
            + _config_settings_cache_key(settings)
        )
    # AIDEV-NOTE: On Windows ARM64, GitHub Actions runners have GCC/MinGW
    # earlier in PATH than MSVC, so meson defaults to GCC.  GNU ld cannot link
    # against MSVC-compiled Python DLLs (e.g. python311.dll), producing
    # "file format not recognized" errors.  Passing --vsenv tells meson to
    # activate the Visual Studio environment before running the build, ensuring
    # MSVC is used — matching the behaviour of the old setup.py build.
    if sys.platform == "win32":
        setup_args = _config_value_list(settings.get("setup-args"))
        if "--vsenv" not in setup_args:
            setup_args.append("--vsenv")
        settings["setup-args"] = setup_args
    return settings


def _copy_install_plan_to_source(build_dir: str) -> int:
    """Copy all meson build artifacts into the source tree.

    AIDEV-NOTE: With a simple path-based editable install, Python finds ddtrace
    directly from the source tree via sys.path — not via MesonpyMetaFinder and
    the build directory.  Compiled extensions and other generated files must live
    alongside the Python source in ddtrace/ so the normal import machinery finds
    them without needing the meson-python editable loader at all.

    Files destined for directories starting with '.' (e.g. {py_platlib}/.meson-native/)
    are intentional meson staging areas used by install scripts; they are skipped here
    and handled separately (see _install_iast_native).
    """
    from pathlib import PurePosixPath

    plan_path = Path(build_dir) / "meson-info" / "intro-install_plan.json"
    if not plan_path.exists():
        return 0

    with open(plan_path, encoding="utf-8") as fh:
        plan = json.load(fh)

    copied = 0
    for _section, data in plan.items():
        for src, target_info in data.items():
            dest = target_info.get("destination", "")
            parts = PurePosixPath(dest).parts
            if not parts or parts[0] not in ("{py_platlib}", "{py_purelib}"):
                continue
            if len(parts) < 2:
                continue
            # Skip internal meson staging directories (e.g. .meson-native/).
            # These are handled by install scripts; we process them separately.
            if parts[1].startswith("."):
                continue
            src_path = Path(src)
            if not src_path.exists() or src_path.is_dir():
                continue
            dst = _ROOT / os.path.join(*parts[1:])
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_path), str(dst))
            copied += 1
    return copied


def _install_iast_native(build_dir: str) -> None:
    """Perform the IAST native install-script transformation.

    AIDEV-NOTE: The IAST native C++ extension is built as native_iast_raw{ext}
    in the meson build directory (meson custom_target install_dir points to
    {py_platlib}/.meson-native/ as a staging location).  During a regular
    'meson install' the install script scripts/meson_install_iast_native.py
    renames it to _native{ext} under ddtrace/appsec/_iast/_taint_tracking/.
    Install scripts do not run during an editable build, so we replicate that
    rename here so the extension is importable from the source tree.
    """
    import importlib.machinery

    bd = Path(build_dir)
    for ext in importlib.machinery.EXTENSION_SUFFIXES:
        raw = bd / f"native_iast_raw{ext}"
        if raw.exists():
            dst = _ROOT / "ddtrace" / "appsec" / "_iast" / "_taint_tracking" / f"_native{ext}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(raw), str(dst))
            break


def _make_simple_editable_wheel(wheel_directory: str, wheel_name: str) -> str:
    """Replace meson-python's editable install with a simple path-based install.

    AIDEV-NOTE: meson-python's editable loader (MesonpyMetaFinder) runs ninja in
    every subprocess that imports ddtrace to check for rebuilt artifacts.  Even a
    'no work to do' ninja run starts a full Python process, adding ~1-3 seconds of
    overhead to every ddtrace import in a subprocess.  Tests that spawn subprocesses
    (e.g. ddtrace-run with a 5-second timeout, pygoat's Django server) fail because
    of this latency.

    Instead of patching the loader with fragile regexes we replace it entirely:
    1. Copy all meson build artifacts (extensions, generated files) to the source tree.
    2. Replace the loader-activating .pth with a plain source-path .pth so Python
       finds ddtrace from the source tree via normal import machinery — no loader,
       no ninja, no per-subprocess overhead.

    This matches the behaviour of the old cmake/setuptools in-place build.  After
    editing Cython/C source, re-run `pip install -e .` to rebuild (same workflow as
    cmake).  Set MESONPY_EDITABLE_VERBOSE=1 to use the original meson-python loader
    with auto-rebuild instead (install will skip this transformation).
    """
    if os.environ.get("MESONPY_EDITABLE_VERBOSE"):
        # Developer requested the full meson-python loader with auto-rebuild.
        return wheel_name

    wheel_path = Path(wheel_directory) / wheel_name
    patched_path = wheel_path.with_suffix(".simple.whl")

    # Extract the build directory path from the loader so we can read the install plan.
    # Also detect the dist-info prefix and whether top_level.txt is already present.
    build_dir: str | None = None
    dist_info_prefix: str | None = None
    has_top_level_txt = False
    with zipfile.ZipFile(wheel_path, "r") as zf:
        for info in zf.infolist():
            if info.filename.endswith("_ddtrace_editable_loader.py"):
                loader_text = zf.read(info.filename).decode("utf-8")
                m = re.search(
                    r"install\(\s*\n?\s*'[^']*'\s*,\s*\n?\s*\{[^}]*\}\s*,\s*\n?\s*'([^']+)'",
                    loader_text,
                )
                if m:
                    build_dir = m.group(1)
            elif ".dist-info/" in info.filename and dist_info_prefix is None:
                dist_info_prefix = info.filename.split(".dist-info/")[0] + ".dist-info/"
            if info.filename.endswith("/top_level.txt") or info.filename == "top_level.txt":
                has_top_level_txt = True

    if build_dir:
        _copy_install_plan_to_source(build_dir)
        # AIDEV-NOTE: _install_iast_native is intentionally not called here.
        # cmake's LIBRARY_OUTPUT_DIRECTORY in CMakeLists.txt already places
        # _native{ext} directly into ddtrace/appsec/_iast/_taint_tracking/ in
        # the source tree during the cmake build step (before install).  Calling
        # _install_iast_native would overwrite that correct binary with
        # native_iast_raw{ext} from the meson build root — which may contain the
        # wrong binary due to the glob fallback in meson_build_ext.py cmd_cmake.

    top_level_content = b"ddtrace\n"
    top_level_name = (dist_info_prefix + "top_level.txt") if (dist_info_prefix and not has_top_level_txt) else None
    if top_level_name:
        top_level_hash = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(top_level_content).digest()).rstrip(b"=").decode("ascii")
        top_level_record_line = f"{top_level_name},{top_level_hash},{len(top_level_content)}\n"
    record_name = (dist_info_prefix + "RECORD") if dist_info_prefix else None

    with zipfile.ZipFile(wheel_path, "r") as src_zip, zipfile.ZipFile(patched_path, "w") as dst_zip:
        for info in src_zip.infolist():
            filename = info.filename
            data = src_zip.read(filename)

            if filename.endswith("_ddtrace_editable_loader.py"):
                # Replace with a stub so the RECORD manifest stays intact but
                # the loader installs no meta-path finder and runs no ninja.
                data = b"# meson-python editable loader disabled: using simple path-based install\n"
            elif filename.endswith(".pth") and b"_ddtrace_editable_loader" in data:
                # AIDEV-NOTE: Replace the loader-activating .pth with a plain
                # source-directory path.  Python's site module adds every line in
                # a .pth file that is an existing directory to sys.path, so this
                # single line makes the whole source tree importable — identical
                # to setuptools' legacy editable install mode.
                data = (str(_ROOT) + "\n").encode("utf-8")
            elif top_level_name and filename == record_name:
                # Append the top_level.txt entry to RECORD before writing it.
                data = data + top_level_record_line.encode("utf-8")

            dst_zip.writestr(info, data)

        # AIDEV-NOTE: Inject a .pth named with a leading '0' (ASCII 48) so it
        # sorts before _riot_site_packages_*.pth (ASCII 95) during site-packages
        # initialisation.  Python processes .pth files in sorted order; when a
        # test subprocess starts it inherits COV_CORE_SOURCE from the outer pytest
        # process.  riot's activate() calls site.addsitedir(..., known_paths=set())
        # which re-executes pytest-cov.pth and calls init() again.  init() tries to
        # claim sys.monitoring tool ID 1 (COVERAGE_ID); on the second call it raises
        # ValueError('tool 1 is already in use') which leaks into stderr and breaks
        # tests asserting err == b"".  Stripping COV_CORE_* / COVERAGE_PROCESS_START
        # before activate() runs makes init() a no-op, eliminating the noise.
        # AIDEV-NOTE: Uses __import__('os') not 'import os as _os' because Python
        # 3.9-3.11 exec()s .pth lines without closure cells, so list comprehensions
        # cannot access outer exec-local names via LOAD_DEREF.  __import__ is a
        # builtin (LOAD_GLOBAL) and always accessible.
        _cov_strip_code = (
            "import sys; "
            "[__import__('os').environ.pop(k) for k in"
            " [k for k in list(__import__('os').environ)"
            " if k.startswith('COV_CORE') or k == 'COVERAGE_PROCESS_START']]\n"
        )
        dst_zip.writestr("0_ddtrace_strip_cov.pth", _cov_strip_code.encode("utf-8"))

        # AIDEV-NOTE: Inject top_level.txt into the dist-info so that
        # importlib.metadata.packages_distributions() maps 'ddtrace' to
        # the installed distribution.  Without this, the meson-python editable
        # RECORD has no ddtrace/ entries and MesonpyMetaFinder (which we've
        # removed) was the only thing that made 'ddtrace' appear in the
        # packages mapping.  When 'ddtrace' is absent, iastpatch.is_first_party()
        # classifies it as local/first-party code and patches it — breaking the
        # IAST tests that expect DENIED_NOT_FOUND for ddtrace.*.
        if top_level_name:
            dst_zip.writestr(top_level_name, top_level_content)

    patched_path.replace(wheel_path)
    return wheel_name


get_requires_for_build_sdist = mesonpy.get_requires_for_build_sdist
get_requires_for_build_wheel = mesonpy.get_requires_for_build_wheel
get_requires_for_build_editable = mesonpy.get_requires_for_build_editable


def build_sdist(sdist_directory: str, config_settings: dict[Any, Any] | None = None) -> str:
    return mesonpy.build_sdist(sdist_directory, _with_default_build_dir(config_settings))


def _inject_top_level_txt(wheel_directory: str, wheel_name: str) -> str:
    """Inject top_level.txt into a wheel's dist-info if not already present.

    AIDEV-NOTE: mesonpy.build_wheel delegates directly to meson-python and produces
    a wheel whose dist-info has no top_level.txt.  Without top_level.txt,
    importlib.metadata.packages_distributions() does not list 'ddtrace', so
    IAST's is_first_party() classifies ddtrace modules as user code and patches
    aspects.py — causing infinite recursion in modulo_aspect.  This post-processing
    step adds the missing top_level.txt so the installed wheel behaves the same as
    one built with setuptools (which always writes top_level.txt).
    """
    wheel_path = Path(wheel_directory) / wheel_name
    patched_path = wheel_path.with_suffix(".patched.whl")

    dist_info_prefix: str | None = None
    has_top_level_txt = False
    with zipfile.ZipFile(wheel_path, "r") as zf:
        for info in zf.infolist():
            if ".dist-info/" in info.filename and dist_info_prefix is None:
                dist_info_prefix = info.filename.split(".dist-info/")[0] + ".dist-info/"
            if info.filename.endswith("/top_level.txt") or info.filename == "top_level.txt":
                has_top_level_txt = True

    if has_top_level_txt or dist_info_prefix is None:
        return wheel_name

    top_level_content = b"ddtrace\n"
    top_level_name = dist_info_prefix + "top_level.txt"
    top_level_hash = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(top_level_content).digest()).rstrip(b"=").decode("ascii")
    top_level_record_line = f"{top_level_name},{top_level_hash},{len(top_level_content)}\n"

    record_name = dist_info_prefix + "RECORD"

    with zipfile.ZipFile(wheel_path, "r") as src_zip, zipfile.ZipFile(patched_path, "w") as dst_zip:
        for info in src_zip.infolist():
            data = src_zip.read(info.filename)
            if info.filename == record_name:
                # Append the top_level.txt entry before writing RECORD
                data = data + top_level_record_line.encode("utf-8")
            dst_zip.writestr(info, data)
        dst_zip.writestr(top_level_name, top_level_content)

    patched_path.replace(wheel_path)
    return wheel_name


def build_wheel(
    wheel_directory: str,
    config_settings: dict[Any, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    settings = _with_default_wheel_build_settings(config_settings)
    wheel_name = mesonpy.build_wheel(
        wheel_directory,
        _with_default_build_dir(settings),
        metadata_directory,
    )
    return _inject_top_level_txt(wheel_directory, wheel_name)


def build_editable(
    wheel_directory: str,
    config_settings: dict[Any, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    wheel_name = mesonpy.build_editable(
        wheel_directory,
        _with_default_build_dir(config_settings),
        metadata_directory,
    )
    return _make_simple_editable_wheel(wheel_directory, wheel_name)
