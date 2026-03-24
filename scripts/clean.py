#!/usr/bin/env python3
"""Clean build artifacts for dd-trace-py.

Replicates the CleanLibraries command from setup.py without importing
the heavy build-time dependencies (cython, cmake, setuptools-rust) that
setup.py requires unconditionally at the module level.

Usage:
    python scripts/clean.py        # equivalent to: python setup.py clean --all
    uv run scripts/clean.py
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DDTRACE_DIR = HERE / "ddtrace"
VENDOR_DIR = DDTRACE_DIR / "vendor"
NATIVE_CRATE = HERE / "src" / "native"
LIBDDWAF_DOWNLOAD_DIR = DDTRACE_DIR / "appsec" / "_ddwaf" / "libddwaf"
CACHE_DIR = Path(os.getenv("DD_SETUP_CACHE_DIR", str(HERE / ".download_cache")))


def remove_native_extensions() -> None:
    """Remove native extensions and shared libraries installed by setup.py."""
    for pattern in ("*.so", "*.pyd", "*.dylib", "*.dll"):
        for path in DDTRACE_DIR.rglob(pattern):
            if path.is_file() and not path.is_relative_to(VENDOR_DIR):
                try:
                    path.unlink()
                except OSError as e:
                    print(f"WARNING: could not remove {path}: {e}")


def remove_rust_targets() -> None:
    """Remove all Rust target dirs (target, target3.9, target3.10, etc.)."""
    for target_dir in NATIVE_CRATE.glob("target*"):
        if target_dir.is_dir():
            shutil.rmtree(target_dir, True)


def remove_artifacts() -> None:
    shutil.rmtree(LIBDDWAF_DOWNLOAD_DIR, True)
    remove_native_extensions()


def remove_build_artifacts() -> None:
    """Remove egg-info, dist, .eggs, *.egg, and CMake FetchContent cache."""
    for path in (HERE / "ddtrace.egg-info", HERE / "dist", HERE / ".eggs"):
        if path.exists():
            shutil.rmtree(path, True)
    for egg in HERE.glob("*.egg"):
        if egg.is_file():
            egg.unlink(missing_ok=True)
        elif egg.is_dir():
            shutil.rmtree(egg, True)
    cmake_deps = CACHE_DIR / "_cmake_deps"
    if cmake_deps.exists():
        shutil.rmtree(cmake_deps, True)


def remove_build_dir() -> None:
    """Remove the entire build/ tree for a clean slate."""
    build_dir = HERE / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir, True)


def main() -> int:
    remove_rust_targets()
    remove_artifacts()
    remove_build_dir()
    remove_build_artifacts()
    return 0


if __name__ == "__main__":
    sys.exit(main())
