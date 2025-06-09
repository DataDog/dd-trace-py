import atexit
import fnmatch
import hashlib
import os
from os.path import exists, isfile
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import time
import warnings

import cmake
from setuptools_rust import Binding
from setuptools_rust import RustExtension
from setuptools_rust import build_rust


from setuptools import Extension, find_packages, setup  # isort: skip
from setuptools.command.build_ext import build_ext  # isort: skip
from setuptools.command.build_py import build_py as BuildPyCommand  # isort: skip
from pathlib import Path  # isort: skip
from pkg_resources import get_build_platform  # isort: skip
from distutils.command.clean import clean as CleanCommand  # isort: skip


try:
    # ORDER MATTERS
    # Import this after setuptools or it will fail
    from Cython.Build import cythonize  # noqa: I100
    import Cython.Distutils
except ImportError:
    raise ImportError(
        "Failed to import Cython modules. This can happen under versions of pip older than 18 that don't "
        "support installing build requirements during setup. If you're using pip, make sure it's a "
        "version >=18.\nSee the quickstart documentation for more information:\n"
        "https://ddtrace.readthedocs.io/en/stable/installation_quickstart.html"
    )

from urllib.error import HTTPError
from urllib.request import urlretrieve

# workaround for ModuleNotFound.
sys.path.insert(0, str(Path(__file__).parent.resolve()))

from build_libnative import build_crate, clean_crate


HERE = Path(__file__).resolve().parent

COMPILE_MODE = "Release"
if "DD_COMPILE_DEBUG" in os.environ:
    warnings.warn(
        "The DD_COMPILE_DEBUG environment variable is deprecated and will be deleted, "
        "use DD_COMPILE_MODE=Debug|Release|RelWithDebInfo|MinSizeRel.",
    )
    COMPILE_MODE = "Debug"
else:
    COMPILE_MODE = os.environ.get("DD_COMPILE_MODE", "Release")

FAST_BUILD = os.getenv("DD_FAST_BUILD", "false").lower() in ("1", "yes", "on", "true")
if FAST_BUILD:
    print("WARNING: DD_FAST_BUILD is enabled, some optimizations will be disabled")
else:
    print("INFO: DD_FAST_BUILD not enabled")

if FAST_BUILD:
    os.environ["DD_COMPILE_ABSEIL"] = "0"

SCCACHE_COMPILE = os.getenv("DD_USE_SCCACHE", "0").lower() in ("1", "yes", "on", "true")

IS_PYSTON = hasattr(sys, "pyston_version_info")
IS_EDITABLE = False  # Set to True if the package is being installed in editable mode

LIBDDWAF_DOWNLOAD_DIR = HERE / "ddtrace" / "appsec" / "_ddwaf" / "libddwaf"
IAST_DIR = HERE / "ddtrace" / "appsec" / "_iast" / "_taint_tracking"
DDUP_DIR = HERE / "ddtrace" / "internal" / "datadog" / "profiling" / "ddup"
CRASHTRACKER_DIR = HERE / "ddtrace" / "internal" / "datadog" / "profiling" / "crashtracker"
STACK_V2_DIR = HERE / "ddtrace" / "internal" / "datadog" / "profiling" / "stack_v2"
NATIVE_CRATE = HERE / "src" / "native"

BUILD_PROFILING_NATIVE_TESTS = os.getenv("DD_PROFILING_NATIVE_TESTS", "0").lower() in ("1", "yes", "on", "true")

CURRENT_OS = platform.system()

LIBDDWAF_VERSION = "1.25.1"

# DEV: update this accordingly when src/native upgrades libdatadog dependency.
# libdatadog v15.0.0 requires rust 1.78.
RUST_MINIMUM_VERSION = "1.78"


def interpose_sccache():
    """
    Injects sccache into the relevant build commands if it's allowed and we think it'll work
    """
    if not SCCACHE_COMPILE:
        return

    # Check for sccache.  We don't do multi-step failover (e.g., if ${SCCACHE_PATH} is set, but the binary is invalid)
    sccache_path = Path(os.getenv("SCCACHE_PATH", shutil.which("sccache")))
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


