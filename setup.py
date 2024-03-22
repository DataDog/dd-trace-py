import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile

import cmake


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


HERE = Path(__file__).resolve().parent

DEBUG_COMPILE = "DD_COMPILE_DEBUG" in os.environ

# stack_v2 profiling extensions are optional, unless they are made explicitly required by this environment variable
STACK_V2_REQUIRED = "DD_STACK_V2_REQUIRED" in os.environ

IS_PYSTON = hasattr(sys, "pyston_version_info")

LIBDDWAF_DOWNLOAD_DIR = HERE / "ddtrace" / "appsec" / "_ddwaf" / "libddwaf"
IAST_DIR = HERE / "ddtrace" / "appsec" / "_iast" / "_taint_tracking"
DDUP_DIR = HERE / "ddtrace" / "internal" / "datadog" / "profiling" / "ddup"
STACK_V2_DIR = HERE / "ddtrace" / "internal" / "datadog" / "profiling" / "stack_v2"

CURRENT_OS = platform.system()

LIBDDWAF_VERSION = "1.16.0"

# Set macOS SDK default deployment target to 10.14 for C++17 support (if unset, may default to 10.9)
if CURRENT_OS == "Darwin":
    os.environ.setdefault("MACOSX_DEPLOYMENT_TARGET", "10.14")


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
        CleanLibraries.remove_artifacts()
        LibDDWafDownload.run()
        BuildPyCommand.run(self)


class CleanLibraries(CleanCommand):
    @staticmethod
    def remove_artifacts():
        shutil.rmtree(LIBDDWAF_DOWNLOAD_DIR, True)
        shutil.rmtree(IAST_DIR / "*.so", True)

    def run(self):
        CleanLibraries.remove_artifacts()
        CleanCommand.run(self)


class CMakeBuild(build_ext):
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

        if not DEBUG_COMPILE:
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

        # Get development paths
        python_include = sysconfig.get_paths()["include"]
        python_lib = sysconfig.get_config_var("LIBDIR")

        # Which commands are passed to _every_ cmake invocation
        cmake_args = ext.cmake_args or []
        cmake_args += [
            "-S{}".format(ext.source_dir),  # cmake>=3.13
            "-B{}".format(cmake_build_dir),  # cmake>=3.13
            "-DPython3_INCLUDE_DIRS={}".format(python_include),
            "-DPython3_LIBRARIES={}".format(python_lib),
            "-DPYTHON_EXECUTABLE={}".format(sys.executable),
            "-DCMAKE_BUILD_TYPE={}".format(ext.build_type),
            "-DLIB_INSTALL_DIR={}".format(output_dir),
            "-DEXTENSION_NAME={}".format(extension_basename),
        ]

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
        if CURRENT_OS == "Darwin" and sys.version_info >= (3, 8, 0):
            # Cross-compile support for macOS - respect ARCHFLAGS if set
            # Darwin Universal2 should bundle both architectures
            # This is currently specific to IAST and requires cmakefile support
            archs = re.findall(r"-arch (\S+)", os.environ.get("ARCHFLAGS", ""))
            if archs:
                cmake_args += [
                    "-DBUILD_MACOS=ON",
                    "-DCMAKE_OSX_ARCHITECTURES={}".format(";".join(archs)),
                ]

        cmake_command = (
            Path(cmake.CMAKE_BIN_DIR) / "cmake"
        ).resolve()  # explicitly use the cmake provided by the cmake package
        subprocess.run([cmake_command, *cmake_args], cwd=cmake_build_dir, check=True)
        subprocess.run([cmake_command, "--build", ".", *build_args], cwd=cmake_build_dir, check=True)
        subprocess.run([cmake_command, "--install", ".", *install_args], cwd=cmake_build_dir, check=True)


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
        self.build_type = build_type or "Debug" if DEBUG_COMPILE else "Release"
        self.optional = optional  # If True, cmake errors are ignored


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
else:
    linux = CURRENT_OS == "Linux"
    encoding_libraries = []
    extra_compile_args = ["-DPy_BUILD_CORE"]
    if DEBUG_COMPILE:
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
    ext_modules = [
        Extension(
            "ddtrace.profiling.collector._memalloc",
            sources=[
                "ddtrace/profiling/collector/_memalloc.c",
                "ddtrace/profiling/collector/_memalloc_tb.c",
                "ddtrace/profiling/collector/_memalloc_heap.c",
            ],
            extra_compile_args=debug_compile_args,
        ),
    ]
    if platform.system() not in ("Windows", ""):
        ext_modules.append(
            Extension(
                "ddtrace.appsec._iast._stacktrace",
                # Sort source files for reproducibility
                sources=[
                    "ddtrace/appsec/_iast/_stacktrace.c",
                ],
                extra_compile_args=debug_compile_args,
            )
        )

        ext_modules.append(CMakeExtension("ddtrace.appsec._iast._taint_tracking._native", source_dir=IAST_DIR))

    if platform.system() == "Linux" and is_64_bit_python():
        ext_modules.append(
            CMakeExtension(
                "ddtrace.internal.datadog.profiling.ddup._ddup",
                source_dir=DDUP_DIR,
                cmake_args=[
                    "-DPY_MAJOR_VERSION={}".format(sys.version_info.major),
                    "-DPY_MINOR_VERSION={}".format(sys.version_info.minor),
                    "-DPY_MICRO_VERSION={}".format(sys.version_info.micro),
                ],
                optional=not STACK_V2_REQUIRED,
            )
        )

        # Echion doesn't build on 3.7, so just skip it outright for now
        if sys.version_info >= (3, 8):
            ext_modules.append(
                CMakeExtension(
                    "ddtrace.internal.datadog.profiling.stack_v2._stack_v2",
                    source_dir=STACK_V2_DIR,
                    optional=not STACK_V2_REQUIRED,
                ),
            )

else:
    ext_modules = []


setup(
    name="ddtrace",
    packages=find_packages(exclude=["tests*", "benchmarks*"]),
    package_data={
        "ddtrace": ["py.typed"],
        "ddtrace.appsec": ["rules.json"],
        "ddtrace.appsec._ddwaf": ["libddwaf/*/lib/libddwaf.*"],
        "ddtrace.appsec._iast._taint_tracking": ["CMakeLists.txt"],
        "ddtrace.internal.datadog.profiling": ["libdd_wrapper.*"],
    },
    zip_safe=False,
    # enum34 is an enum backport for earlier versions of python
    # funcsigs backport required for vendored debtcollector
    cmdclass={
        "build_ext": CMakeBuild,
        "build_py": LibraryDownloader,
        "clean": CleanLibraries,
    },
    setup_requires=["setuptools_scm[toml]>=4", "cython", "cmake>=3.24.2,<3.28"],
    ext_modules=ext_modules
    + cythonize(
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
            Cython.Distutils.Extension(
                "ddtrace.profiling.collector.stack",
                sources=["ddtrace/profiling/collector/stack.pyx"],
                language="c",
                extra_compile_args=extra_compile_args,
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
            Cython.Distutils.Extension(
                "ddtrace.profiling.exporter.pprof",
                sources=["ddtrace/profiling/exporter/pprof.pyx"],
                language="c",
            ),
            Cython.Distutils.Extension(
                "ddtrace.profiling._build",
                sources=["ddtrace/profiling/_build.pyx"],
                language="c",
            ),
        ],
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
    + get_exts_for("wrapt")
    + get_exts_for("psutil"),
)
