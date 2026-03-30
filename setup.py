import atexit
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
import time
import typing as t
import warnings

import cmake
from setuptools_rust import Binding
from setuptools_rust import RustExtension
from setuptools_rust import build_rust


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
    from Cython.Build.Dependencies import create_dependency_tree as _cython_dep_tree
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
    # Trade binary size for compilation speed in dev environments by disabling
    # LTO and increasing codegen parallelism. Never used for release wheels.
    os.environ.setdefault("CARGO_PROFILE_RELEASE_LTO", "off")
    os.environ.setdefault("CARGO_PROFILE_RELEASE_CODEGEN_UNITS", "16")
    os.environ.setdefault("CARGO_PROFILE_RELEASE_OPT_LEVEL", "2")

SCCACHE_COMPILE = os.getenv("DD_USE_SCCACHE", "0").lower() in ("1", "yes", "on", "true")

# Retry configuration for downloads (handles GitHub API failures like 503, 429)
DOWNLOAD_MAX_RETRIES = int(os.getenv("DD_DOWNLOAD_MAX_RETRIES", "10"))
DOWNLOAD_INITIAL_DELAY = float(os.getenv("DD_DOWNLOAD_INITIAL_DELAY", "1.0"))
DOWNLOAD_MAX_DELAY = float(os.getenv("DD_DOWNLOAD_MAX_DELAY", "120"))

# Shared cmake dependencies fetched once and reused by all extensions.
# Each entry maps to a FetchContent_Declare() + FetchContent_MakeAvailable() block.
# cmake_vars is a list of (name, value, type) tuples set as cmake cache variables
# *before* FetchContent_MakeAvailable() so the dep can see them during its configure.
FETCH_CONTENT_DEPS: "list[dict[str, t.Any]]" = [
    dict(
        name="absl",
        repository="https://github.com/abseil/abseil-cpp.git",
        tag="20250127.1",
        shallow=True,
        # ABSL_ENABLE_INSTALL: Abseil disables its own install rules when it detects it is
        #   not the top-level project; forcing ON re-enables them so "cmake --install"
        #   produces the headers and static libs consumable via find_package(absl).
        # ABSL_BUILD_TESTING / BUILD_TESTING: avoid pulling in googletest.
        cmake_vars=[
            ("ABSL_ENABLE_INSTALL", "ON", "BOOL"),
            ("ABSL_BUILD_TESTING", "OFF", "BOOL"),
            ("BUILD_TESTING", "OFF", "BOOL"),
        ],
    ),
]

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
                    sources = ext.get_sources(self)
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
                    sources = [Path(_) for _ in ext.sources]
                    # Header files are stored in ext.depends by convention; include
                    # them so that a header-only change changes the hash and forces
                    # a cache miss.
                    if ext.depends:
                        sources.extend(Path(_) for _ in ext.depends)

                sources_hash = hashlib.sha256()
                # DEV: Make sure to include the rust features since changing them changes what gets built
                for feature in rust_features:
                    sources_hash.update(feature.encode())
                for source in sorted(sources):
                    sources_hash.update(source.read_bytes())
                hash_digest = sources_hash.hexdigest()

                entries: list[tuple[str, str, str]] = []
                entries.append((ext.name, hash_digest, str(Path(self.get_ext_fullpath(ext.name)))))

                # Headers generated by the Rust build are consumed by downstream
                # C/C++ steps (build_libdd_wrapper, MemallocExtension).  Cache
                # them under the same hash as the Rust extension so that a CI
                # cache hit restores everything needed for those steps without
                # re-running cargo.
                if isinstance(ext, RustExtension):
                    include_dir = CARGO_TARGET_DIR / "include" / "datadog"
                    entries.extend((ext.name, hash_digest, str(h)) for h in sorted(include_dir.glob("*.h")))

                # Include any dependencies that might have been built alongside
                # the extension.
                if isinstance(ext, CMakeExtension):
                    entries.extend(
                        (f"{ext.name}-{dependency.name}", hash_digest, str(dependency) + "*")
                        for dependency in ext.dependencies
                    )

                for entry in entries:
                    print("#EXTHASH:", entry)

            # cmake shared deps (Abseil etc.) — the entire install prefix is a
            # directory tree that ext_cache.py stashes and restores as a whole.
            # Hash is derived from FETCH_CONTENT_DEPS content + platform + mode
            # so any dep bump or platform change produces a distinct cache key.
            platform_tag = sysconfig.get_platform()
            cmake_deps_hash = hashlib.sha256()
            for dep in FETCH_CONTENT_DEPS:
                cmake_deps_hash.update(dep["name"].encode())
                cmake_deps_hash.update(dep["repository"].encode())
                cmake_deps_hash.update(dep["tag"].encode())
                for var_name, var_value, var_type in dep.get("cmake_vars", []):
                    cmake_deps_hash.update(f"{var_name}={var_value}:{var_type}".encode())
            cmake_deps_hash.update(platform_tag.encode())
            cmake_deps_hash.update(COMPILE_MODE.encode())
            deps_install_dir = HERE / "build" / f"cmake_deps.{platform_tag}.{COMPILE_MODE}" / "install"
            # Trailing "/" signals to ext_cache.py that this is a directory tree,
            # not a single file, and should be stashed/restored with copytree.
            print("#EXTHASH:", ("__cmake_deps__", cmake_deps_hash.hexdigest(), str(deps_install_dir) + "/"))

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

            # Run dedup_headers on all headers the Rust build generated.
            # Discovered by globbing include/datadog/ so no feature-specific
            # list needs to be maintained here.
            include_dir = CARGO_TARGET_DIR / "include" / "datadog"
            if include_dir.exists():
                header_files = sorted(p.name for p in include_dir.glob("*.h"))
                if header_files:
                    subprocess.run(
                        ["dedup_headers", *header_files],
                        cwd=str(include_dir),
                        check=True,
                        env=dedup_env,
                    )