def verify_checksum_from_file(sha256_filename, filename):
    # sha256 File format is ``checksum`` followed by two whitespaces, then ``filename`` then ``\n``
    expected_checksum, expected_filename = list(filter(None, open(sha256_filename, "r").read().strip().split(" ")))
    actual_checksum = hashlib.sha256(open(filename, "rb").read()).hexdigest()
    try:
        assert expected_filename.endswith(filename)
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
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def is_64_bit_python():
    return sys.maxsize > (1 << 32)


class LibraryDownload:
    name = None
    download_dir = None
    version = None
    url_root = None
    available_releases = None
    expected_checksums = None
    translate_suffix = None

    @classmethod
    def download_artifacts(cls):
        suffixes = cls.translate_suffix[CURRENT_OS]
        download_dir = Path(cls.download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)  # No need to check if it exists

        # If the directory is nonempty, assume we're done
        if any(download_dir.iterdir()):
            return

        for arch in cls.available_releases[CURRENT_OS]:
            if CURRENT_OS == "Linux" and not get_build_platform().endswith(arch):
                # We cannot include the dynamic libraries for other architectures here.
                continue
            elif CURRENT_OS == "Darwin":
                # Detect build type for macos:
                # https://github.com/pypa/cibuildwheel/blob/main/cibuildwheel/macos.py#L250
                target_platform = os.getenv("PLAT")
                # Darwin Universal2 should bundle both architectures
                if not target_platform.endswith(("universal2", arch)):
                    continue
            elif CURRENT_OS == "Windows" and (not is_64_bit_python() != arch.endswith("32")):
                # Win32 can be built on a 64-bit machine so build_platform may not be relevant
                continue

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

            try:
                filename, http_response = urlretrieve(download_address, archive_name)
            except HTTPError as e:
                print("No archive found for dynamic library {}: {}".format(cls.name, archive_dir))
                raise e

            # Verify checksum of downloaded file
            if cls.expected_checksums is None:
                sha256_address = download_address + ".sha256"
                sha256_filename, http_response = urlretrieve(sha256_address, archive_name + ".sha256")
                verify_checksum_from_file(sha256_filename, filename)
            else:
                expected_checksum = cls.expected_checksums[CURRENT_OS][arch]
                verify_checksum_from_hash(expected_checksum, filename)

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

            Path(filename).unlink()

    @classmethod
    def run(cls):
        cls.download_artifacts()

    @classmethod
    def get_archive_name(cls, arch, os):
        return cls.get_package_name(arch, os) + ".tar.gz"


class LibDDWafDownload(LibraryDownload):
    name = "ddwaf"
    download_dir = LIBDDWAF_DOWNLOAD_DIR
    version = LIBDDWAF_VERSION
    url_root = "https://github.com/DataDog/libddwaf/releases/download"
    available_releases = {
        "Windows": ["win32", "x64"],
        "Darwin": ["arm64", "x86_64"],
        "Linux": ["aarch64", "x86_64"],
    }
    translate_suffix = {"Windows": (".dll",), "Darwin": (".dylib",), "Linux": (".so",)}

    @classmethod
    def get_package_name(cls, arch, opsys):
        archive_dir = "lib%s-%s-%s-%s" % (cls.name, cls.version, opsys.lower(), arch)
        return archive_dir

    @classmethod
    def get_archive_name(cls, arch, opsys):
        os_name = opsys.lower()
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
    def remove_artifacts():
        shutil.rmtree(LIBDDWAF_DOWNLOAD_DIR, True)
        shutil.rmtree(IAST_DIR / "*.so", True)

    @staticmethod
    def remove_rust():
        clean_crate(NATIVE_CRATE)

    def run(self):
        CleanLibraries.remove_rust()
        CleanLibraries.remove_artifacts()
        CleanCommand.run(self)


