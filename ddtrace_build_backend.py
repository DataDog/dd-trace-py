from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sys
import sysconfig
from typing import Any
import zipfile

import mesonpy
import mesonpy._tags


_ROOT = Path(__file__).resolve().parent
_BUILD_SYSTEM_FILES = (
    "meson.build",
    "ddtrace/internal/native/meson.build",
    "ddtrace/internal/datadog/profiling/meson.build",
    "ddtrace/internal/datadog/profiling/ddup/meson.build",
    "ddtrace/internal/datadog/profiling/stack/meson.build",
    "ddtrace/profiling/collector/meson.build",
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
_EDITABLE_RUNTIME_REQUIRES = ("ninja",)


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


def _patch_editable_loader(loader_text: str) -> str:
    # AIDEV-NOTE: Keep the editable loader rebuild command interpreter-relative.
    # Riot can run subprocesses with a PATH that does not include the base venv's
    # script directory, so replacing meson-python's ephemeral build-env ninja
    # path with bare "ninja" breaks imports in build_base_venvs smoke tests.
    patched, count = re.subn(
        r"(?m)^(\s*)\[[^\n]*ninja[^\n]*\],$",
        r"\1[sys.executable, '-m', 'ninja'],",
        loader_text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("Could not find editable loader ninja command to patch")

    # AIDEV-NOTE: When sys.executable is not Python (e.g. in uWSGI worker
    # processes where the embedding process binary is used), the rebuild command
    # [sys.executable, '-m', 'ninja'] fails with a non-zero exit code.
    # Instead of propagating an ImportError, fall through to reading the
    # existing intro-install_plan.json in the build directory.  The plan was
    # written by the initial `pip install -e .` run and still points to the
    # pre-built artifacts, so imports succeed without any rebuild.
    patched, count2 = re.subn(
        r"(?m)^(\s+)except subprocess\.CalledProcessError as exc:\n(\s+)raise ImportError\([^)]+\) from exc",
        r"\1except subprocess.CalledProcessError:\n\2pass  # rebuild failed; use existing build artifacts",
        patched,
        count=1,
    )
    if count2 != 1:
        raise RuntimeError("Could not find editable loader CalledProcessError handler to patch")
    return patched


def _patch_editable_metadata(metadata_text: str) -> str:
    lines = metadata_text.splitlines()
    existing = {line.partition(":")[2].strip() for line in lines if line.startswith("Requires-Dist:")}
    for requirement in _EDITABLE_RUNTIME_REQUIRES:
        if requirement not in existing:
            lines.append(f"Requires-Dist: {requirement}")
    return "\n".join(lines) + "\n"


def _patch_editable_wheel(wheel_directory: str, wheel_name: str) -> str:
    wheel_path = Path(wheel_directory) / wheel_name
    patched_path = wheel_path.with_suffix(".patched.whl")

    with zipfile.ZipFile(wheel_path, "r") as src, zipfile.ZipFile(patched_path, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename.endswith("_ddtrace_editable_loader.py"):
                data = _patch_editable_loader(data.decode("utf-8")).encode("utf-8")
            elif info.filename.endswith(".dist-info/METADATA"):
                data = _patch_editable_metadata(data.decode("utf-8")).encode("utf-8")
            dst.writestr(info, data)

    patched_path.replace(wheel_path)
    return wheel_name


get_requires_for_build_sdist = mesonpy.get_requires_for_build_sdist
get_requires_for_build_wheel = mesonpy.get_requires_for_build_wheel
get_requires_for_build_editable = mesonpy.get_requires_for_build_editable


def build_sdist(sdist_directory: str, config_settings: dict[Any, Any] | None = None) -> str:
    return mesonpy.build_sdist(sdist_directory, _with_default_build_dir(config_settings))


def build_wheel(
    wheel_directory: str,
    config_settings: dict[Any, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    settings = _with_default_wheel_build_settings(config_settings)
    return mesonpy.build_wheel(
        wheel_directory,
        _with_default_build_dir(settings),
        metadata_directory,
    )


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
    return _patch_editable_wheel(wheel_directory, wheel_name)
