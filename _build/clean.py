from distutils.command.clean import clean as CleanCommand
import os
from pathlib import Path
import shutil

from _build.constants import DDTRACE_DIR
from _build.constants import HERE
from _build.constants import NATIVE_CRATE
from _build.constants import VENDOR_DIR


class CleanLibraries(CleanCommand):
    @staticmethod
    def remove_native_extensions():
        """Remove native extensions and shared libraries installed by setup.py."""
        for pattern in ("*.so", "*.pyd", "*.dylib", "*.dll"):
            for path in DDTRACE_DIR.rglob(pattern):
                # Avoid modifying vendored directories
                if path.is_file() and not path.is_relative_to(VENDOR_DIR):
                    try:
                        path.unlink()
                    except OSError as e:
                        print(f"WARNING: could not remove {path}: {e}")

    @staticmethod
    def remove_artifacts():
        from _build.constants import LIBDDWAF_DOWNLOAD_DIR

        shutil.rmtree(LIBDDWAF_DOWNLOAD_DIR, True)
        CleanLibraries.remove_native_extensions()

    @staticmethod
    def remove_rust_targets():
        """Remove all Rust target dirs (target, target3.9, target3.10, etc.)."""
        # rmtree is a superset of `cargo clean`; target* catches plain target and versioned
        for target_dir in NATIVE_CRATE.glob("target*"):
            if target_dir.is_dir():
                shutil.rmtree(target_dir, True)

    @staticmethod
    def remove_build_artifacts():
        """Remove egg-info, dist, .eggs, *.egg, and CMake FetchContent cache.

        The base distutils clean command does not remove these. They can cause
        stale metadata and odd behavior on reinstall. Invoked only for
        ``clean --all`` to give a full reset before a fresh build.
        """
        for path in (HERE / "ddtrace.egg-info", HERE / "dist", HERE / ".eggs"):
            if path.exists():
                shutil.rmtree(path, True)
        for egg in HERE.glob("*.egg"):
            if egg.is_file():
                egg.unlink(missing_ok=True)
            elif egg.is_dir():
                shutil.rmtree(egg, True)
        # Inline LibraryDownload.CACHE_DIR to avoid circular import
        cache_dir = Path(os.getenv("DD_SETUP_CACHE_DIR", str(HERE / ".download_cache")))
        cmake_deps = cache_dir / "_cmake_deps"
        if cmake_deps.exists():
            shutil.rmtree(cmake_deps, True)

    @staticmethod
    def remove_build_dir():
        """Remove the entire build/ tree for a clean slate.

        The base CleanCommand only removes specific subdirs (build_temp, build_lib, etc.)
        per runtime. We remove build/ wholesale so all build output is cleared.
        """
        build_dir = HERE / "build"
        if build_dir.exists():
            shutil.rmtree(build_dir, True)

    def run(self):
        CleanLibraries.remove_rust_targets()
        CleanLibraries.remove_artifacts()
        CleanLibraries.remove_build_dir()
        if self.all:
            CleanLibraries.remove_build_artifacts()


def main() -> int:
    CleanLibraries.remove_rust_targets()
    CleanLibraries.remove_artifacts()
    CleanLibraries.remove_build_dir()
    CleanLibraries.remove_build_artifacts()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