class CustomBuildExt(build_ext):
    def run(self):
        self.build_rust()
        super().run()
        for ext in self.extensions:
            self.build_extension(ext)

    def build_rust(self):

        build_crate(NATIVE_CRATE, True, native_features)

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
            super().build_extension(ext)

        if COMPILE_MODE.lower() in ("release", "minsizerel"):
            try:
                self.try_strip_symbols(self.get_ext_fullpath(ext.name))
            except Exception as e:
                print(f"WARNING: An error occurred while building the extension: {e}")

    def build_extension_cmake(self, ext):
        # Define the build and output directories
        output_dir = Path(self.get_ext_fullpath(ext.name)).parent.resolve()
        extension_basename = Path(self.get_ext_fullpath(ext.name)).name

        # We derive the cmake build directory from the output directory, but put it in
        # a sibling directory to avoid polluting the final package
        cmake_build_dir = Path(self.build_lib.replace("lib.", "cmake."), ext.name).resolve()
        cmake_build_dir.mkdir(parents=True, exist_ok=True)

        # Which commands are passed to _every_ cmake invocation
        cmake_args = ext.cmake_args or []
        cmake_args += [
            "-S{}".format(ext.source_dir),  # cmake>=3.13
            "-B{}".format(cmake_build_dir),  # cmake>=3.13
            "-DPython3_ROOT_DIR={}".format(sysconfig.get_config_var("prefix")),
            "-DPYTHON_EXECUTABLE={}".format(sys.executable),
            "-DCMAKE_BUILD_TYPE={}".format(ext.build_type),
            "-DLIB_INSTALL_DIR={}".format(output_dir),
            "-DEXTENSION_NAME={}".format(extension_basename),
        ]

        if BUILD_PROFILING_NATIVE_TESTS:
            cmake_args += ["-DBUILD_TESTING=ON"]

        # If it's been enabled, also propagate sccache to the CMake build.  We have to manually set the default CC/CXX
        # compilers here, because otherwise the way we wrap sccache will conflict with the CMake wrappers
        sccache_path = os.getenv("DD_SCCACHE_PATH")
        if sccache_path:
            cmake_args += [
                "-DCMAKE_C_COMPILER={}".format(os.getenv("DD_CC_OLD", shutil.which("cc"))),
                "-DCMAKE_C_COMPILER_LAUNCHER={}".format(sccache_path),
                "-DCMAKE_CXX_COMPILER={}".format(os.getenv("DD_CXX_OLD", shutil.which("c++"))),
                "-DCMAKE_CXX_COMPILER_LAUNCHER={}".format(sccache_path),
            ]

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
            cmake_args += [
                "-A{}".format("x64" if platform.architecture()[0] == "64bit" else "Win32"),
            ]
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
    build_times = {}

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
            f.write(f"\tCARGO_BUILD_JOBS: {os.getenv('CARGO_BUILD_JOBS', 'unset')}\n")
            f.write(f"\tCMAKE_BUILD_PARALLEL_LEVEL: {os.getenv('CMAKE_BUILD_PARALLEL_LEVEL', 'unset')}\n")
            f.write(f"\tDD_COMPILE_MODE: {COMPILE_MODE}\n")
            f.write(f"\tDD_USE_SCCACHE: {SCCACHE_COMPILE}\n")
            f.write(f"\tDD_FAST_BUILD: {FAST_BUILD}\n")
            f.write("Extension build times:\n")
            f.write(f"\tTotal: {build_total_s:0.2f}s ({build_percent:0.2f}%)\n")
            for ext, elapsed_ns in sorted(cls.build_times.items(), key=lambda x: x[1], reverse=True):
                elapsed_s = elapsed_ns / 1e9
                ext_percent = (elapsed_ns / total_ns) * 100.0
                f.write(f"\t{ext.name}: {elapsed_s:0.2f}s ({ext_percent:0.2f}%)\n")


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
    CMakeBuild.build_extension = debug_build_extension(CMakeBuild.build_extension)
    build_rust.build_extension = debug_build_extension(build_rust.build_extension)
    atexit.register(DebugMetadata.dump_metadata)


