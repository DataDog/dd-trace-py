import atexit
import contextlib
from dataclasses import dataclass
import hashlib
from itertools import chain
import os
import platform
import random
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import time
import typing as t
import warnings

from setuptools_rust import Binding
from setuptools_rust import RustExtension
from setuptools_rust import build_rust

import cmake


from setuptools import Distribution, Extension, find_packages, setup  # isort: skip
from setuptools.command.build_ext import build_ext  # isort: skip
from setuptools.command.build_py import build_py as BuildPyCommand  # isort: skip
from pathlib import Path  # isort: skip
from distutils.command.clean import clean as CleanCommand  # isort: skip
from distutils.dep_util import newer_group  # isort: skip
from distutils.util import get_platform  # isort: skip


try:
    # ORDER MATTERS
    # Import this after setuptools or it will fail
    from Cython.Build import cythonize
    import Cython.Distutils
except ImportError:
    raise ImportError(
        "Failed to import Cython modules. This can happen under versions of pip older than 18 that don't "
        "support installing build requirements during setup. If you're using pip, make sure it's a "
        "version >=18.\nSee the quickstart documentation for more information:\n"
        "https://ddtrace.readthedocs.io/en/stable/installation_quickstart.html"
    )

from functools import wraps
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import urlretrieve


HERE = Path(__file__).resolve().parent

CURRENT_OS = platform.system()

# What's meant by each build mode is similar to that from CMake, except that
# non-CMake extensions are by default built with debug symbols. And we build
# with Release by default for Windows.
# Released wheels on Linux and macOS are stripped of debug symbols. We use
# scripts/extract_debug_symbols.py to extract the debug symbols from the wheels.
# C/C++ and Cython extensions built with setuptools.Extension, and
# Cython.Distutils.Extension by default inherits CFLAGS from the Python
# interpreter, and it usually has -O3 -g. So they're built with debug symbols
# by default.
# RustExtension src/native has two build profiles, release and debug, and only
# DD_COMPILE_MODE=Debug will build with debug profile, and rest will build with
# release profile, which also has debug symbols by default.
# And when MinSizeRel or Release is used, we strip the debug symbols from the
# wheels, see try_strip_symbols() below.
COMPILE_MODE = "Release" if CURRENT_OS == "Windows" else "RelWithDebInfo"
if "DD_COMPILE_DEBUG" in os.environ:
    warnings.warn(
        "The DD_COMPILE_DEBUG environment variable is deprecated and will be deleted, "
        "use DD_COMPILE_MODE=Debug|Release|RelWithDebInfo|MinSizeRel.",
    )
    COMPILE_MODE = "Debug"
else:
    COMPILE_MODE = os.environ.get("DD_COMPILE_MODE", COMPILE_MODE)

FAST_BUILD = os.getenv("DD_FAST_BUILD", "false").lower() in ("1", "yes", "on", "true")
if FAST_BUILD:
    print("WARNING: DD_FAST_BUILD is enabled, some optimizations will be disabled")
else:
    print("INFO: DD_FAST_BUILD not enabled")

if FAST_BUILD:
    os.environ["DD_COMPILE_ABSEIL"] = "0"
    # Trade binary size for compilation speed in dev environments by disabling
    # LTO and increasing codegen parallelism. Never used for release wheels.
    os.environ.setdefault("CARGO_PROFILE_RELEASE_LTO", "off")
    os.environ.setdefault("CARGO_PROFILE_RELEASE_CODEGEN_UNITS", "16")
    os.environ.setdefault("CARGO_PROFILE_RELEASE_OPT_LEVEL", "2")

SCCACHE_COMPILE = os.getenv("DD_USE_SCCACHE", "0").lower() in ("1", "yes", "on", "true")

# Default CMAKE_BUILD_PARALLEL_LEVEL to the number of CPUs so that cmake
# builds use all available cores instead of a single thread.
# process_cpu_count (3.13+) respects cgroup limits in containers;
# fall back to cpu_count on older Pythons.
_cpu_count = getattr(os, "process_cpu_count", os.cpu_count)() or 1
if "CMAKE_BUILD_PARALLEL_LEVEL" not in os.environ:
    os.environ["CMAKE_BUILD_PARALLEL_LEVEL"] = str(_cpu_count)

# Retry configuration for downloads (handles GitHub API failures like 503, 429)
DOWNLOAD_MAX_RETRIES = int(os.getenv("DD_DOWNLOAD_MAX_RETRIES", "10"))
DOWNLOAD_INITIAL_DELAY = float(os.getenv("DD_DOWNLOAD_INITIAL_DELAY", "1.0"))
DOWNLOAD_MAX_DELAY = float(os.getenv("DD_DOWNLOAD_MAX_DELAY", "120"))

IS_PYSTON = hasattr(sys, "pyston_version_info")
IS_EDITABLE = False  # Set to True if the package is being installed in editable mode

NATIVE_CRATE = HERE / "src" / "native"
DDTRACE_DIR = HERE / "ddtrace"
LIBDDWAF_DOWNLOAD_DIR = DDTRACE_DIR / "appsec" / "_ddwaf" / "libddwaf"
IAST_DIR = DDTRACE_DIR / "appsec" / "_iast" / "_taint_tracking"
DDUP_DIR = DDTRACE_DIR / "internal" / "datadog" / "profiling" / "ddup"
STACK_DIR = DDTRACE_DIR / "internal" / "datadog" / "profiling" / "stack"
VENDOR_DIR = DDTRACE_DIR / "vendor"
CARGO_TARGET_DIR = NATIVE_CRATE.absolute() / f"target{sys.version_info.major}.{sys.version_info.minor}"
DD_CARGO_ARGS = shlex.split(os.getenv("DD_CARGO_ARGS", ""))

BUILD_PROFILING_NATIVE_TESTS = os.getenv("DD_PROFILING_NATIVE_TESTS", "0").lower() in ("1", "yes", "on", "true")

CURRENT_OS = platform.system()
SERVERLESS_BUILD = os.getenv("DD_SERVERLESS_BUILD", "0").lower() in ("1", "yes", "on", "true")
WHEEL_FLAVOR = "-serverless" if SERVERLESS_BUILD else ""

LIBDDWAF_VERSION = "1.30.1"

# DEV: update this accordingly when src/native upgrades libdatadog dependency.
# libdatadog v15.0.0 requires rust 1.78.
RUST_MINIMUM_VERSION = "1.78"


def interpose_sccache():
    """
    Injects sccache into the relevant build commands if it's allowed and we think it'll work
    """
    if not SCCACHE_COMPILE:
        return

    # Check for sccache.  We don't do multi-step failover (e.g., if the path is set, but the binary is invalid)
    # Honor both SCCACHE_PATH and SCCACHE env vars for compatibility with docs
    _sccache_path = os.getenv("SCCACHE_PATH") or os.getenv("SCCACHE") or shutil.which("sccache")
    if _sccache_path is None:
        print("WARNING: sccache not found in SCCACHE_PATH, SCCACHE, or PATH, skipping sccache interposition")
        return
    sccache_path = Path(_sccache_path)
    if sccache_path.is_file() and os.access(sccache_path, os.X_OK):
        # Both the cmake and rust toolchains allow the caller to interpose sccache into the compiler commands, but this
        # misses calls from native extension builds.  So we do the normal Rust thing, but modify CC and CXX to point to
        # a wrapper
        os.environ["DD_SCCACHE_PATH"] = str(sccache_path.resolve())
        os.environ["RUSTC_WRAPPER"] = str(sccache_path.resolve())
        cc_path = next(
            (shutil.which(cmd) for cmd in [os.getenv("CC", ""), "cc", "gcc", "clang"] if shutil.which(cmd)), None
        )
        if cc_path:
            os.environ["DD_CC_OLD"] = cc_path
            os.environ["CC"] = str(sccache_path) + " " + str(cc_path)

        cxx_path = next(
            (shutil.which(cmd) for cmd in [os.getenv("CXX", ""), "c++", "g++", "clang++"] if shutil.which(cmd)), None
        )
        if cxx_path:
            os.environ["DD_CXX_OLD"] = cxx_path
            os.environ["CXX"] = str(sccache_path) + " " + str(cxx_path)


