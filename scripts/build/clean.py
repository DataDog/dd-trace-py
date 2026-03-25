#!/usr/bin/env python3
"""Clean build artifacts from the ddtrace source tree.

Replaces ``python setup.py clean --all``.  Removes:

  - Compiled native extensions (.so / .pyd / .dylib / .dll) from the source tree
  - The ``build/`` tree (CMake binary trees, build-env, Ninja cache)
  - Rust target directories under ``src/native/``
  - Staged libddwaf shared libraries (``ddtrace/appsec/_ddwaf/libddwaf/``)
  - Stale egg-info, dist, .eggs, and *.egg artefacts

Usage
-----
    python scripts/build/clean.py
"""

from __future__ import annotations

from pathlib import Path
import shutil


HERE = Path(__file__).resolve().parent.parent
_DDWAF_DIR = HERE / "ddtrace" / "appsec" / "_ddwaf" / "libddwaf"
_NATIVE_CRATE = HERE / "src" / "native"


def _remove(path: Path) -> None:
    if path.is_file() or path.is_symlink():
        path.unlink()
        print(f"  removed {path.relative_to(HERE)}")
    elif path.is_dir():
        shutil.rmtree(path)
        print(f"  removed {path.relative_to(HERE)}/")


def remove_native_extensions() -> None:
    """Remove compiled extension files from the source tree."""
    print("Removing native extensions...")
    for pattern in ("**/*.so", "**/*.pyd", "**/*.dylib", "**/*.dll"):
        for path in HERE.glob(pattern):
            # Skip files inside build/ and src/native/target* (handled separately)
            parts = path.relative_to(HERE).parts
            if parts[0] in ("build",) or (parts[:2] == ("src", "native") and parts[2].startswith("target")):
                continue
            _remove(path)


def remove_build_dir() -> None:
    """Remove the entire build/ tree (CMake binary trees + build-envs)."""
    print("Removing build/...")
    _remove(HERE / "build")


def remove_rust_targets() -> None:
    """Remove Cargo target directories under src/native/."""
    print("Removing Rust target directories...")
    for target_dir in _NATIVE_CRATE.glob("target*"):
        if target_dir.is_dir():
            _remove(target_dir)


def remove_staged_libddwaf() -> None:
    """Remove staged libddwaf shared libraries."""
    print("Removing staged libddwaf...")
    _remove(_DDWAF_DIR)


def remove_dist_artifacts() -> None:
    """Remove egg-info, dist, .eggs, and *.egg artefacts."""
    print("Removing dist artefacts...")
    for path in (HERE / "ddtrace.egg-info", HERE / "dist", HERE / ".eggs"):
        _remove(path)
    for egg in HERE.glob("*.egg"):
        _remove(egg)


def main() -> None:
    remove_native_extensions()
    remove_build_dir()
    remove_rust_targets()
    remove_staged_libddwaf()
    remove_dist_artifacts()
    print("Done.")


if __name__ == "__main__":
    main()