class LibraryDownload:
    CACHE_DIR = Path(os.getenv("DD_SETUP_CACHE_DIR", HERE / ".download_cache"))
    USE_CACHE = os.getenv("DD_SETUP_CACHE_DOWNLOADS", "1").lower() in ("1", "yes", "on", "true")

    name: "t.Optional[str]" = None
    download_dir = Path.cwd()
    version: "t.Optional[str]" = None
    url_root: "t.Optional[str]" = None
    available_releases: "dict[str, list[str]]" = {}
    expected_checksums = None
    translate_suffix: "dict[str, tuple[str, ...]]" = {}

    @classmethod
    def download_artifacts(cls):
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
        global IS_EDITABLE
        if self.editable_mode:
            IS_EDITABLE = True

        CleanLibraries.remove_artifacts()
        LibDDWafDownload.run()
        BuildPyCommand.run(self)


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
        # Remove the shared FetchContent source cache. Build artifacts live in
        # each extension's per-project cmake build dir (under build/) which is
        # removed by remove_build_dir().
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
        CleanLibraries.remove_native_extensions()
        CleanLibraries.remove_build_dir()
        if self.all:
            CleanLibraries.remove_build_artifacts()


class CustomBuildExt(build_ext):
    INCREMENTAL = os.getenv("DD_CMAKE_INCREMENTAL_BUILD", "1").lower() in ("1", "yes", "on", "true")

    def finalize_options(self):
        super().finalize_options()
        # Pin build_temp to the source tree so that .o files survive across
        # `pip install -e .` invocations.  pip creates a new temp dir each run,
        # so if we let build_temp default to wherever build_lib is, object files
        # are discarded and the full C++ compilation repeats every time.
        # Include COMPILE_MODE in the path so that Debug and RelWithDebInfo
        # builds never share object files: distutils recompilation is timestamp-
        # based and cannot detect flag or macro differences, so mixing modes
        # would silently reuse stale objects built with the wrong configuration.
        plat = f"{sysconfig.get_platform()}-{sys.version_info.major}.{sys.version_info.minor}"
        self.build_temp = str(HERE / "build" / f"temp.{plat}.{COMPILE_MODE}")

    def run(self):
        self.build_rust()

        # Pre-build shared cmake deps (Abseil, etc.) as a standalone cmake project
        # so all extensions can reuse the compiled artifacts via find_package.
        # Raises on failure — extensions assume the install prefix always exists.
        self._cmake_deps_install_dir: Path = self._prebuild_cmake_deps()

        # Build libdd_wrapper before building other extensions that depend on it
        if CURRENT_OS in ("Linux", "Darwin") and is_64_bit_python():
            self.build_libdd_wrapper()

        super().run()
        for ext in self.extensions:
            self.build_extension(ext)

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

            include_dir = CARGO_TARGET_DIR / "include" / "datadog"
            headers_exist = include_dir.is_dir() and bool(list(include_dir.glob("*.h")))

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

        # Determine output directory (profiling directory)
        if IS_EDITABLE or getattr(self, "inplace", False):
            wrapper_output_dir = Path(__file__).parent / "ddtrace" / "internal" / "datadog" / "profiling"
        else:
            wrapper_output_dir = (
                Path(__file__).parent / Path(self.build_lib) / "ddtrace" / "internal" / "datadog" / "profiling"
            )

        wrapper_name = f"libdd_wrapper{self.suffix}"

        # Anchor the cmake build dir to the source tree so that cmake's own
        # incremental artefacts (object files, the built .so) survive across
        # `pip install -e .` invocations.  Using self.build_lib would put it
        # in pip's temp dir, which is discarded after each run.
        plat = f"{sysconfig.get_platform()}-cpython-{sys.version_info.major}{sys.version_info.minor}"
        cmake_build_dir = HERE / "build" / f"cmake.{plat}" / "libdd_wrapper_build"

        # Use the library inside the cmake build dir as the freshness anchor.
        # The cmake build dir is anchored to the source tree (not pip's temp
        # build_lib) so it persists across pip invocations.
        cmake_built_library = cmake_build_dir / wrapper_name
        source_files = (
            list(dd_wrapper_dir.glob("**/*.cpp"))
            + list(dd_wrapper_dir.glob("**/*.hpp"))
            + [dd_wrapper_dir / "CMakeLists.txt"]
        )
        cmake_command = (Path(cmake.CMAKE_BIN_DIR) / "cmake").resolve()
        build_args = [f"--config {COMPILE_MODE}"]
        if "CMAKE_BUILD_PARALLEL_LEVEL" not in os.environ:
            if hasattr(self, "parallel") and self.parallel:
                build_args += [f"-j{self.parallel}"]
        install_args = [f"--config {COMPILE_MODE}"]

        sources_changed = not cmake_built_library.exists() or newer_group(
            [str(f) for f in source_files if f.exists()], str(cmake_built_library), missing="newer"
        )
        if sources_changed:
            # Full configure + build + install.  We always reconfigure here so
            # cmake picks up the current cmake binary (pip creates a fresh build
            # env each run; using an old Makefile that embeds the previous pip
            # env's cmake path would cause gmake to fail with "No such file").
            cmake_build_dir.mkdir(parents=True, exist_ok=True)
            cmake_args = self._get_common_cmake_args(dd_wrapper_dir, cmake_build_dir, wrapper_output_dir, wrapper_name)
            subprocess.run([cmake_command, *cmake_args], cwd=cmake_build_dir, check=True)
            subprocess.run([cmake_command, "--build", ".", *build_args], cwd=cmake_build_dir, check=True)
            subprocess.run([cmake_command, "--install", ".", *install_args], cwd=cmake_build_dir, check=True)

            print(f"Built libdd_wrapper shared library: {wrapper_name}")
        else:
            # Sources unchanged: ensure the inplace copy is present (may be
            # absent on a fresh checkout or after a manual clean).
            wrapper_output_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cmake_built_library, wrapper_output_dir / wrapper_name)
            print(f"Skipping libdd_wrapper build (no changes): {wrapper_name}")

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
            prepare = getattr(ext, "prepare", None)
            if prepare is not None:
                prepare(self)
            self._build_extension_incremental(ext)

        if COMPILE_MODE.lower() in ("release", "minsizerel"):
            try:
                self.try_strip_symbols(self.get_ext_fullpath(ext.name))
            except Exception as e:
                print(f"WARNING: An error occurred while building the extension: {e}")

    def _skip_if_up_to_date(self, ext_name: str, anchor_so: Path, sources: "list[str]", force: bool = False) -> bool:
        """Return True (and copy anchor → ext_path) when the extension is up-to-date.

        Both setuptools and cmake extensions share this early-exit pattern:
        compare a stable anchor .so against the list of source files and, if
        nothing is newer, skip the build and serve the cached .so instead.
        """
        if not (self.INCREMENTAL and not force):
            return False
        if not anchor_so.exists() or newer_group(sources, str(anchor_so), missing="newer"):
            return False
        ext_path = Path(self.get_ext_fullpath(ext_name))
        print(f"skipping '{ext_name}' extension (up-to-date)")
        if ext_path != anchor_so:
            ext_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(anchor_so, ext_path)
        return True

    @staticmethod
    def _pyx_source_deps(pyx_path: str) -> "list[str]":
        """Return absolute paths of source-tree .pxd files that pyx_path depends on.

        Cython's dependency tree returns source-tree files as relative paths and
        stdlib headers (in site-packages) as absolute paths.  We keep only the
        relative ones — those live in the source tree and have stable mtimes —
        and resolve them to absolute paths for newer_group().
        """
        dep_tree = _cython_dep_tree()
        return [
            str((HERE / d).resolve())
            for d in dep_tree.all_dependencies(pyx_path)
            if d.endswith(".pxd") and not Path(d).is_absolute() and (HERE / d).exists()
        ]

    def _build_extension_incremental(self, ext: Extension) -> None:
        """Build a regular setuptools Extension with incremental skipping.

        Uses the inplace .so in the source tree as the persistent freshness
        anchor.  pip's build_ext may target a temp build_lib directory (so
        ext_path differs from the inplace path), but the inplace .so survives
        across invocations and is therefore the right thing to compare sources
        against.

        For Cython extensions: cythonize() regenerates the .c file whenever a
        Cython header (.pxd) dependency changes — which happens on every
        `pip install -e .` because pip creates a fresh build env with fresh
        .pxd timestamps.  To avoid spurious rebuilds we compare the .pyx
        source (not the generated .c) against the inplace .so: if the .pyx has
        not changed the output is identical regardless of .c regeneration.
        """
        # Derive the inplace .so path from the extension name.  This is stable
        # across pip invocations regardless of the current build_lib temp dir.
        inplace_so = HERE / self.get_ext_filename(self.get_ext_fullname(ext.name))

        # For Cython extensions ext.sources contains the generated .c file
        # (cythonize() replaces .pyx with .c in-place).  Use the .pyx file
        # as the primary freshness indicator when it exists; its mtime reflects
        # when the actual source last changed, while .c gets regenerated on
        # every pip run due to pip-build-env .pxd dependency timestamps.
        # Also include the source-tree .pxd dependencies so that a .pxd change
        # without a corresponding .pyx change still triggers a rebuild.
        # Cython stdlib .pxd files (in site-packages, returned as absolute
        # paths by the dependency tree) are excluded — they get fresh timestamps
        # on every pip invocation and would defeat the incremental-skip logic.
        sources = []
        for src in ext.sources or []:
            pyx = Path(src).with_suffix(".pyx")
            if pyx.exists():
                sources.append(str(pyx))
                sources.extend(self._pyx_source_deps(str(pyx)))
            else:
                sources.append(src)
        # Header files (C/C++): distutils convention stores them in ext.depends.
        # They are not compiled separately but changes to them must still
        # invalidate the cached .so.
        sources.extend(ext.depends or [])

        if self._skip_if_up_to_date(ext.name, inplace_so, sources):
            return

        super().build_extension(ext)

        # Keep the inplace copy up-to-date so future freshness checks are
        # correct even when this invocation built to a temp build_lib.
        # Only do this for editable/inplace installs.  Wheel builds must NOT
        # leave per-Python-version .so files in the source tree: MANIFEST.in's
        # "graft ddtrace" causes setuptools to include every .so it finds in
        # ddtrace/ when packaging a wheel.  On CI, multiple Python versions are
        # built sequentially on the same persistent runner, so a cpython-39
        # _memalloc.so saved here would end up inside the cpython-310 wheel —
        # but libdd_wrapper.cpython-39-darwin.so is absent from that wheel,
        # causing delocate-wheel to fail with "not found" for the stale rpath.
        if IS_EDITABLE or getattr(self, "inplace", False):
            ext_path = Path(self.get_ext_fullpath(ext.name))
            if ext_path.exists() and ext_path != inplace_so:
                inplace_so.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ext_path, inplace_so)

    @staticmethod
    def _generate_cmake_deps_file(dest_dir: Path) -> None:
        """Render FETCH_CONTENT_DEPS into a CMakeLists.txt under dest_dir.

        The generated file is equivalent to the former static cmake_deps/CMakeLists.txt
        but is derived from the authoritative FETCH_CONTENT_DEPS list in setup.py so
        there is only one place to update when adding or bumping a dependency.
        """
        lines = [
            "cmake_minimum_required(VERSION 3.19)",
            "project(dd_cmake_deps LANGUAGES C CXX)",
            "",
            "set(CMAKE_CXX_STANDARD 17)",
            "set(CMAKE_CXX_STANDARD_REQUIRED ON)",
            "set(CMAKE_POSITION_INDEPENDENT_CODE ON)",
            "",
            "include(FetchContent)",
            'set(FETCHCONTENT_UPDATES_DISCONNECTED ON CACHE BOOL "" FORCE)',
            "",
        ]
        for dep in FETCH_CONTENT_DEPS:
            name = dep["name"]
            for var_name, var_value, var_type in dep.get("cmake_vars", []):
                lines.append(f'set({var_name} {var_value} CACHE {var_type} "" FORCE)')
            lines += [
                "",
                "FetchContent_Declare(",
                f"    {name}",
                f"    GIT_REPOSITORY {dep['repository']}",
                f"    GIT_TAG        {dep['tag']}",
                *(["    GIT_SHALLOW    TRUE"] if dep.get("shallow") else []),
                "    GIT_PROGRESS   TRUE",
                ")",
                f"FetchContent_MakeAvailable({name})",
                "",
            ]
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "CMakeLists.txt").write_text("\n".join(lines))

    def _prebuild_cmake_deps(self) -> Path:
        """Build FETCH_CONTENT_DEPS once as a standalone CMake project.

        Generates a CMakeLists.txt from the FETCH_CONTENT_DEPS list and builds
        shared dependencies (Abseil, etc.) into a platform-tagged install prefix
        under build/.  The prefix is independent of both the extension's cmake
        tree and the Python version, so all CPython builds on the same platform
        share the compiled artifacts.

        cmake-based extensions (_ddup, _stack) receive the install prefix via
        CMAKE_PREFIX_PATH so their FetchContent_MakeAvailable() calls delegate
        to find_package without any change to those CMakeLists files.

        Extensions compiled by setuptools (AbslExtension / MemallocExtension)
        receive the Abseil include dirs and static libs injected at build time
        via AbslExtension.prepare().

        Raises on failure so the build fails fast rather than silently falling
        back to per-extension inline builds.
        """
        platform_tag = sysconfig.get_platform()  # e.g. "macosx-15.4-arm64" – no Python version
        # Anchor to the source tree, not self.build_lib: during `pip install -e .`
        # build_lib points into pip's temp directory and would be thrown away.
        deps_root = HERE / "build" / f"cmake_deps.{platform_tag}.{COMPILE_MODE}"
        deps_build_dir = deps_root / "build"
        deps_install_dir = deps_root / "install"

        # Fast path: all deps already installed from a previous build.
        if all(next(deps_install_dir.glob(f"**/{dep['name']}Config.cmake"), None) for dep in FETCH_CONTENT_DEPS):
            print(f"Reusing pre-built cmake deps from {deps_install_dir}")
            return deps_install_dir

        # Generate the CMakeLists.txt from FETCH_CONTENT_DEPS so the static
        # cmake_deps/ directory in the source tree is no longer needed.
        cmake_src_dir = deps_root / "src"
        self._generate_cmake_deps_file(cmake_src_dir)

        deps_build_dir.mkdir(parents=True, exist_ok=True)

        cmake_command = (Path(cmake.CMAKE_BIN_DIR) / "cmake").resolve()
        # Point FetchContent directly at the shared download cache so that
        # source trees (absl-src/ etc.) are fetched straight there rather than
        # being downloaded to the build dir and copied over afterwards.
        shared_source_cache = LibraryDownload.CACHE_DIR / "_cmake_deps"
        shared_source_cache.mkdir(parents=True, exist_ok=True)
        configure_args = [
            f"-S{cmake_src_dir}",
            f"-B{deps_build_dir}",
            f"-DCMAKE_BUILD_TYPE={COMPILE_MODE}",
            f"-DCMAKE_INSTALL_PREFIX={deps_install_dir}",
            f"-DFETCHCONTENT_BASE_DIR={shared_source_cache}",
        ]
        # If sources are already in the cache, pass them explicitly so cmake
        # skips the network fetch entirely.  Names are derived from directory
        # names ("absl-src" → FETCHCONTENT_SOURCE_DIR_ABSL).
        for src_dir in sorted(shared_source_cache.glob("*-src")):
            dep_name = src_dir.name[:-4].upper()  # "absl-src" → "ABSL"
            configure_args.append(f"-DFETCHCONTENT_SOURCE_DIR_{dep_name}={src_dir}")

        print(f"Pre-building cmake deps into {deps_install_dir}")
        subprocess.run([cmake_command, *configure_args], check=True)

        build_args = ["--build", str(deps_build_dir), "--config", COMPILE_MODE]
        if "CMAKE_BUILD_PARALLEL_LEVEL" not in os.environ:
            build_args += ["--parallel"]
        subprocess.run([cmake_command, *build_args], check=True)

        subprocess.run([cmake_command, "--install", str(deps_build_dir)], check=True)
        return deps_install_dir

    @staticmethod
    def _seed_cmake_source_cache(deps_dir: Path) -> None:
        """Copy newly-downloaded FetchContent source trees to the shared source cache.

        Called after cmake configure so that subsequent extensions (and future
        builds) can reuse the downloaded sources without re-fetching from GitHub.
        Only the ``*-src`` trees are copied; build artifacts stay in the
        per-project ``_deps/`` directory and are never shared.
        """
        if not deps_dir.is_dir():
            return
        shared_cache = LibraryDownload.CACHE_DIR / "_cmake_deps"
        for src_dir in deps_dir.glob("*-src"):
            if not src_dir.is_dir():
                continue
            dest = shared_cache / src_dir.name
            if not dest.exists():
                shared_cache.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src_dir, dest)
                print(f"Cached FetchContent source tree: {src_dir.name}")

    def _get_common_cmake_args(self, source_dir, build_dir, output_dir, extension_name, build_type=None):
        """Get common CMake arguments used by both libdd_wrapper and extensions."""
        # Use base_prefix (not prefix) to get the actual Python installation path even when in a venv
        # Resolve symlinks so CMake can find include/lib directories relative to the real installation
        python_root = Path(sys.base_prefix).resolve()

        cmake_args = [
            f"-S{source_dir}",
            f"-B{build_dir}",
            f"-DPython3_ROOT_DIR={python_root}",
            f"-DPython3_EXECUTABLE={sys.executable}",
            f"-DPYTHON_EXECUTABLE={sys.executable}",
            f"-DCMAKE_BUILD_TYPE={build_type or COMPILE_MODE}",
            f"-DLIB_INSTALL_DIR={output_dir}",
            f"-DEXTENSION_NAME={extension_name}",
            f"-DEXTENSION_SUFFIX={self.suffix}",
            f"-DNATIVE_EXTENSION_LOCATION={self.output_dir}",
            f"-DRUST_GENERATED_HEADERS_DIR={CARGO_TARGET_DIR / 'include'}",
        ]

        # Tell each extension's FetchContent to resolve via find_package instead
        # of downloading and building from source.  FETCHCONTENT_TRY_FIND_PACKAGE_MODE=ALWAYS
        # (cmake 3.24+) makes FetchContent_MakeAvailable() transparently delegate
        # to find_package when the package is available on CMAKE_PREFIX_PATH,
        # without requiring any change to the extension CMakeLists.txt files.
        # This is the mechanism that allows sharing the compiled dep artifacts
        # across all extensions and Python versions in the same build tree.
        cmake_args += [
            f"-DCMAKE_PREFIX_PATH={self._cmake_deps_install_dir}",
            "-DFETCHCONTENT_TRY_FIND_PACKAGE_MODE=ALWAYS",
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
        # Define the build and output directories.
        # Anchor cmake_build_dir to the source tree (not self.build_lib) so
        # that cmake's object files and built .so survive across `pip install
        # -e .` invocations.  pip creates a fresh temp dir each run, so
        # deriving from build_lib would force a full cmake rebuild every time.
        output_dir = Path(self.get_ext_fullpath(ext.name)).parent.resolve()
        extension_basename = Path(self.get_ext_fullpath(ext.name)).name
        plat = f"{sysconfig.get_platform()}-cpython-{sys.version_info.major}{sys.version_info.minor}"
        cmake_build_dir = HERE / "build" / f"cmake.{plat}" / ext.name
        cmake_build_dir.mkdir(parents=True, exist_ok=True)

        if IS_EDITABLE:
            # Use the .so inside the cmake build dir as the freshness anchor.
            # The cmake build dir is anchored to the source tree so it persists
            # across pip invocations; using the inplace source-tree copy would
            # also work but the cmake build dir copy is more reliably present.
            cmake_so = cmake_build_dir / Path(self.get_ext_fullpath(ext.name)).name

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

            sources = [str(s.resolve()) for s in ext.get_sources(self)] + dependencies
            if self._skip_if_up_to_date(ext.name, cmake_so, sources, force=force):
                return
            print(f"building '{ext.name}' CMake extension")

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
        # After a successful configure, seed the shared source cache with any
        # FetchContent sources that were just downloaded, so subsequent
        # extensions can skip the download step.
        self._seed_cmake_source_cache(cmake_build_dir / "_deps")
        subprocess.run([cmake_command, "--build", ".", *build_args], cwd=cmake_build_dir, check=True)
        subprocess.run([cmake_command, "--install", ".", *install_args], cwd=cmake_build_dir, check=True)


class DebugMetadata:
    start_ns = 0
    enabled = "_DD_DEBUG_EXT" in os.environ
    metadata_file = os.getenv("_DD_DEBUG_EXT_FILE", "debug_ext_metadata.txt")
    build_times: "dict[t.Any, int]" = {}
    download_times: "dict[str, int]" = {}

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
            f.write("Extension build times:\n")
            f.write(f"\tTotal: {build_total_s:0.2f}s ({build_percent:0.2f}%)\n")
            for ext, elapsed_ns in sorted(cls.build_times.items(), key=lambda x: x[1], reverse=True):
                elapsed_s = elapsed_ns / 1e9
                ext_percent = (elapsed_ns / total_ns) * 100.0
                f.write(f"\t{ext.name}: {elapsed_s:0.2f}s ({ext_percent:0.2f}%)\n")

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


if DebugMetadata.enabled:
    DebugMetadata.start_ns = time.time_ns()
    setattr(CustomBuildExt, "build_extension", debug_build_extension(CustomBuildExt.build_extension))
    setattr(build_rust, "build_extension", debug_build_extension(CustomBuildRust.build_extension))
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

    def get_sources(self, cmd: build_ext) -> list[Path]:
        """
        Returns the list of source files for this extension.
        This is used by the CustomBuildExt class to determine if the extension needs to be rebuilt.
        """
        full_path = Path(cmd.get_ext_fullpath(self.name))

        # Collect all the source files within the source directory. We exclude
        # Python sources and anything that does not have a suffix (most likely
        # a binary file), or that has the same name as the extension binary.
        def is_valid_source(src: Path) -> bool:
            return bool(
                src.is_file()
                and src.name != full_path.name
                and src.suffix
                and src.suffix not in {".py", ".pyc", ".pyi"}
            )

        return [
            src
            for source_dir in chain([self.source_dir], self.extra_source_dirs)
            for src in Path(source_dir).glob("**/*")
            if is_valid_source(src)
        ]


class AbslExtension(Extension):
    """C++ Extension that links against pre-built Abseil static libraries.

    Call prepare(build_cmd) just before compilation to inject include dirs and
    link args once the cmake_deps install prefix is known.
    """

    def prepare(self, build_cmd: "CustomBuildExt") -> None:
        install_dir: Path = build_cmd._cmake_deps_install_dir
        absl_includes, absl_link_args = self._absl_link_args(install_dir)
        self.include_dirs = list(self.include_dirs or []) + absl_includes
        self.extra_link_args = list(self.extra_link_args or []) + absl_link_args

    @staticmethod
    def _absl_link_args(install_dir: Path) -> "tuple[list[str], list[str]]":
        include_dirs = [str(install_dir / "include")]
        # Some Linux distros install under lib64; fall back gracefully.
        lib_dir = install_dir / "lib"
        if not lib_dir.is_dir():
            lib_dir = install_dir / "lib64"
        absl_libs = sorted(lib_dir.glob("libabsl_*.a"))
        if not absl_libs:
            return include_dirs, []
        lib_paths = [str(lib) for lib in absl_libs]
        if sys.platform == "darwin":
            extra_link_args = lib_paths
        else:
            # Linux: Abseil static libs have circular references; group them.
            extra_link_args = ["-Wl,--start-group"] + lib_paths + ["-Wl,--end-group"]
        return include_dirs, extra_link_args


class MemallocExtension(AbslExtension):
    """memalloc profiling extension; also links against libdd_wrapper."""

    def prepare(self, build_cmd: "CustomBuildExt") -> None:
        super().prepare(build_cmd)
        # Rust-generated C headers (produced by build_rust())
        self.include_dirs = list(self.include_dirs or []) + [str(CARGO_TARGET_DIR / "include")]
        # Locate the already-built libdd_wrapper shared library.
        if IS_EDITABLE or getattr(build_cmd, "inplace", False):
            profiling_dir = Path(__file__).parent / "ddtrace" / "internal" / "datadog" / "profiling"
        else:
            profiling_dir = (
                Path(__file__).parent / Path(build_cmd.build_lib) / "ddtrace" / "internal" / "datadog" / "profiling"
            )
        wrapper_name = f"libdd_wrapper{build_cmd.suffix}"
        wrapper_lib = profiling_dir / wrapper_name
        if wrapper_lib.exists():
            self.extra_link_args = list(self.extra_link_args or []) + [str(wrapper_lib)]
        else:
            print(f"WARNING: libdd_wrapper not found at {wrapper_lib}; _memalloc may not link correctly")


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
                "ddtrace.appsec._iast._stacktrace",
                sources=[
                    "ddtrace/appsec/_iast/_stacktrace.c",
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
        _iast_sources: list[str] = []
        _iast_depends: list[str] = []
        for _pattern in [
            "*.cpp",
            "api/*.cpp",
            "context/*.cpp",
            "aspects/*.cpp",
            "initializer/*.cpp",
            "tainted_ops/*.cpp",
            "taint_tracking/*.cpp",
            "utils/*.cpp",
        ]:
            _iast_sources.extend(str(p.relative_to(HERE)) for p in IAST_DIR.glob(_pattern))
        for _pattern in [
            "*.h",
            "api/*.h",
            "context/*.h",
            "aspects/*.h",
            "initializer/*.h",
            "tainted_ops/*.h",
            "taint_tracking/*.h",
            "utils/*.h",
        ]:
            _iast_depends.extend(str(p.relative_to(HERE)) for p in IAST_DIR.glob(_pattern))
        ext_modules.append(
            AbslExtension(
                "ddtrace.appsec._iast._taint_tracking._native",
                sources=_iast_sources,
                depends=_iast_depends,
                include_dirs=[
                    str(IAST_DIR.relative_to(HERE)),
                    str((IAST_DIR / "_vendor" / "pybind11" / "include").relative_to(HERE)),
                ],
                extra_compile_args=[
                    "-std=c++17",
                    "-fPIC",
                    "-fexceptions",
                    "-fvisibility=hidden",
                    "-fpermissive",
                    "-pthread",
                    "-Wall",
                    "-Wno-unknown-pragmas",
                    "-U_FORTIFY_SOURCE",
                    "-g",
                ],
            )
        )

    if CURRENT_OS in ("Linux", "Darwin") and is_64_bit_python():
        MEMALLOC_DIR = HERE / "ddtrace" / "profiling" / "collector"
        DD_WRAPPER_INCLUDE = DDUP_DIR.parent / "dd_wrapper" / "include"

        _memalloc_defines: list[tuple[str, "t.Optional[str]"]] = [("_POSIX_C_SOURCE", "200809L")]
        if CURRENT_OS == "Darwin":
            _memalloc_defines.append(("_DARWIN_C_SOURCE", None))
        if COMPILE_MODE.lower() not in ("debug",):
            _memalloc_defines.append(("NDEBUG", None))
        if os.environ.get("DD_PROFILING_MEMALLOC_ASSERT_ON_REENTRY", "0") not in ("0", ""):
            _memalloc_defines.append(("MEMALLOC_ASSERT_ON_REENTRY", None))

        _memalloc_link_args: list[str] = []
        if CURRENT_OS == "Darwin":
            _memalloc_link_args += ["-Wl,-rpath,@loader_path/../../internal/datadog/profiling"]
        elif CURRENT_OS == "Linux":
            _memalloc_link_args += ["-Wl,-rpath,$ORIGIN/../../internal/datadog/profiling"]

        ext_modules.append(
            MemallocExtension(
                "ddtrace.profiling.collector._memalloc",
                sources=[
                    str((MEMALLOC_DIR / "_memalloc.cpp").relative_to(HERE)),
                    str((MEMALLOC_DIR / "_memalloc_tb.cpp").relative_to(HERE)),
                    str((MEMALLOC_DIR / "_memalloc_heap.cpp").relative_to(HERE)),
                    str((MEMALLOC_DIR / "_memalloc_reentrant.cpp").relative_to(HERE)),
                ],
                depends=[
                    str(p.relative_to(HERE))
                    for p in sorted(MEMALLOC_DIR.glob("*.h")) + sorted(DD_WRAPPER_INCLUDE.rglob("*.h"))
                ],
                include_dirs=[
                    str(MEMALLOC_DIR.relative_to(HERE)),
                    str(DD_WRAPPER_INCLUDE.relative_to(HERE)),
                    # CARGO_TARGET_DIR/include injected at build time
                ],
                extra_compile_args=[
                    "-std=c++20",
                    "-fPIC",
                    "-fvisibility=hidden",
                    "-pthread",
                    "-Wall",
                    "-Wextra",
                ],
                define_macros=_memalloc_defines,
                extra_link_args=_memalloc_link_args,
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

interpose_sccache()
setup(
    name="ddtrace",
    packages=find_packages(exclude=["tests*", "benchmarks*", "scripts*"]),
    package_data={
        "ddtrace": ["py.typed"],
        "ddtrace.appsec": ["rules.json"],
        "ddtrace.appsec._ddwaf": ["libddwaf/*/lib/libddwaf.*"],
        "ddtrace.internal.datadog.profiling": (
            ["libdd_wrapper*.*"]
            + (["ddtrace/internal/datadog/profiling/test/*"] if BUILD_PROFILING_NATIVE_TESTS else [])
        ),
    },
    exclude_package_data={
        "": ["CMakeLists.txt", "*.md", "*.sh", "*.cmake", "*.pxd"],
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
