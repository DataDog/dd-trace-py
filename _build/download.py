from distutils.util import get_platform
import os
from pathlib import Path
import platform
import tarfile
import time
from urllib.request import urlretrieve

from setuptools.command.build_py import build_py as BuildPyCommand

from _build.constants import CURRENT_OS
from _build.constants import HERE
from _build.constants import LIBDDWAF_DOWNLOAD_DIR
from _build.constants import LIBDDWAF_VERSION
from _build.utils import is_64_bit_python
from _build.utils import retry_download
from _build.utils import verify_checksum_from_file
from _build.utils import verify_checksum_from_hash


# Source/build file extensions that should never appear in a binary wheel.
# These live alongside .py files in package dirs but are only needed for compiling.
_WHEEL_EXCLUDED_EXTENSIONS = frozenset(
    [
        # C/C++ source and headers (compiled into .so extensions)
        ".c",
        ".h",
        ".cpp",
        ".cc",
        ".hpp",
        # Cython source (compiled into .so extensions; .pxd kept for sdist only)
        ".pyx",
        ".pxd",
        # Build system files
        ".cmake",
        ".sh",
        # Developer tooling
        ".plantuml",
        ".supp",
    ]
)


class LibraryDownload:
    CACHE_DIR = Path(os.getenv("DD_SETUP_CACHE_DIR", HERE / ".download_cache"))
    USE_CACHE = os.getenv("DD_SETUP_CACHE_DOWNLOADS", "1").lower() in ("1", "yes", "on", "true")

    name = None
    download_dir = Path.cwd()
    version = None
    url_root = None
    available_releases = {}
    expected_checksums = None
    translate_suffix = {}

    @classmethod
    def download_artifacts(cls):
        from _build.debug import DebugMetadata

        suffixes = cls.translate_suffix[CURRENT_OS]
        download_dir = Path(cls.download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)  # No need to check if it exists

        # If the directory is nonempty, assume we're done
        if any(download_dir.iterdir()):
            return

        for arch in cls.available_releases[CURRENT_OS]:
            if CURRENT_OS == "Linux" and not get_platform().endswith(arch):
                # We cannot include the dynamic libraries for other architectures here.
                continue
            elif CURRENT_OS == "Darwin":
                # Detect build type for macos:
                # https://github.com/pypa/cibuildwheel/blob/main/cibuildwheel/macos.py#L250
                target_platform = os.getenv("PLAT")
                # Darwin Universal2 should bundle both architectures
                if target_platform and not target_platform.endswith(("universal2", arch)):
                    continue
            elif CURRENT_OS == "Windows":
                if arch == "win32" and is_64_bit_python():
                    continue  # Skip 32-bit builds on 64-bit Python
                elif arch in ["x64", "arm64"] and not is_64_bit_python():
                    continue  # Skip 64-bit builds on 32-bit Python
                elif arch == "arm64" and platform.machine().lower() not in ["arm64", "aarch64"]:
                    continue  # Skip ARM64 builds on non-ARM64 machines
                elif arch == "x64" and platform.machine().lower() not in ["amd64", "x86_64"]:
                    continue  # Skip x64 builds on non-x64 machines

            arch_dir = download_dir / arch

            # If the directory for the architecture exists and is nonempty, assume we're done
            if arch_dir.is_dir() and any(arch_dir.iterdir()):
                continue

            archive_dir = cls.get_package_name(arch, CURRENT_OS)
            archive_name = cls.get_archive_name(arch, CURRENT_OS)

            download_address = "%s/%s/%s" % (
                cls.url_root,
                cls.version,
                archive_name,
            )

            download_dest = cls.CACHE_DIR / archive_name if cls.USE_CACHE else Path(archive_name)
            if cls.USE_CACHE and not cls.CACHE_DIR.exists():
                cls.CACHE_DIR.mkdir(parents=True)

            if not (cls.USE_CACHE and download_dest.exists()):
                print(f"Downloading {archive_name} to {download_dest}")
                start_ns = time.time_ns()

                # Create retry-wrapped download function
                @retry_download()
                def download_file(url, dest):
                    """Download file with automatic retry on transient errors."""
                    return urlretrieve(url, str(dest))

                filename, _ = download_file(download_address, download_dest)

                # Verify checksum of downloaded file
                if cls.expected_checksums is None:
                    sha256_address = download_address + ".sha256"
                    sha256_dest = str(download_dest) + ".sha256"
                    sha256_filename, _ = download_file(sha256_address, sha256_dest)
                    verify_checksum_from_file(sha256_filename, str(download_dest))
                else:
                    expected_checksum = cls.expected_checksums[CURRENT_OS][arch]
                    verify_checksum_from_hash(expected_checksum, str(download_dest))

                DebugMetadata.download_times[archive_name] = time.time_ns() - start_ns

            else:
                # If the file exists in the cache, we will use it
                filename = str(download_dest)
                print(f"Using cached {filename}")

            # Open the tarfile first to get the files needed.
            # This could be solved with "r:gz" mode, that allows random access
            # but that approach does not work on Windows
            with tarfile.open(filename, "r|gz", errorlevel=2) as tar:
                dynfiles = [c for c in tar.getmembers() if c.name.endswith(suffixes)]

            with tarfile.open(filename, "r|gz", errorlevel=2) as tar:
                tar.extractall(members=dynfiles, path=HERE)
                Path(HERE / archive_dir).rename(arch_dir)

            # Rename <name>.xxx to lib<name>.xxx so the filename is the same for every OS
            lib_dir = arch_dir / "lib"
            for suffix in suffixes:
                original_file = lib_dir / "{}{}".format(cls.name, suffix)
                if original_file.exists():
                    renamed_file = lib_dir / "lib{}{}".format(cls.name, suffix)
                    original_file.rename(renamed_file)

            if not cls.USE_CACHE:
                Path(filename).unlink()

    @classmethod
    def run(cls):
        cls.download_artifacts()

    @classmethod
    def get_package_name(cls, arch, os) -> str:
        raise NotImplementedError()

    @classmethod
    def get_archive_name(cls, arch, os):
        return cls.get_package_name(arch, os) + ".tar.gz"