def retry_download(
    max_attempts=DOWNLOAD_MAX_RETRIES,
    initial_delay=DOWNLOAD_INITIAL_DELAY,
    max_delay=DOWNLOAD_MAX_DELAY,
    backoff_factor=1.618,
):
    """
    Decorator to retry downloads with exponential backoff.
    Handles HTTP 503, 429, network errors from GitHub API, and cargo install failures.
    Retriable errors: HTTP 429 (rate limit), 502, 503, 504, network timeouts, and subprocess errors.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except (HTTPError, URLError, TimeoutError, OSError, subprocess.CalledProcessError) as e:
                    # Check if it's a retriable error
                    is_retriable = False
                    if isinstance(e, HTTPError):
                        # Retry on 429 (rate limit), 502/503/504 (server errors)
                        is_retriable = e.code in (429, 502, 503, 504)
                        error_code = f"HTTP {e.code}"
                    elif isinstance(e, (URLError, TimeoutError)):
                        # Retry on network errors and timeouts
                        is_retriable = True
                        error_code = type(e).__name__
                    elif isinstance(e, OSError):
                        # Retry on connection errors
                        is_retriable = True
                        error_code = type(e).__name__
                    elif isinstance(e, subprocess.CalledProcessError):
                        # Retry on subprocess errors (e.g., cargo install network failures)
                        # These often indicate temporary network issues
                        is_retriable = True
                        error_code = f"subprocess exit code {e.returncode}"
                    else:
                        error_code = type(e).__name__

                    if not is_retriable:
                        print(f"ERROR: Operation failed (non-retriable {error_code}): {e}")
                        raise

                    if attempt == max_attempts - 1:
                        print(f"ERROR: Operation failed after {max_attempts} attempts (last error: {error_code})")
                        raise

                    # Calculate delay with jitter
                    delay = min(initial_delay * (backoff_factor**attempt), max_delay)
                    jitter = random.uniform(0, delay * 0.1)
                    total_delay = delay + jitter

                    print(f"WARNING: Operation failed (attempt {attempt + 1}/{max_attempts}): {error_code} - {e}")
                    print(f"  Retrying in {total_delay:.1f} seconds...")
                    time.sleep(total_delay)

            return func(*args, **kwargs)

        return wrapper

    return decorator


def verify_checksum_from_file(sha256_filename, filename):
    # sha256 File format is ``checksum`` followed by two whitespaces, then ``filename`` then ``\n``
    expected_checksum, expected_filename = list(filter(None, open(sha256_filename, "r").read().strip().split(" ")))
    actual_checksum = hashlib.sha256(open(filename, "rb").read()).hexdigest()
    try:
        assert expected_filename.endswith(Path(filename).name)
        assert expected_checksum == actual_checksum
    except AssertionError:
        print("Checksum verification error: Checksum and/or filename don't match:")
        print("expected checksum: %s" % expected_checksum)
        print("actual checksum: %s" % actual_checksum)
        print("expected filename: %s" % expected_filename)
        print("actual filename: %s" % filename)
        sys.exit(1)


def verify_checksum_from_hash(expected_checksum, filename):
    # sha256 File format is ``checksum`` followed by two whitespaces, then ``filename`` then ``\n``
    actual_checksum = hashlib.sha256(open(filename, "rb").read()).hexdigest()
    try:
        assert expected_checksum == actual_checksum
    except AssertionError:
        print("Checksum verification error: Checksum mismatch:")
        print("expected checksum: %s" % expected_checksum)
        print("actual checksum: %s" % actual_checksum)
        sys.exit(1)


def load_module_from_project_file(mod_name, fname):
    """
    Helper used to load a module from a file in this project

    DEV: Loading this way will by-pass loading all parent modules
         e.g. importing `ddtrace.vendor.psutil.setup` will load `ddtrace/__init__.py`
         which has side effects like loading the tracer
    """
    fpath = HERE / fname

    import importlib.util

    spec = importlib.util.spec_from_file_location(mod_name, fpath)
    if spec is None:
        raise ImportError(f"Could not find module {mod_name} in {fpath}")
    mod = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(f"Could not load module {mod_name} from {fpath}")
    spec.loader.exec_module(mod)
    return mod


def is_64_bit_python():
    return sys.maxsize > (1 << 32)


rust_features = ["stats"]
if CURRENT_OS in ("Linux", "Darwin") and is_64_bit_python():
    rust_features.append("profiling")
    if not SERVERLESS_BUILD:
        rust_features.append("crashtracker")
if not SERVERLESS_BUILD:
    rust_features.append("ffe")


class PatchedDistribution(Distribution):
    def __init__(self, attrs=None):
        super().__init__(attrs)
        # Tell ext_hashes about your manually-built Rust artifact

        rust_env = os.environ.copy()
        rust_env["CARGO_TARGET_DIR"] = str(CARGO_TARGET_DIR)
        self.rust_extensions = [
            RustExtension(
                # The Python import path of your extension:
                "ddtrace.internal.native._native",
                # Path to your Cargo.toml so setuptools-rust can infer names
                path=str(Path(__file__).parent / "src" / "native" / "Cargo.toml"),
                py_limited_api="auto",
                binding=Binding.PyO3,
                debug=COMPILE_MODE.lower() == "debug",
                features=rust_features,
                env=rust_env,
                args=DD_CARGO_ARGS,
            )
        ]


class ExtensionHashes(build_ext):
    def run(self):
        try:
            dist = self.distribution
            for ext in chain(dist.ext_modules, getattr(dist, "rust_extensions", [])):
                if isinstance(ext, CMakeExtension):
                    sources = ext.get_sources()
                elif isinstance(ext, RustExtension):
                    source_path = Path(ext.path).parent
                    sources = [
                        _
                        for _ in source_path.glob("**/*")
                        if _.is_file()
                        and _.relative_to(source_path).parts[0]
                        != f"target{sys.version_info.major}.{sys.version_info.minor}"
                    ]
                else:
                    # Hash the explicit .pyx sources plus all .pxd files found
                    # under ddtrace/.  .pxd files act like C headers — a change
                    # in any of them can affect compiled output for any Cython
                    # extension that imports it.
                    sources = [Path(_) for _ in ext.sources]
                    sources.extend(p for p in (HERE / "ddtrace").glob("**/*.pxd") if p.is_file())

                sources_hash = hashlib.sha256()
                # DEV: Make sure to include the rust features since changing them changes what gets built
                for feature in rust_features:
                    sources_hash.update(feature.encode())
                for source in sorted(sources):
                    sources_hash.update(source.read_bytes())
                hash_digest = sources_hash.hexdigest()

                entries: list[tuple[str, str, str]] = []
                entries.append((ext.name, hash_digest, str(Path(self.get_ext_fullpath(ext.name)))))

                # For profiling, these headers are generated by the Rust extension
                # and they are used to build dd_wrapper shared library. We need
                # to persist in the extension hash cache so that we can handle
                # the case where Rust extension is not rebuilt but the dd_wrapper
                # needs to be rebuilt when its sources changed.
                if isinstance(ext, RustExtension) and "profiling" in ext.features:
                    for f in ["common.h", "profiling.h"]:
                        entries.append(
                            (
                                ext.name,
                                hash_digest,
                                str(CARGO_TARGET_DIR / "include" / "datadog" / f),
                            )
                        )

                # Include any dependencies that might have been built alongside
                # the extension.
                if isinstance(ext, CMakeExtension):
                    entries.extend(
                        (f"{ext.name}-{dependency.name}", hash_digest, str(dependency) + "*")
                        for dependency in ext.dependencies
                    )

                for entry in entries:
                    print("#EXTHASH:", entry)

            # Emit shared dependency metadata so ext_cache.py can cache and
            # restore install trees without any per-dependency special-casing.
            for dep in SHARED_DEPS:
                print("#SHAREDEPINFO:", (dep.name, dep.config_hash(), str(dep.install_dir)))

        except Exception as e:
            print("WARNING: Failed to compute extension hashes: %s" % e)
            raise e


class CustomBuildRust(build_rust):
    """Custom build_rust command that handles dedup_headers and header copying."""

    def initialize_options(self):
        super().initialize_options()

    def is_installed(self, bin_file):
        """Check if a binary is installed in PATH."""
        for path in os.environ.get("PATH", "").split(os.pathsep):
            if os.path.isfile(os.path.join(path, bin_file)):
                return True
        return False

    def install_dedup_headers(self):
        """Install dedup_headers if not already installed."""
        if not self.is_installed("dedup_headers"):
            # Create retry-wrapped cargo install function
            @retry_download(max_attempts=DOWNLOAD_MAX_RETRIES, initial_delay=2.0)
            def cargo_install_with_retry():
                """Run cargo install with retry on network failures."""
                subprocess.run(
                    [
                        "cargo",
                        "install",
                        "--git",
                        "https://github.com/DataDog/libdatadog",
                        "--bin",
                        "dedup_headers",
                        "tools",
                    ],
                    check=True,
                )

            cargo_install_with_retry()

    def run(self):
        """Run the build process with additional post-processing."""

        has_profiling_feature = False
        for ext in self.distribution.rust_extensions:
            if ext.features and "profiling" in ext.features:
                has_profiling_feature = True
                break

        if IS_EDITABLE or getattr(self, "inplace", False):
            self.inplace = True

        super().run()

        # Check if profiling is enabled and run dedup_headers
        if has_profiling_feature:
            self.install_dedup_headers()

            # Add cargo binary folder to PATH
            home = os.path.expanduser("~")
            cargo_bin = os.path.join(home, ".cargo", "bin")
            dedup_env = os.environ.copy()
            dedup_env["PATH"] = cargo_bin + os.pathsep + os.environ["PATH"]

            # Run dedup_headers on the generated headers
            include_dir = CARGO_TARGET_DIR / "include" / "datadog"
            if include_dir.exists():
                subprocess.run(
                    ["dedup_headers", "common.h", "profiling.h"],
                    cwd=str(include_dir),
                    check=True,
                    env=dedup_env,
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
        suffixes = cls.translate_suffix[CURRENT_OS]
        download_dir = Path(cls.download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)  # No need to check if it exists

        # If the version has changed since the last download, wipe and re-fetch.
        # This ensures version bumps are picked up even in incremental builds where
        # CleanLibraries.remove_artifacts() is skipped.
        version_sentinel = download_dir / ".version"
        if cls.version and version_sentinel.exists() and version_sentinel.read_text().strip() != cls.version:
            shutil.rmtree(download_dir)
            download_dir.mkdir(parents=True, exist_ok=True)

        # If the directory is nonempty (beyond the sentinel), assume we're done
        non_sentinel = [p for p in download_dir.iterdir() if p.name != ".version"]
        if non_sentinel:
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

        # Record the version so future incremental runs can detect bumps.
        if cls.version:
            (download_dir / ".version").write_text(cls.version)

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


class LibraryDownloader(BuildPyCommand):
    def run(self):
        # The setuptools docs indicate the `editable_mode` attribute of the build_py command class
        # is set to True when the package is being installed in editable mode, which we need to know
        # for some extensions
        global IS_EDITABLE
        if self.editable_mode:
            IS_EDITABLE = True

        # Skip wiping native extensions when incremental builds are enabled.
        # The skip checks in build_extension / build_extension_cmake rely on the
        # existing .so files (restored from ext_cache or left from the previous
        # build) to determine whether recompilation is needed.  Deleting them
        # here defeats those checks and forces a full rebuild every time.
        # LibraryDownload.download_artifacts() handles version bumps internally
        # via a .version sentinel, so libddwaf is always re-fetched when its
        # version changes even when CleanLibraries.remove_artifacts() is skipped.
        if not CustomBuildExt.INCREMENTAL:
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
        cmake_deps = LibraryDownload.CACHE_DIR / "_cmake_deps"
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


@dataclass
class SharedDep:
    """Declarative description of a C++ library built once and shared across extensions.

    Adding a new shared dependency requires:
      1. A standalone ``cmake/<name>/CMakeLists.txt`` that fetches and installs the library.
      2. An entry in ``SHARED_DEPS`` below.
      3. An ``elseif(DEFINED <cmake_var>)`` branch using ``find_package()`` in each
         consuming extension's CMakeLists.txt.

    ``ext_cache.py`` discovers all entries via the ``#SHAREDEPINFO:`` lines emitted by
    ``setup.py ext_hashes`` and caches or restores each install tree without any
    per-dependency special-casing.
    """

    name: str  # short identifier used for cache paths and log messages
    cmake_dir: Path  # directory containing the standalone CMakeLists.txt
    version: str  # version string included in the configuration hash
    cmake_var: str  # cmake variable forwarded to consuming extensions (-D<cmake_var>=<install_dir>)
    install_dir: Path  # CMAKE_INSTALL_PREFIX for this dependency
    # Platforms where this dep is needed; build_shared_deps() skips all others.
    platforms: tuple[str, ...] = ("Linux", "Darwin")
    # Optional callable: if it returns True the build is skipped (after the platform
    # check). Encode environment-specific conditions here (e.g. debug mode, env flags).
    should_skip: t.Optional[t.Callable[[], bool]] = None

    def config_hash(self) -> str:
        """Stable hash of the build configuration, used as the cache key.

        Covers dependency version, compile mode, platform, machine arch, and any
        macOS ``ARCHFLAGS`` targets.  Any change that would produce a different
        binary invalidates the hash and triggers a rebuild.
        """
        archs = re.findall(r"-arch (\S+)", os.environ.get("ARCHFLAGS", ""))
        parts = [self.version, COMPILE_MODE, sys.platform, platform.machine()] + archs
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

    def is_built(self) -> bool:
        """True if the install directory contains an up-to-date build."""
        sentinel = self.install_dir / ".dep_build_info"
        return sentinel.exists() and sentinel.read_text().strip() == self.config_hash()

    def mark_built(self) -> None:
        """Write the sentinel file after a successful build."""
        (self.install_dir / ".dep_build_info").write_text(self.config_hash())


# ---------------------------------------------------------------------------
# Shared C++ dependency declarations
# ---------------------------------------------------------------------------


def _absl_should_skip() -> bool:
    """Skip abseil in fast builds (DD_COMPILE_ABSEIL=0) and Debug mode."""
    if os.environ.get("DD_COMPILE_ABSEIL", "1") in ("0", "false"):
        return True
    return COMPILE_MODE.lower() == "debug"


SHARED_DEPS: list[SharedDep] = [
    SharedDep(
        name="absl",
        cmake_dir=HERE / "cmake" / "abseil",
        version="20250127.1",
        cmake_var="ABSL_INSTALL_DIR",
        install_dir=LibraryDownload.CACHE_DIR / "_cmake_deps" / f"absl_install_{platform.machine()}",
        platforms=("Linux", "Darwin"),
        should_skip=_absl_should_skip,
    ),
]


class CustomBuildExt(build_ext):
    INCREMENTAL = os.getenv("DD_CMAKE_INCREMENTAL_BUILD", "1").lower() in ("1", "yes", "on", "true")

    def run(self):
        with _time_phase("build_rust"):
            self.build_rust()

        # Build libdd_wrapper before building other extensions that depend on it
        if CURRENT_OS in ("Linux", "Darwin") and is_64_bit_python():
            with _time_phase("build_libdd_wrapper"):
                self.build_libdd_wrapper()

        # Build all declared shared C++ dependencies before extension builds.
        with _time_phase("build_shared_deps"):
            self.build_shared_deps()

        # super().run() iterates self.extensions and calls self.build_extension()
        # for each one — that is sufficient; no second loop needed.
        with _time_phase("build_extensions"):
            super().run()

    def build_extensions(self):
        # Enable parallel extension builds by default.  All extensions are
        # independent at this point (Rust and libdd_wrapper are already built
        # in run()), so they can safely compile concurrently.  The user can
        # override via ``--parallel N`` / ``-j N`` on the command line, or
        # set DD_BUILD_PARALLEL=0 to disable.
        dd_build_parallel = os.getenv("DD_BUILD_PARALLEL")
        if dd_build_parallel is not None:
            try:
                requested = int(dd_build_parallel)
            except ValueError:
                print(f"WARNING: DD_BUILD_PARALLEL={dd_build_parallel!r} is not a valid integer, ignoring")
                requested = 0
            self.parallel = requested if requested > 0 else False
        elif not self.parallel:
            self.parallel = _cpu_count
        super().build_extensions()

    def build_rust(self):
        """Build the Rust component using CustomBuildRust command."""
        self.suffix = sysconfig.get_config_var("EXT_SUFFIX")
        native_name = f"_native{self.suffix}"

        if IS_EDITABLE or getattr(self, "inplace", False):
            self.output_dir = Path(__file__).parent / "ddtrace" / "internal" / "native"
        else:
            self.output_dir = Path(__file__).parent / Path(self.build_lib) / "ddtrace" / "internal" / "native"

        library = self.output_dir / native_name

        # Determine the Rust source binary path - need to handle different architectures
        rust_source = NATIVE_CRATE / "target" / "release" / f"lib_native{self.suffix}"
        if not rust_source.exists():
            # Fallback to generic .so extension for cross-platform compatibility
            rust_source = NATIVE_CRATE / "target" / "release" / "lib_native.so"

        # Check if we need to run the Rust build by checking if sources are newer than the destination
        should_build = True
        if library.exists():
            library_mtime = library.stat().st_mtime

            # Check if any Rust source files are newer than the destination library
            cargo_files = [NATIVE_CRATE / "Cargo.toml", NATIVE_CRATE / "Cargo.lock"]

            # Get all source files (including subdirectories)
            source_files = []
            # Find all .rs files in the crate (including subdirectories)
            source_files.extend(NATIVE_CRATE.glob("**/*.rs"))
            # Add cargo files
            source_files.extend([f for f in cargo_files if f.exists()])

            # Check if any source file is newer than the library
            newest_source_time = 0
            for src_file in source_files:
                if src_file.exists():
                    newest_source_time = max(newest_source_time, src_file.stat().st_mtime)

            required_headers = ["common.h"]
            if "profiling" in rust_features:
                required_headers.append("profiling.h")

            include_dir = CARGO_TARGET_DIR / "include" / "datadog"
            headers_exist = include_dir.exists() and all((include_dir / header).exists() for header in required_headers)

            # Only rebuild if source files are newer than the destination OR if any required header is missing
            should_build = newest_source_time > library_mtime or not headers_exist

        if should_build:
            # Create and run the CustomBuildRust command
            build_rust_cmd = CustomBuildRust(self.distribution)
            build_rust_cmd.initialize_options()
            build_rust_cmd.finalize_options()
            # Propagate the inplace flag to the rust build command
            build_rust_cmd.inplace = getattr(self, "inplace", False)
            build_rust_cmd.run()

            if not library.exists():
                raise RuntimeError("Not able to find native library")

            print(f"Built and copied Rust extension: {native_name}")
            self.built_native = True
        else:
            print(f"Skipping Rust extension build (no changes): {native_name}")
            self.built_native = False

        # Set SONAME (always do this as it's idempotent)
        if CURRENT_OS == "Linux":
            subprocess.run(["patchelf", "--set-soname", native_name, library], check=True)
        elif CURRENT_OS == "Darwin":
            subprocess.run(["install_name_tool", "-id", native_name, library], check=True)

    def build_libdd_wrapper(self):
        """Build libdd_wrapper shared library as a dependency for profiling extensions."""
        dd_wrapper_dir = DDUP_DIR.parent / "dd_wrapper"

        # Determine output directory (profiling directory).
        # Store as self.wrapper_output_dir so _get_common_cmake_args can pass
        # DD_WRAPPER_DIR to downstream extensions (ddup, stack, memalloc).
        if IS_EDITABLE or getattr(self, "inplace", False):
            wrapper_output_dir = Path(__file__).parent / "ddtrace" / "internal" / "datadog" / "profiling"
        else:
            wrapper_output_dir = (
                Path(__file__).parent / Path(self.build_lib) / "ddtrace" / "internal" / "datadog" / "profiling"
            )
        self.wrapper_output_dir = wrapper_output_dir

        wrapper_name = f"libdd_wrapper{self.suffix}"
        wrapper_library = wrapper_output_dir / wrapper_name

        # Check if we need to build libdd_wrapper by checking if sources are newer
        should_build = True
        if wrapper_library.exists():
            wrapper_mtime = wrapper_library.stat().st_mtime

            # Check dd_wrapper source files
            source_files = []
            source_files.extend(dd_wrapper_dir.glob("**/*.cpp"))
            source_files.extend(dd_wrapper_dir.glob("**/*.hpp"))
            source_files.extend([dd_wrapper_dir / "CMakeLists.txt"])

            # Check if any source file is newer than the wrapper library
            newest_source_time = 0
            for src_file in source_files:
                if src_file.exists():
                    newest_source_time = max(newest_source_time, src_file.stat().st_mtime)

            # Rebuild if source files changed OR if _native.so was rebuilt (our dependency)
            source_files_changed = newest_source_time > wrapper_mtime
            should_build = source_files_changed or getattr(self, "built_native", False)

        if should_build:
            # Build libdd_wrapper using CMake
            cmake_build_dir = Path(self.build_lib.replace("lib.", "cmake."), "libdd_wrapper_build").resolve()
            cmake_build_dir.mkdir(parents=True, exist_ok=True)

            cmake_args = self._get_common_cmake_args(dd_wrapper_dir, cmake_build_dir, wrapper_output_dir, wrapper_name)

            build_args = [f"--config {COMPILE_MODE}"]
            if "CMAKE_BUILD_PARALLEL_LEVEL" not in os.environ:
                if hasattr(self, "parallel") and self.parallel:
                    build_args += [f"-j{self.parallel}"]

            install_args = [f"--config {COMPILE_MODE}"]

            cmake_command = (Path(cmake.CMAKE_BIN_DIR) / "cmake").resolve()
            subprocess.run([cmake_command, *cmake_args], cwd=cmake_build_dir, check=True)
            subprocess.run([cmake_command, "--build", ".", *build_args], cwd=cmake_build_dir, check=True)
            subprocess.run([cmake_command, "--install", ".", *install_args], cwd=cmake_build_dir, check=True)

            print(f"Built libdd_wrapper shared library: {wrapper_name}")
        else:
            print(f"Skipping libdd_wrapper build (no changes): {wrapper_name}")

    def build_shared_deps(self) -> None:
        """Build all shared C++ dependencies declared in SHARED_DEPS.

        Each dep that passes its platform and ``should_skip`` checks is built
        exactly once.  Successfully built deps are recorded in
        ``self._built_shared_deps`` so that ``_get_common_cmake_args`` can
        forward the install path to every consuming extension.
        """
        self._built_shared_deps: set[str] = set()
        for dep in SHARED_DEPS:
            if CURRENT_OS not in dep.platforms:
                continue
            if dep.should_skip is not None and dep.should_skip():
                print(f"Skipping {dep.name} build (should_skip returned True)")
                continue
            self._build_shared_dep(dep)

    def _build_shared_dep(self, dep: SharedDep) -> None:
        """Build *dep* and install it to ``dep.install_dir``."""
        if dep.is_built():
            print(f"{dep.name} already built at {dep.install_dir}, skipping")
            self._built_shared_deps.add(dep.name)
            return

        print(f"Building {dep.name} {dep.version} → {dep.install_dir}")
        dep.install_dir.mkdir(parents=True, exist_ok=True)

        cmake_build_dir = Path(self.build_lib.replace("lib.", "cmake."), f"{dep.name}_build").resolve()
        cmake_build_dir.mkdir(parents=True, exist_ok=True)

        cmake_command = (Path(cmake.CMAKE_BIN_DIR) / "cmake").resolve()

        cmake_args = self._base_cmake_args() + [
            f"-S{dep.cmake_dir}",
            f"-B{cmake_build_dir}",
            f"-DCMAKE_INSTALL_PREFIX={dep.install_dir}",
            "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
        ]

        if CURRENT_OS == "Darwin":
            archs = re.findall(r"-arch (\S+)", os.environ.get("ARCHFLAGS", ""))
            if archs:
                cmake_args += [
                    f"-DCMAKE_OSX_ARCHITECTURES={';'.join(archs)}",
                    "-DCMAKE_OSX_DEPLOYMENT_TARGET=10.14",
                ]

        subprocess.run([cmake_command, *cmake_args], cwd=cmake_build_dir, check=True)
        subprocess.run([cmake_command, "--build", ".", "--config", COMPILE_MODE], cwd=cmake_build_dir, check=True)
        subprocess.run([cmake_command, "--install", ".", "--config", COMPILE_MODE], cwd=cmake_build_dir, check=True)

        dep.mark_built()
        self._built_shared_deps.add(dep.name)
        print(f"{dep.name} built and installed to {dep.install_dir}")

    @staticmethod
    def try_strip_symbols(so_file):
        if CURRENT_OS == "Linux" and shutil.which("strip") is not None:
            try:
                subprocess.run(["strip", "-g", so_file], check=True)
            except subprocess.CalledProcessError as e:
                print(
                    "WARNING: stripping '{}' returned non-zero exit status ({}), ignoring".format(so_file, e.returncode)
                )
            except Exception as e:
                print(
                    "WARNING: An error occurred while stripping the symbols from '{}', ignoring: {}".format(so_file, e)
                )

    def build_extension(self, ext):
        if isinstance(ext, CMakeExtension):
            try:
                self.build_extension_cmake(ext)
            except subprocess.CalledProcessError as e:
                print("WARNING: Command '{}' returned non-zero exit status {}.".format(e.cmd, e.returncode))
                if ext.optional:
                    return
                raise
            except Exception as e:
                print("WARNING: An error occurred while building the CMake extension {}, {}.".format(ext.name, e))
                if ext.optional:
                    return
                raise
        else:
            # Skip compilation when the output .so is already newer than all
            # sources.  ext.sources contains the .c files (post-cythonize), so
            # if Cython regenerated a .c due to a .pxd or .pyx change the .c
            # will be newer and this guard will correctly let the build proceed.
            # We use the inplace path (source-tree location) explicitly because
            # that is where ext_cache always restores .so files (it runs
            # ext_hashes --inplace), regardless of the current self.inplace.
            if self.INCREMENTAL:
                # get_ext_filename gives the package-relative path, e.g.
                # "ddtrace/profiling/collector/_lock.cpython-313-darwin.so"
                ext_inplace = Path(self.get_ext_filename(ext.name)).resolve()
                full_path = Path(self.get_ext_fullpath(ext.name))

                # Use the .pyx sources (not the generated .c files) for the
                # mtime comparison.  cythonize() touches the .c file during
                # the same process that restores the .so from ext_cache, so
                # they can have identical timestamps and newer_group would
                # incorrectly force a rebuild.  The .pyx files are stable
                # (committed to git) and only change when the developer edits
                # them.  Fall back to .c if no .pyx sibling exists.
                def _pyx_or_c(c: str) -> str:
                    p = Path(c)
                    pyx = p.with_suffix(".pyx")
                    return str(pyx.resolve()) if pyx.exists() else str(p.resolve())

                sources_for_check = [_pyx_or_c(s) for s in ext.sources]
                # Also include all .pxd files so declaration changes invalidate the cache.
                sources_for_check.extend(str(p.resolve()) for p in (HERE / "ddtrace").glob("**/*.pxd") if p.is_file())
                if not newer_group(
                    sources_for_check,
                    str(ext_inplace),
                    "newer",
                ):
                    print(f"skipping '{ext.name}' extension (up-to-date)")
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    if ext_inplace != full_path.resolve():
                        shutil.copy(ext_inplace, full_path)
                else:
                    super().build_extension(ext)
            else:
                super().build_extension(ext)

        if COMPILE_MODE.lower() in ("release", "minsizerel"):
            try:
                self.try_strip_symbols(self.get_ext_fullpath(ext.name))
            except Exception as e:
                print(f"WARNING: An error occurred while building the extension: {e}")

    @staticmethod
    def _base_cmake_args(build_type: t.Optional[str] = None) -> list:
        """CMake arguments shared by every invocation: build type, download cache, sccache.

        Both ``_build_shared_dep`` and ``_get_common_cmake_args`` use this so the two
        call-sites stay in sync without duplicating the logic.
        """
        args = [
            f"-DCMAKE_BUILD_TYPE={build_type or COMPILE_MODE}",
            f"-DFETCHCONTENT_BASE_DIR={LibraryDownload.CACHE_DIR / '_cmake_deps'}",
        ]
        sccache_path = os.getenv("DD_SCCACHE_PATH")
        if sccache_path:
            args += [
                f"-DCMAKE_C_COMPILER={os.getenv('DD_CC_OLD', shutil.which('cc'))}",
                f"-DCMAKE_C_COMPILER_LAUNCHER={sccache_path}",
                f"-DCMAKE_CXX_COMPILER={os.getenv('DD_CXX_OLD', shutil.which('c++'))}",
                f"-DCMAKE_CXX_COMPILER_LAUNCHER={sccache_path}",
            ]
        return args

    def _get_common_cmake_args(self, source_dir, build_dir, output_dir, extension_name, build_type=None):
        """Get common CMake arguments used by both libdd_wrapper and extensions."""
        # Use base_prefix (not prefix) to get the actual Python installation path even when in a venv
        # Resolve symlinks so CMake can find include/lib directories relative to the real installation
        python_root = Path(sys.base_prefix).resolve()

        cmake_args = self._base_cmake_args(build_type) + [
            f"-S{source_dir}",
            f"-B{build_dir}",
            f"-DPython3_ROOT_DIR={python_root}",
            f"-DPython3_EXECUTABLE={sys.executable}",
            f"-DPYTHON_EXECUTABLE={sys.executable}",
            f"-DLIB_INSTALL_DIR={output_dir}",
            f"-DEXTENSION_NAME={extension_name}",
            f"-DEXTENSION_SUFFIX={self.suffix}",
            f"-DNATIVE_EXTENSION_LOCATION={self.output_dir}",
            f"-DRUST_GENERATED_HEADERS_DIR={CARGO_TARGET_DIR / 'include'}",
            f"-DCMAKE_MODULE_PATH={HERE / 'cmake'}",
        ]
        # Pass DD_WRAPPER_DIR for ddup/stack/memalloc cmake builds to find libdd_wrapper
        if hasattr(self, "wrapper_output_dir"):
            cmake_args += [
                f"-DDD_WRAPPER_DIR={self.wrapper_output_dir}",
            ]

        # Forward the install path of every successfully pre-built shared dep so
        # each consuming extension's CMakeLists.txt can use find_package() instead
        # of running its own FetchContent build.
        built = getattr(self, "_built_shared_deps", set())
        for dep in SHARED_DEPS:
            if dep.name in built:
                cmake_args += [f"-D{dep.cmake_var}={dep.install_dir}"]

        # Point FetchContent downloads at a persistent download cache so CMake
        # doesn't re-fetch from GitHub (e.g. abseil) on every build invocation.
        # Each extension gets its own subdirectory so parallel cmake builds
        # don't race on the same FetchContent state files.  Sources are still
        # cached on disk, so subsequent builds reuse them.
        # FETCHCONTENT_BASE_DIR defaults to a path inside the ephemeral cmake
        # build dir, so without this every build would re-download from GitHub.
        ext_cache_key = Path(
            extension_name
        ).stem  # e.g. "_native.cpython-314-darwin.so" -> "_native.cpython-314-darwin"
        cmake_args += [
            f"-DFETCHCONTENT_BASE_DIR={LibraryDownload.CACHE_DIR / '_cmake_deps' / ext_cache_key}",
        ]

        # Add sccache support if available
        sccache_path = os.getenv("DD_SCCACHE_PATH")
        if sccache_path:
            cmake_args += [
                f"-DCMAKE_C_COMPILER={os.getenv('DD_CC_OLD', shutil.which('cc'))}",
                f"-DCMAKE_C_COMPILER_LAUNCHER={sccache_path}",
                f"-DCMAKE_CXX_COMPILER={os.getenv('DD_CXX_OLD', shutil.which('c++'))}",
                f"-DCMAKE_CXX_COMPILER_LAUNCHER={sccache_path}",
            ]

        return cmake_args

    def build_extension_cmake(self, ext: "CMakeExtension") -> None:
        if self.INCREMENTAL:
            # DEV: Rudimentary incremental build support. We copy the logic from
            # setuptools' build_ext command, best effort.
            full_path = Path(self.get_ext_fullpath(ext.name))
            ext_path = Path(ext.source_dir, full_path.name)

            force = self.force

            if ext.dependencies:
                dependencies = [
                    str(d.resolve())
                    for dependency in ext.dependencies
                    for d in dependency.parent.glob(dependency.name + "*")
                    if d.is_file()
                ]
                if not dependencies:
                    # We expected some dependencies but none were found so we
                    # force the build to happen
                    force = True

            else:
                dependencies = []

            if not (
                force
                or newer_group(
                    [str(_.resolve()) for _ in ext.get_sources()] + dependencies, str(ext_path.resolve()), "newer"
                )
            ):
                print(f"skipping '{ext.name}' CMake extension (up-to-date)")

                # We need to copy the binary where setuptools expects it
                full_path.parent.mkdir(parents=True, exist_ok=True)
                if ext_path.resolve() != full_path.resolve():
                    shutil.copy(ext_path, full_path)

                return
            else:
                print(f"building '{ext.name}' CMake extension")

        # Define the build and output directories
        output_dir = Path(self.get_ext_fullpath(ext.name)).parent.resolve()
        extension_basename = Path(self.get_ext_fullpath(ext.name)).name

        # We derive the cmake build directory from the output directory, but put it in
        # a sibling directory to avoid polluting the final package
        cmake_build_dir = Path(self.build_lib.replace("lib.", "cmake."), ext.name).resolve()
        cmake_build_dir.mkdir(parents=True, exist_ok=True)

        # Which commands are passed to _every_ cmake invocation
        cmake_args = ext.cmake_args or []
        cmake_args += self._get_common_cmake_args(
            ext.source_dir, cmake_build_dir, output_dir, extension_basename, ext.build_type
        )

        if BUILD_PROFILING_NATIVE_TESTS:
            cmake_args += ["-DBUILD_TESTING=ON"]
        else:
            cmake_args += ["-DBUILD_TESTING=OFF"]

        # If this is an inplace build, propagate this fact to CMake in case it's helpful
        # In particular, this is needed for build products which are not otherwise managed
        # by setuptools/distutils
        if IS_EDITABLE:
            # the INPLACE_LIB_INSTALL_DIR should be the source dir of the extension
            cmake_args.append("-DINPLACE_LIB_INSTALL_DIR={}".format(ext.source_dir))

        # Arguments to the cmake --build command
        build_args = ext.build_args or []
        build_args += ["--config {}".format(ext.build_type)]
        if "CMAKE_BUILD_PARALLEL_LEVEL" not in os.environ:
            # CMAKE_BUILD_PARALLEL_LEVEL works across all generators
            # self.parallel is a Python 3 only way to set parallel jobs by hand
            # using -j in the build_ext call, not supported by pip or PyPA-build.
            # DEV: -j is supported in CMake 3.12+ only.
            if hasattr(self, "parallel") and self.parallel:
                build_args += ["-j{}".format(self.parallel)]

        # Arguments to cmake --install command
        install_args = ext.install_args or []
        install_args += ["--config {}".format(ext.build_type)]

        # platform/version-specific arguments--may go into cmake, build, or install as needed
        if CURRENT_OS == "Windows":
            arch = platform.machine().lower()
            if arch in ("amd64", "x86_64"):
                cmake_arch = "x64"
            elif arch in ("x86", "i386", "i686"):
                cmake_arch = "Win32"
            elif arch == "arm64":
                cmake_arch = "ARM64"
            else:
                raise RuntimeError(f"Unsupported architecture: {arch}")
            cmake_args += [f"-A{cmake_arch}"]
        if CURRENT_OS == "Darwin":
            # Cross-compile support for macOS - respect ARCHFLAGS if set
            # Darwin Universal2 should bundle both architectures
            # This is currently specific to IAST and requires cmakefile support
            archs = re.findall(r"-arch (\S+)", os.environ.get("ARCHFLAGS", ""))
            if archs:
                cmake_args += [
                    "-DBUILD_MACOS=ON",
                    "-DCMAKE_OSX_ARCHITECTURES={}".format(";".join(archs)),
                    # Set macOS SDK default deployment target to 10.14 for C++17 support (if unset, may default to 10.9)
                    "-DCMAKE_OSX_DEPLOYMENT_TARGET=10.14",
                ]

        if CURRENT_OS != "Windows" and FAST_BUILD and ext.build_type:
            cmake_args += [
                "-DCMAKE_C_FLAGS_%s=-O0" % ext.build_type.upper(),
                "-DCMAKE_CXX_FLAGS_%s=-O0" % ext.build_type.upper(),
            ]
        cmake_command = (
            Path(cmake.CMAKE_BIN_DIR) / "cmake"
        ).resolve()  # explicitly use the cmake provided by the cmake package
        subprocess.run([cmake_command, *cmake_args], cwd=cmake_build_dir, check=True)
        subprocess.run([cmake_command, "--build", ".", *build_args], cwd=cmake_build_dir, check=True)
        subprocess.run([cmake_command, "--install", ".", *install_args], cwd=cmake_build_dir, check=True)


class DebugMetadata:
    start_ns = 0
    enabled = "_DD_DEBUG_EXT" in os.environ
    metadata_file = os.getenv("_DD_DEBUG_EXT_FILE", "debug_ext_metadata.txt")
    build_times: dict[str, int] = {}
    shared_dep_times: dict[str, int] = {}  # dep.name → elapsed_ns
    download_times: dict[str, int] = {}
    phase_times: dict[str, int] = {}  # phase name → elapsed_ns

    @classmethod
    def dump_metadata(cls):
        if not cls.enabled or not cls.build_times:
            return

        total_ns = time.time_ns() - cls.start_ns
        total_s = total_ns / 1e9

        build_total_ns = sum(cls.build_times.values())
        build_total_s = build_total_ns / 1e9
        build_percent = (build_total_ns / total_ns) * 100.0

        with open(cls.metadata_file, "w") as f:
            f.write(f"Total time: {total_s:0.2f}s\n")
            f.write("Environment:\n")
            for n, v in [
                ("CARGO_BUILD_JOBS", os.getenv("CARGO_BUILD_JOBS", "unset")),
                ("CMAKE_BUILD_PARALLEL_LEVEL", os.getenv("CMAKE_BUILD_PARALLEL_LEVEL", "unset")),
                ("DD_COMPILE_MODE", COMPILE_MODE),
                ("DD_USE_SCCACHE", SCCACHE_COMPILE),
                ("DD_FAST_BUILD", FAST_BUILD),
                ("DD_CMAKE_INCREMENTAL_BUILD", CustomBuildExt.INCREMENTAL),
            ]:
                print(f"\t{n}: {v}", file=f)

            if cls.phase_times:
                phase_total_ns = sum(cls.phase_times.values())
                phase_total_s = phase_total_ns / 1e9
                phase_percent = (phase_total_ns / total_ns) * 100.0
                f.write("Build phase times:\n")
                f.write(f"\tTotal: {phase_total_s:0.2f}s ({phase_percent:0.2f}%)\n")
                for name, elapsed_ns in sorted(cls.phase_times.items(), key=lambda x: x[1], reverse=True):
                    elapsed_s = elapsed_ns / 1e9
                    pct = (elapsed_ns / total_ns) * 100.0
                    f.write(f"\t{name}: {elapsed_s:0.2f}s ({pct:0.2f}%)\n")

            f.write("Extension build times:\n")
            f.write(f"\tTotal: {build_total_s:0.2f}s ({build_percent:0.2f}%)\n")
            for ext, elapsed_ns in sorted(cls.build_times.items(), key=lambda x: x[1], reverse=True):
                elapsed_s = elapsed_ns / 1e9
                ext_percent = (elapsed_ns / total_ns) * 100.0
                f.write(f"\t{ext.name}: {elapsed_s:0.2f}s ({ext_percent:0.2f}%)\n")

            if cls.shared_dep_times:
                f.write("Shared dependency build times:\n")
                for name, elapsed_ns in sorted(cls.shared_dep_times.items(), key=lambda x: x[1], reverse=True):
                    elapsed_s = elapsed_ns / 1e9
                    dep_percent = (elapsed_ns / total_ns) * 100.0
                    f.write(f"\t{name}: {elapsed_s:0.2f}s ({dep_percent:0.2f}%)\n")

            if cls.download_times:
                download_total_ns = sum(cls.download_times.values())
                download_total_s = download_total_ns / 1e9
                download_percent = (download_total_ns / total_ns) * 100.0

                f.write("Artifact download times:\n")
                f.write(f"\tTotal: {download_total_s:0.2f}s ({download_percent:0.2f}%)\n")
                for n, elapsed_ns in sorted(cls.download_times.items(), key=lambda x: x[1], reverse=True):
                    elapsed_s = elapsed_ns / 1e9
                    ext_percent = (elapsed_ns / total_ns) * 100.0
                    f.write(f"\t{n}: {elapsed_s:0.2f}s ({ext_percent:0.2f}%)\n")


def debug_build_extension(fn):
    def wrapper(self, ext, *args, **kwargs):
        start = time.time_ns()
        try:
            return fn(self, ext, *args, **kwargs)
        finally:
            DebugMetadata.build_times[ext] = time.time_ns() - start

    return wrapper


def debug_build_shared_dep(fn):
    def wrapper(self, dep, *args, **kwargs):
        start = time.time_ns()
        try:
            return fn(self, dep, *args, **kwargs)
        finally:
            DebugMetadata.shared_dep_times[dep.name] = time.time_ns() - start

    return wrapper


@contextlib.contextmanager
def _time_phase(name: str):
    """Record elapsed time for a named build phase in DebugMetadata.phase_times."""
    start = time.time_ns()
    try:
        yield
    finally:
        DebugMetadata.phase_times[name] = DebugMetadata.phase_times.get(name, 0) + (time.time_ns() - start)


if DebugMetadata.enabled:
    DebugMetadata.start_ns = time.time_ns()
    CustomBuildExt.build_extension = debug_build_extension(CustomBuildExt.build_extension)
    CustomBuildExt._build_shared_dep = debug_build_shared_dep(CustomBuildExt._build_shared_dep)
    build_rust.build_extension = debug_build_extension(CustomBuildRust.build_extension)
    atexit.register(DebugMetadata.dump_metadata)


class CMakeExtension(Extension):
    def __init__(
        self,
        name,
        source_dir=Path.cwd(),
        extra_source_dirs=[],
        cmake_args=[],
        build_args=[],
        install_args=[],
        build_type=None,
        optional=True,  # By default, extensions are optional
        dependencies=[],
    ):
        super().__init__(name, sources=[])
        self.source_dir = source_dir
        self.extra_source_dirs = extra_source_dirs  # extra source dirs to include when computing extension hash
        self.cmake_args = cmake_args or []
        self.build_args = build_args or []
        self.install_args = install_args or []
        self.build_type = build_type or COMPILE_MODE
        self.optional = optional  # If True, cmake errors are ignored
        self.dependencies = dependencies

    def get_sources(self) -> list[Path]:
        """
        Returns the list of source files for this extension.
        This is used by the CustomBuildExt class to determine if the extension needs to be rebuilt.
        """

        # Collect all the source files within the source directory. We exclude
        # Python sources and anything that does not have a suffix (most likely
        # a binary file), or that has the same name as the extension binary.
        def is_valid_source(src: Path) -> bool:
            return (
                src.is_file()
                and src.suffix
                # Exclude compiled/generated artifacts that are not real sources:
                # .so/.dylib/.dll/.pyd — compiled native extensions (from co-located Cython exts)
                # .c — Cython-generated C files (present in shared source directories)
                # .py/.pyc/.pyi — Python sources and bytecode
                and src.suffix not in {".py", ".pyc", ".pyi", ".c", ".so", ".dylib", ".dll", ".pyd"}
            )

        return [
            src
            for source_dir in chain([self.source_dir], self.extra_source_dirs)
            for src in Path(source_dir).glob("**/*")
            if is_valid_source(src)
        ]


def check_rust_toolchain():
    try:
        rustc_res = subprocess.run(["rustc", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        cargo_res = subprocess.run(["cargo", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if rustc_res.returncode != 0:
            raise EnvironmentError("rustc required to build Rust extensions")
        if cargo_res.returncode != 0:
            raise EnvironmentError("cargo required to build Rust extensions")

        # Now check valid minimum versions.  These are hardcoded for now, but should be canonized in some other way
        rustc_ver = rustc_res.stdout.decode().split(" ")[1]
        cargo_ver = cargo_res.stdout.decode().split(" ")[1]
        if rustc_ver < RUST_MINIMUM_VERSION:
            raise EnvironmentError(f"rustc version {RUST_MINIMUM_VERSION} or later required, {rustc_ver} found")
        if cargo_ver < RUST_MINIMUM_VERSION:
            raise EnvironmentError(f"cargo version {RUST_MINIMUM_VERSION} or later required, {cargo_ver} found")
    except FileNotFoundError:
        raise EnvironmentError("Rust toolchain not found. Please install Rust from https://rustup.rs/")


# Before adding any extensions, check that system pre-requisites are satisfied
try:
    check_rust_toolchain()
except EnvironmentError as e:
    print(f"{e}")
    sys.exit(1)


def get_exts_for(name):
    try:
        mod = load_module_from_project_file(
            "ddtrace.vendor.{}.setup".format(name), "ddtrace/vendor/{}/setup.py".format(name)
        )
        return mod.get_extensions()
    except Exception as e:
        print("WARNING: Failed to load %s extensions, skipping: %s" % (name, e))
        return []


if CURRENT_OS == "Windows":
    encoding_libraries = ["ws2_32"]
    extra_compile_args = []
    debug_compile_args = []
    fast_build_args = []
else:
    linux = CURRENT_OS == "Linux"
    encoding_libraries = []
    extra_compile_args = ["-DPy_BUILD_CORE"]
    fast_build_args = ["-O0"] if FAST_BUILD else []
    if COMPILE_MODE.lower() == "debug":
        if linux:
            debug_compile_args = ["-g", "-O0", "-Wall", "-Wextra", "-Wpedantic"]
        else:
            debug_compile_args = [
                "-g",
                "-O0",
                "-Wall",
                "-Wextra",
                "-Wpedantic",
                # Cython is not deprecation-proof
                "-Wno-deprecated-declarations",
            ]
    else:
        debug_compile_args = []


if not IS_PYSTON:
    ext_modules: list[t.Union[Extension, Cython.Distutils.Extension, RustExtension]] = [
        Extension(
            "ddtrace.internal._threads",
            sources=["ddtrace/internal/_threads.cpp"],
            extra_compile_args=(
                ["-std=c++20", "-Wall", "-Wextra"] + fast_build_args
                if CURRENT_OS != "Windows"
                else ["/std:c++20", "/MT"]
            ),
        ),
    ]

    if platform.system() not in ("Windows", ""):
        ext_modules.append(
            Extension(
                "ddtrace.appsec._shared._stacktrace",
                sources=[
                    "ddtrace/appsec/_shared/_stacktrace.c",
                ],
                extra_compile_args=extra_compile_args + debug_compile_args + fast_build_args,
            )
        )
        ext_modules.append(
            Extension(
                "ddtrace.appsec._iast._ast.iastpatch",
                sources=[
                    "ddtrace/appsec/_iast/_ast/iastpatch.c",
                ],
                extra_compile_args=extra_compile_args + debug_compile_args + fast_build_args,
            )
        )
        ext_modules.append(
            CMakeExtension("ddtrace.appsec._iast._taint_tracking._native", source_dir=IAST_DIR, optional=False)
        )

    if CURRENT_OS in ("Linux", "Darwin") and is_64_bit_python():
        # Memory profiler now uses CMake to support Abseil dependency
        MEMALLOC_DIR = HERE / "ddtrace" / "profiling" / "collector"
        memalloc_cmake_args = []
        if os.environ.get("DD_PROFILING_MEMALLOC_ASSERT_ON_REENTRY", "0") not in ("0", ""):
            memalloc_cmake_args.append("-DMEMALLOC_ASSERT_ON_REENTRY=ON")
        ext_modules.append(
            CMakeExtension(
                "ddtrace.profiling.collector._memalloc",
                source_dir=MEMALLOC_DIR,
                cmake_args=memalloc_cmake_args,
                optional=False,
            )
        )

        ext_modules.append(
            CMakeExtension(
                "ddtrace.internal.datadog.profiling.ddup._ddup",
                source_dir=DDUP_DIR,
                extra_source_dirs=[
                    DDUP_DIR / ".." / "cmake",
                    DDUP_DIR / ".." / "dd_wrapper",
                ],
                optional=False,
            )
        )

        ext_modules.append(
            CMakeExtension(
                "ddtrace.internal.datadog.profiling.stack._stack",
                source_dir=STACK_DIR,
                extra_source_dirs=[
                    STACK_DIR / ".." / "cmake",
                    STACK_DIR / ".." / "dd_wrapper",
                ],
                optional=False,
            ),
        )


else:
    ext_modules = []


cython_exts = []
if os.getenv("DD_CYTHONIZE", "1").lower() in ("1", "yes", "on", "true"):
    with _time_phase("cythonize"):
        cython_exts = cythonize(
            [
                Cython.Distutils.Extension(
                    "ddtrace.internal._tagset",
                    sources=["ddtrace/internal/_tagset.pyx"],
                    language="c",
                ),
                Extension(
                    "ddtrace.internal._encoding",
                    ["ddtrace/internal/_encoding.pyx"],
                    include_dirs=["."],
                    libraries=encoding_libraries,
                    define_macros=[(f"__{sys.byteorder.upper()}_ENDIAN__", "1")],
                ),
                Extension(
                    "ddtrace.internal.telemetry.metrics_namespaces",
                    ["ddtrace/internal/telemetry/metrics_namespaces.pyx"],
                    language="c",
                ),
                Cython.Distutils.Extension(
                    "ddtrace.profiling._threading",
                    sources=["ddtrace/profiling/_threading.pyx"],
                    language="c",
                ),
                Cython.Distutils.Extension(
                    "ddtrace.profiling.collector._task",
                    sources=["ddtrace/profiling/collector/_task.pyx"],
                    language="c",
                ),
                Cython.Distutils.Extension(
                    "ddtrace.profiling.collector._exception",
                    sources=["ddtrace/profiling/collector/_exception.pyx"],
                    language="c",
                ),
                Cython.Distutils.Extension(
                    "ddtrace.profiling.collector._fast_poisson",
                    sources=["ddtrace/profiling/collector/_fast_poisson.pyx"],
                    language="c",
                ),
                Cython.Distutils.Extension(
                    "ddtrace.profiling.collector._sampler",
                    sources=["ddtrace/profiling/collector/_sampler.pyx"],
                    language="c",
                ),
                Cython.Distutils.Extension(
                    "ddtrace.profiling.collector._lock",
                    sources=["ddtrace/profiling/collector/_lock.pyx"],
                    language="c",
                ),
            ],
            compile_time_env={
                "PY_MAJOR_VERSION": sys.version_info.major,
                "PY_MINOR_VERSION": sys.version_info.minor,
                "PY_MICRO_VERSION": sys.version_info.micro,
                "PY_VERSION_HEX": sys.hexversion,
            },
            force=os.getenv("DD_SETUP_FORCE_CYTHONIZE", "0") == "1",
            annotate=os.getenv("_DD_CYTHON_ANNOTATE") == "1",
            compiler_directives={"language_level": "3"},
            cache=True,
        )

PACKAGE_NAME = f"ddtrace{WHEEL_FLAVOR}"
if PACKAGE_NAME != "ddtrace":
    subprocess.run(["sed", "-i", "-e", f's/^name = ".*"/name = "{PACKAGE_NAME}"/g', "pyproject.toml"])
print(f"INFO: building package '{PACKAGE_NAME}'")


def _git(*args: str) -> t.Optional[str]:
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.decode("utf-8", "replace").strip()


def _in_git_repo() -> bool:
    # True iff HERE is itself the top level of a git working tree. We use
    # `rev-parse --show-toplevel` rather than `--git-dir` because the latter
    # walks up through parent directories: if a stamped sdist is unpacked
    # inside some other project's checkout, `--git-dir` would match that
    # outer repo and stale-stamp recovery would corrupt the sdist's
    # pyproject.toml. Sdist installs and tarball extracts outside any repo
    # naturally fall through to False.
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(HERE),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    toplevel = out.stdout.decode("utf-8", "replace").strip()
    if not toplevel:
        return False
    return Path(toplevel).resolve() == HERE.resolve()


def _head_on_main() -> bool:
    # Only stamp when HEAD is an ancestor of the *remote* main branch
    # (refs/remotes/origin/main) — i.e. the commit is on, or has been merged
    # to, main. A local refs/heads/main can be stale or contain unpushed
    # commits, so we deliberately do not fall back to it; a clone with no
    # origin/main simply doesn't get stamped. Callers must ensure we're
    # already in a git repo (see _in_git_repo).
    ref = "refs/remotes/origin/main"
    # Probe whether origin/main exists. Exit 1 is the normal "ref absent"
    # answer (forks without tracking refs, mirrors without main, clones that
    # never fetched origin) and we stay silent for it. Any other non-zero
    # exit indicates the query itself failed and is worth surfacing.
    try:
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=str(HERE),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        if exc.returncode != 1:
            print(
                f"WARN: rc version stamping skipped; `git rev-parse --verify {ref}` exited {exc.returncode}",
                file=sys.stderr,
            )
        return False
    except OSError:
        return False
    # Ref exists — now check ancestry. Exit 1 is the normal "HEAD is not an
    # ancestor" answer (topic branches, release-branch checkouts, detached
    # older commits). Any other non-zero exit indicates the query itself
    # failed — most often a shallow clone whose history doesn't reach back
    # far enough.
    try:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", "HEAD", ref],
            cwd=str(HERE),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except subprocess.CalledProcessError as exc:
        if exc.returncode != 1:
            print(
                "WARN: rc version stamping skipped; `git merge-base "
                "--is-ancestor HEAD origin/main` exited "
                f"{exc.returncode} (likely a shallow clone — set GIT_DEPTH=0 "
                "or fetch --deepen if stamping is expected)",
                file=sys.stderr,
            )
        return False
    except OSError:
        return False


def _atomic_write(path: Path, text: str) -> None:
    # Write to a sibling tempfile and rename, so a crash mid-write can't leave
    # a half-written pyproject.toml on disk.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, str(path))
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _restore_pyproject(path: str, original_text: str) -> None:
    # atexit handler: restore pyproject.toml to its pre-stamp contents. Runs
    # after setup() has returned and all wheel/sdist metadata has been
    # written, so the restore cannot affect the artifact we just built.
    with contextlib.suppress(OSError):
        _atomic_write(Path(path), original_text)


# rc-stamping mutates pyproject.toml in place because setuptools reads the
# version from it; the original is restored via atexit so the working tree
# (and any subsequent build or `git status` check) stays clean. The local
# segment (`+g<sha>`) does not affect PEP 440 version precedence, so
# installs still upgrade as expected, but PyPI rejects local segments —
# the release-tag pipelines set DD_VERSION_NO_LOCAL=1 to skip stamping (see
# .gitlab-ci.yml workflow:rules).
# Only fires for rc builds (not a/b/dev) and only when HEAD is an ancestor
# of origin/main (i.e. the commit is on, or has been merged to, main), so
# topic branches and release-branch checkouts never get stamped.
#
# Exclusions:
#   - Windows builds: skipped entirely. Windows wheels ship with the plain
#     rc version; this avoids propagating env vars into the Windows docker
#     runner and means aggregation tooling must treat wheel-local segments
#     as optional (see .gitlab/validate-ddtrace-package.py).
#   - Sdist builds: skipped when `"sdist"` appears in sys.argv. A stamped
#     sdist would outrank unstamped platform wheels in pip's candidate
#     ordering, so a Windows resolver pointed at the combined wheelhouse
#     would pull the sdist and try to build from source. Only wheels on
#     supported platforms carry the commit hash.
#   - Non-repo contexts: if HERE is not the top level of a git working tree
#     (sdist rebuild, tarball extract, or HERE nested inside an unrelated
#     repo), we respect whatever pyproject.toml already says.
#
# The implementation is intentionally lock-free: every concurrent-build flow
# we actually care about (CI containers, separate worktrees) runs in its
# own directory, so the stamp+atexit-restore window can't overlap. In the
# one remaining theoretical race — two `pip install -e .` running in the
# exact same checkout — the worst outcome is one wheel being unstamped,
# which is benign.
#
# Stale-stamp recovery handles a prior build that crashed via SIGKILL/OOM
# before atexit could restore: if the file on disk already has `+g<sha>`,
# strip it back to the baseline. This runs in two situations: (1)
# DD_VERSION_NO_LOCAL=1 — we must clear any stale stamp so the escape
# hatch actually produces a clean artifact for PyPI; and (2) we're in a
# git repo — safe to strip because we can re-stamp or cleanly leave the
# baseline on disk. We deliberately do NOT strip when we're in a non-repo
# context without the env var: pyproject.toml's version there is
# authoritative (a non-repo build has no other source of truth).
def _maybe_stamp_rc_version() -> None:
    if sys.platform == "win32":
        return
    pyproject = HERE / "pyproject.toml"
    try:
        text = pyproject.read_text()
    except OSError:
        return
    m = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    if not m:
        return
    version = m.group(1)

    no_local = os.environ.get("DD_VERSION_NO_LOCAL") == "1"
    in_repo = _in_git_repo()

    if no_local or in_repo:
        stale = re.match(r"^(.+?)\+g[0-9a-f]{7,}$", version)
        if stale:
            stripped = stale.group(1)
            text = text.replace(f'version = "{version}"', f'version = "{stripped}"', 1)
            version = stripped
            # Persist the recovered baseline immediately so that if we bail
            # out below (non-rc, non-main ancestor, no head sha, or the
            # no_local early return) the tree still ends up clean.
            try:
                _atomic_write(pyproject, text)
            except OSError:
                print(
                    "WARN: rc version stamping skipped; failed to restore stale "
                    "pyproject.toml stamp (dirty tree left on disk)",
                    file=sys.stderr,
                )
                return

    if no_local:
        # Release-tag pipelines: output must not carry a local segment. We
        # already stripped any stale stamp above; don't stamp anew.
        return
    if not in_repo:
        # Sdist rebuild or other non-repo context. Respect whatever version
        # is already in pyproject.toml — in particular, if it's a stamped
        # sdist, its local segment must be preserved so the wheel filename
        # matches the sdist name.
        return
    if "sdist" in sys.argv:
        # Sdist builds are intentionally left unstamped. A stamped sdist
        # (`4.8.0rc4+g<sha>`) would outrank unstamped platform wheels
        # (notably the Windows wheels, which never stamp) in pip's
        # candidate ordering, so a Windows resolver pointed at the combined
        # wheelhouse would pull the sdist and try to build from source.
        # Wheels on supported platforms still carry the commit hash via the
        # bdist_wheel path.
        return
    if not re.search(r"rc\d+", version):
        return
    if not _head_on_main():
        return
    short_sha = _git("rev-parse", "--short=7", "HEAD")
    if not short_sha:
        return
    new_version = f"{version}+g{short_sha}"
    new_text = text.replace(f'version = "{version}"', f'version = "{new_version}"', 1)
    try:
        _atomic_write(pyproject, new_text)
    except OSError:
        return
    atexit.register(_restore_pyproject, str(pyproject), text)
    print(f"INFO: stamped rc build with commit hash: {new_version}")


_maybe_stamp_rc_version()

interpose_sccache()
setup(
    name="ddtrace",
    packages=find_packages(
        exclude=[
            "tests*",
            "benchmarks*",
            "scripts*",
            # pybind11 vendor is a build-time dependency only; nothing in ddtrace imports it at runtime
            "ddtrace.appsec._iast._taint_tracking._vendor.pybind11*",
            # C++ test helpers, not needed at runtime
            "ddtrace.internal.datadog.profiling.ddup.test*",
        ]
    ),
    include_package_data=False,
    package_data={
        # Type stubs and markers for all packages
        "": ["*.pyi", "py.typed"],
        "ddtrace.appsec": ["rules.json"],
        "ddtrace.appsec._ddwaf": ["libddwaf/*/lib/libddwaf.*"],
        "ddtrace.appsec.sca": ["_cve_data.json"],
        "ddtrace.internal": ["third-party.tar.gz"],
        "ddtrace.internal.datadog.profiling": (
            ["libdd_wrapper*.*"] + (["test/*"] if BUILD_PROFILING_NATIVE_TESTS else [])
        ),
    },
    zip_safe=False,
    # enum34 is an enum backport for earlier versions of python
    # funcsigs backport required for vendored debtcollector
    cmdclass={
        "build_ext": CustomBuildExt,
        "build_py": LibraryDownloader,
        "build_rust": CustomBuildRust,
        "clean": CleanLibraries,
        "ext_hashes": ExtensionHashes,
    },
    setup_requires=[
        "cython",
        "cmake>=3.24.2,<3.28",
        "setuptools-rust<2",
        "patchelf>=0.17.0.0; sys_platform == 'linux'",
    ],
    ext_modules=ext_modules + cython_exts + get_exts_for("psutil"),
    distclass=PatchedDistribution,
)