class CMakeExtension(Extension):
    def __init__(
        self,
        name,
        source_dir=".",
        cmake_args=[],
        build_args=[],
        install_args=[],
        build_type=None,
        optional=True,  # By default, extensions are optional
    ):
        super().__init__(name, sources=[])
        self.source_dir = source_dir
        self.cmake_args = cmake_args or []
        self.build_args = build_args or []
        self.install_args = install_args or []
        self.build_type = build_type or COMPILE_MODE
        self.optional = optional  # If True, cmake errors are ignored


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


DD_BUILD_EXT_INCLUDES = [_.strip() for _ in os.getenv("DD_BUILD_EXT_INCLUDES", "").split(",") if _.strip()]
DD_BUILD_EXT_EXCLUDES = [_.strip() for _ in os.getenv("DD_BUILD_EXT_EXCLUDES", "").split(",") if _.strip()]


def filter_extensions(extensions):
    # type: (list[Extension]) -> list[Extension]
    if not DD_BUILD_EXT_INCLUDES and not DD_BUILD_EXT_EXCLUDES:
        return extensions

    filtered: list[Extension] = []
    for ext in extensions:
        if DD_BUILD_EXT_EXCLUDES and any(fnmatch.fnmatch(ext.name, pattern) for pattern in DD_BUILD_EXT_EXCLUDES):
            print(f"INFO: Excluding extension {ext.name}")
            continue
        elif DD_BUILD_EXT_INCLUDES and not any(fnmatch.fnmatch(ext.name, pattern) for pattern in DD_BUILD_EXT_INCLUDES):
            print(f"INFO: Excluding extension {ext.name}")
            continue
        print(f"INFO: Including extension {ext.name}")
        filtered.append(ext)
    return filtered


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


if sys.byteorder == "big":
    encoding_macros = [("__BIG_ENDIAN__", "1")]