class LibDDWafDownload(LibraryDownload):
    name = "ddwaf"
    download_dir = LIBDDWAF_DOWNLOAD_DIR
    version = LIBDDWAF_VERSION
    url_root = "https://github.com/DataDog/libddwaf/releases/download"
    available_releases = {
        "Windows": ["arm64", "win32", "x64"],
        "Darwin": ["arm64", "x86_64"],
        "Linux": ["aarch64", "x86_64"],
    }
    translate_suffix = {"Windows": (".dll",), "Darwin": (".dylib",), "Linux": (".so",)}

    @classmethod
    def get_package_name(cls, arch, os):
        archive_dir = "lib%s-%s-%s-%s" % (cls.name, cls.version, os.lower(), arch)
        return archive_dir

    @classmethod
    def get_archive_name(cls, arch, os):
        os_name = os.lower()
        if os_name == "linux":
            archive_dir = "lib%s-%s-%s-linux-musl.tar.gz" % (cls.name, cls.version, arch)
        else:
            archive_dir = "lib%s-%s-%s-%s.tar.gz" % (cls.name, cls.version, os_name, arch)
        return archive_dir


class LibraryDownloader(BuildPyCommand):
    def run(self):
        # The setuptools docs indicate the `editable_mode` attribute of the build_py command class
        # is set to True when the package is being installed in editable mode, which we need to know
        # for some extensions
        if self.editable_mode:
            import _build.constants as _c

            _c.IS_EDITABLE = True

        from _build.clean import CleanLibraries

        CleanLibraries.remove_artifacts()
        LibDDWafDownload.run()
        BuildPyCommand.run(self)
        self._strip_build_artifacts()

    def find_data_files(self, package, src_dir):
        """Strip build/source artifacts from wheel data files."""
        files = BuildPyCommand.find_data_files(self, package, src_dir)
        return [f for f in files if os.path.splitext(f)[1].lower() not in _WHEEL_EXCLUDED_EXTENSIONS]

    def _strip_build_artifacts(self):
        """Remove source/build artifacts from the build_lib directory after copying.

        find_data_files() handles most setuptools code paths, but the PEP 517
        build backend may populate build_lib via a different route. This post-
        processing pass guarantees the artifacts are absent from the final wheel.
        """
        if not self.build_lib:
            return
        build_lib = Path(self.build_lib)
        removed = 0
        for path in build_lib.rglob("*"):
            if path.is_file() and path.suffix.lower() in _WHEEL_EXCLUDED_EXTENSIONS:
                path.unlink()
                removed += 1
        if removed:
            print(f"Stripped {removed} build artifact(s) from wheel", flush=True)
