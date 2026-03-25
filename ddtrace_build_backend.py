from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import sysconfig
from typing import Any

import mesonpy
import mesonpy._tags


_ROOT = Path(__file__).resolve().parent
_BUILD_SYSTEM_FILES = (
    "meson.build",
    "scripts/meson_build_ext.py",
    "ddtrace/profiling/collector/CMakeLists.txt",
    "ddtrace/internal/datadog/profiling/dd_wrapper/CMakeLists.txt",
    "ddtrace/internal/datadog/profiling/ddup/CMakeLists.txt",
    "ddtrace/internal/datadog/profiling/stack/CMakeLists.txt",
    "ddtrace_build_backend.py",
    "pyproject.toml",
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
        )
    return settings


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
    return mesonpy.build_wheel(
        wheel_directory,
        _with_default_build_dir(config_settings),
        metadata_directory,
    )


def build_editable(
    wheel_directory: str,
    config_settings: dict[Any, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    return mesonpy.build_editable(
        wheel_directory,
        _with_default_build_dir(config_settings),
        metadata_directory,
    )