else:
    encoding_macros = [("__LITTLE_ENDIAN__", "1")]


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
    native_features = []
    ext_modules = [
        Extension(
            "ddtrace.profiling.collector._memalloc",
            sources=[
                "ddtrace/profiling/collector/_memalloc.c",
                "ddtrace/profiling/collector/_memalloc_tb.c",
                "ddtrace/profiling/collector/_memalloc_heap.c",
                "ddtrace/profiling/collector/_memalloc_reentrant.c",
                "ddtrace/profiling/collector/_memalloc_heap_map.c",
            ],
            extra_compile_args=(
                debug_compile_args
                # If NDEBUG is set, assert statements are compiled out. Make
                # sure we explicitly set this for normal builds, and explicitly
                # _unset_ it for debug builds in case the CFLAGS from sysconfig
                # include -DNDEBUG
                + (["-DNDEBUG"] if not debug_compile_args else ["-UNDEBUG"])
                + ["-D_POSIX_C_SOURCE=200809L", "-std=c11"]
                + fast_build_args
                if CURRENT_OS != "Windows"
                else ["/std:c11", "/experimental:c11atomics"]
            ),
        ),
        Extension(
            "ddtrace.internal._threads",
            sources=["ddtrace/internal/_threads.cpp"],
            extra_compile_args=(
                ["-std=c++17", "-Wall", "-Wextra"] + fast_build_args
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
        ext_modules.append(
            CMakeExtension("ddtrace.appsec._iast._taint_tracking._native", source_dir=IAST_DIR, optional=False)
        )

    if (CURRENT_OS in ("Linux", "Darwin") and is_64_bit_python()) or CURRENT_OS == "Windows":
        native_features.append("profiling")
        ext_modules.append(
            CMakeExtension(
                "ddtrace.internal.datadog.profiling.ddup._ddup",
                source_dir=DDUP_DIR,
                optional=False,
            )
        )

    if CURRENT_OS in ("Linux", "Darwin") and is_64_bit_python():
        ext_modules.append(
            CMakeExtension(
                "ddtrace.internal.datadog.profiling.crashtracker._crashtracker",
                source_dir=CRASHTRACKER_DIR,
                optional=False,
            )
        )

        ext_modules.append(
            CMakeExtension(
                "ddtrace.internal.datadog.profiling.stack_v2._stack_v2",
                source_dir=STACK_V2_DIR,
                optional=False,
            ),
        )


else:
    ext_modules = []
    native_feautes = []

interpose_sccache()
setup(
    name="ddtrace",
    packages=find_packages(exclude=["tests*", "benchmarks*", "scripts*"]),
    package_data={
        "ddtrace": ["py.typed"],
        "ddtrace.appsec": ["rules.json"],
        "ddtrace.appsec._ddwaf": ["libddwaf/*/lib/libddwaf.*"],
        "ddtrace.appsec._iast._taint_tracking": ["CMakeLists.txt"],
        "ddtrace.internal.datadog.profiling": (
            ["libdd_wrapper*.*"] + ["ddtrace/internal/datadog/profiling/test/*"] if BUILD_PROFILING_NATIVE_TESTS else []
        ),
        "ddtrace.internal.datadog.profiling.crashtracker": ["crashtracker_exe*"],
    },
    zip_safe=False,
    # enum34 is an enum backport for earlier versions of python
    # funcsigs backport required for vendored debtcollector
    cmdclass={
        "build_ext": CustomBuildExt,
        "build_py": LibraryDownloader,
        "build_rust": build_rust,
        "clean": CleanLibraries,
    },
    setup_requires=["setuptools_scm[toml]>=4", "cython", "cmake>=3.24.2,<3.28", "setuptools-rust"],
    ext_modules=filter_extensions(ext_modules)
    + cythonize(
        filter_extensions(
            [
                Cython.Distutils.Extension(
                    "ddtrace.internal._rand",
                    sources=["ddtrace/internal/_rand.pyx"],
                    language="c",
                ),
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
                    define_macros=encoding_macros,
                ),
                Extension(
                    "ddtrace.internal.telemetry.metrics_namespaces",
                    ["ddtrace/internal/telemetry/metrics_namespaces.pyx"],
                    language="c",
                ),
                Cython.Distutils.Extension(
                    "ddtrace.profiling.collector.stack",
                    sources=["ddtrace/profiling/collector/stack.pyx"],
                    language="c",
                    # cython generated code errors on build in toolchains that are strict about int->ptr conversion
                    # OTOH, the MSVC toolchain is different.  In a perfect world we'd deduce the underlying
                    # toolchain and emit the right flags, but as a compromise we assume Windows implies MSVC and
                    # everything else is on a GNU-like toolchain
                    extra_compile_args=extra_compile_args
                    + (["-Wno-int-conversion"] if CURRENT_OS != "Windows" else []),
                ),
                Cython.Distutils.Extension(
                    "ddtrace.profiling.collector._traceback",
                    sources=["ddtrace/profiling/collector/_traceback.pyx"],
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
            ]
        ),
        compile_time_env={
            "PY_MAJOR_VERSION": sys.version_info.major,
            "PY_MINOR_VERSION": sys.version_info.minor,
            "PY_MICRO_VERSION": sys.version_info.micro,
            "PY_VERSION_HEX": sys.hexversion,
        },
        force=True,
        annotate=os.getenv("_DD_CYTHON_ANNOTATE") == "1",
        compiler_directives={"language_level": "3"},
    )
    + filter_extensions(get_exts_for("psutil")),
    # rust_extensions=filter_extensions(
    #     [
    #         RustExtension(
    #             "ddtrace.internal.native._native",
    #             path="src/native/Cargo.toml",
    #             py_limited_api="auto",
    #             binding=Binding.PyO3,
    #             debug=os.getenv("_DD_RUSTC_DEBUG") == "1",
    #         ),
    #     ]
    # ),
)
