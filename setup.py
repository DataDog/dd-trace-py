import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile


from setuptools import Extension, find_packages, setup  # isort: skip
from setuptools.command.build_ext import build_ext  # isort: skip
from setuptools.command.build_py import build_py as BuildPyCommand  # isort: skip
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

if sys.version_info >= (3, 0):
    from urllib.error import HTTPError
    from urllib.request import urlretrieve
else:
    from urllib import urlretrieve

    from urllib2 import HTTPError


HERE = os.path.dirname(os.path.abspath(__file__))

DEBUG_COMPILE = "DD_COMPILE_DEBUG" in os.environ

IS_PYSTON = hasattr(sys, "pyston_version_info")

LIBDDWAF_DOWNLOAD_DIR = os.path.join(HERE, os.path.join("ddtrace", "appsec", "_ddwaf", "libddwaf"))
IAST_DIR = os.path.join(HERE, os.path.join("ddtrace", "appsec", "_iast", "_taint_tracking"))

CURRENT_OS = platform.system()

LIBDDWAF_VERSION = "1.15.0"

LIBDATADOG_PROF_DOWNLOAD_DIR = os.path.join(
    HERE, os.path.join("ddtrace", "internal", "datadog", "profiling", "libdatadog")
)

LIBDATADOG_PROF_VERSION = "v3.0.0"


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
    fpath = os.path.join(HERE, fname)

    if sys.version_info >= (3, 5):
        import importlib.util

        spec = importlib.util.spec_from_file_location(mod_name, fpath)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    elif sys.version_info >= (3, 3):
        from importlib.machinery import SourceFileLoader

        return SourceFileLoader(mod_name, fpath).load_module()
    else:
        import imp

        return imp.load_source(mod_name, fpath)


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

        # If the directory exists and it is not empty, assume the right files are there.
        # Use `python setup.py clean` to remove it.
        if os.path.isdir(cls.download_dir) and os.listdir(cls.download_dir):
            return

        if not os.path.isdir(cls.download_dir):
            os.makedirs(cls.download_dir)

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

            arch_dir = os.path.join(cls.download_dir, arch)

            # If the directory for the architecture exists, assume the right files are there
            if os.path.isdir(arch_dir):
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
                os.rename(os.path.join(HERE, archive_dir), arch_dir)

            # Rename <name>.xxx to lib<name>.xxx so the filename is the same for every OS
            for suffix in suffixes:
                original_file = os.path.join(arch_dir, "lib", cls.name + suffix)
                if os.path.exists(original_file):
                    renamed_file = os.path.join(arch_dir, "lib", "lib" + cls.name + suffix)
                    os.rename(original_file, renamed_file)

            os.remove(filename)

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


class LibDatadogDownload(LibraryDownload):
    name = "datadog"
    download_dir = LIBDATADOG_PROF_DOWNLOAD_DIR
    version = LIBDATADOG_PROF_VERSION
    url_root = "https://github.com/DataDog/libdatadog/releases/download"
    expected_checksums = {
        "Linux": {
            "x86_64": "39418275058a5ba96d6284bb6add0e9fbf6a59d7de0755d10184a0223f21c3ed",
            "aarch64": "71b89626f585ebf385482fb9d000c71f91721b395df8d352ce04a825c1f1776a",
        },
    }
    available_releases = {
        "Windows": [],
        "Darwin": [],
        "Linux": ["x86_64", "aarch64"],
    }
    translate_suffix = {"Windows": (".lib", ".h"), "Darwin": (".a", ".h"), "Linux": (".a", ".h")}

    @classmethod
    def get_package_name(cls, arch, os):
        osnames = {
            "Linux": "unknown-linux-gnu",
        }
        tar_osname = osnames[os]
        archive_dir = "lib%s-%s-%s" % (cls.name, arch, tar_osname)
        return archive_dir

    @staticmethod
    def get_extra_objects():
        base_name = "libdatadog_profiling"
        arch = platform.machine()
        if arch in LibDatadogDownload.available_releases[CURRENT_OS]:
            base_name += LibDatadogDownload.translate_suffix[CURRENT_OS][0]  # always static lib extension
            base_path = os.path.join(
                "ddtrace", "internal", "datadog", "profiling", "libdatadog", arch, "lib", base_name
            )
            return [base_path]
        return []

    @staticmethod
    def get_include_dirs():
        arch = platform.machine()
        if arch in LibDatadogDownload.available_releases[CURRENT_OS]:
            base_include_dir = "ddtrace/internal/datadog/profiling/include"
            arch_include_dir = os.path.join(
                "ddtrace", "internal", "datadog", "profiling", "libdatadog", arch, "include"
            )
            return [base_include_dir, arch_include_dir]

        return []


class LibraryDownloader(BuildPyCommand):
    def run(self):
        CleanLibraries.remove_artifacts()
        LibDatadogDownload.run()
        LibDDWafDownload.run()
        BuildPyCommand.run(self)


class CleanLibraries(CleanCommand):
    @staticmethod
    def remove_artifacts():
        shutil.rmtree(LIBDDWAF_DOWNLOAD_DIR, True)
        shutil.rmtree(LIBDATADOG_PROF_DOWNLOAD_DIR, True)
        shutil.rmtree(os.path.join(IAST_DIR, "*.so"), True)

    def run(self):
        CleanLibraries.remove_artifacts()
        CleanCommand.run(self)


class CMakeBuild(build_ext):
    @staticmethod
    def try_strip_symbols(so_file):
        if CURRENT_OS == "Linux" and shutil.which("strip") is not None:
            subprocess.run(["strip", "-g", so_file], check=True)

    def build_extension(self, ext):
        if isinstance(ext, CMakeExtension):
            self.build_extension_cmake(ext)
        else:
            super().build_extension(ext)

        if not DEBUG_COMPILE:
            try:
                if not DEBUG_COMPILE:
                    self.try_strip_symbols(self.get_ext_fullpath(ext.name))
            except Exception as e:
                print(f"WARNING: An error occurred while building the extension: {e}")
                raise

    def build_extension_cmake(self, ext):
        # Define the build and output directories
        output_dir = os.path.abspath(os.path.dirname(self.get_ext_fullpath(ext.name)))
        extension_basename = os.path.basename(self.get_ext_fullpath(ext.name))

        # We derive the cmake build directory from the output directory, but put it in
        # a sibling directory to avoid polluting the final package
        cmake_build_dir = os.path.abspath(self.build_lib.replace("lib.", "cmake."))
        os.makedirs(cmake_build_dir, exist_ok=True)

        # Which commands are passed to _every_ cmake invocation
        cmake_args = ext.cmake_args or []
        cmake_args += [
            "-S{}".format(ext.source_dir),  # cmake>=3.13
            "-B{}".format(cmake_build_dir),  # cmake>=3.13
            "-DPYTHON_EXECUTABLE={}".format(sys.executable),
            "-DCMAKE_BUILD_TYPE={}".format(ext.build_type),
            "-DCMAKE_LIBRARY_OUTPUT_DIRECTORY={}".format(output_dir),
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
            # DEV: -j is only supported in CMake 3.12+ only.
            if hasattr(self, "parallel") and self.parallel:
                build_args += ["-j{}".format(self.parallel)]

        # Arguments to cmake --install command
        install_args = ext.install_args or []
        install_args += ["--config {}".format(ext.build_type)]

        # platform/version-specific arguments--may go into cmake, build, or install as needed
        if CURRENT_OS == "Windows":
            cmake_args.extend(["-A", "x64" if platform.architecture()[0] == "64bit" else "Win32"])
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

        try:
            cmake_command = os.environ.get("CMAKE_COMMAND", "cmake")
            subprocess.run([cmake_command, *cmake_args], cwd=cmake_build_dir, check=True)
            subprocess.run([cmake_command, "--build", ".", *build_args], cwd=cmake_build_dir, check=True)
            subprocess.run([cmake_command, "--install", ".", *install_args], cwd=cmake_build_dir, check=True)
        except subprocess.CalledProcessError as e:
            print("WARNING: Command '{}' returned non-zero exit status {}.".format(e.cmd, e.returncode))
            if not ext.permissive_build:
                raise
        except Exception as e:
            print("WARNING: An error occurred while building the CMake extension.")
            raise


class CMakeExtension(Extension):
    def __init__(
        self,
        name,
        source_dir=".",
        cmake_args=[],
        build_args=[],
        install_args=[],
        build_type=None,
        permissive_build=False,
    ):
        super().__init__(name, sources=[])
        self.source_dir = source_dir
        self.cmake_args = cmake_args or []
        self.build_args = build_args or []
        self.install_args = install_args or []
        self.build_type = build_type or "Debug" if DEBUG_COMPILE else "Release"
        self.permissive_build = permissive_build  # If True, build errors are ignored


long_description = """
# dd-trace-py

`ddtrace` is Datadog's tracing library for Python.  It is used to trace requests
as they flow across web servers, databases and microservices so that developers
have great visibility into bottlenecks and troublesome requests.

## Getting Started

For a basic product overview, installation and quick start, check out our
[setup documentation][setup docs].

For more advanced usage and configuration, check out our [API
documentation][api docs].

For descriptions of terminology used in APM, take a look at the [official
documentation][visualization docs].

[setup docs]: https://docs.datadoghq.com/tracing/setup/python/
[api docs]: https://ddtrace.readthedocs.io/
[visualization docs]: https://docs.datadoghq.com/tracing/visualization/
"""


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


# TODO can we specify the exact compiler version less literally?
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

if sys.version_info[:2] >= (3, 4) and not IS_PYSTON:
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

        ext_modules.append(
            CMakeExtension(
                "ddtrace.appsec._iast._taint_tracking._native",
                source_dir=IAST_DIR,
                permissive_build=True if CURRENT_OS == "Darwin" else False,
            )
        )
else:
    ext_modules = []


def get_ddup_ext():
    ddup_ext = []
    arch = platform.machine()
    if "glibc" in platform.libc_ver()[0] and arch in LibDatadogDownload.available_releases[CURRENT_OS]:
        LibDatadogDownload.run()
        ddup_ext.extend(
            cythonize(
                [
                    Cython.Distutils.Extension(
                        "ddtrace.internal.datadog.profiling._ddup",
                        sources=[
                            "ddtrace/internal/datadog/profiling/src/exporter.cpp",
                            "ddtrace/internal/datadog/profiling/src/interface.cpp",
                            "ddtrace/internal/datadog/profiling/_ddup.pyx",
                        ],
                        include_dirs=LibDatadogDownload.get_include_dirs(),
                        extra_objects=LibDatadogDownload.get_extra_objects(),
                        extra_compile_args=["-std=c++17", "-flto"],
                        language="c++",
                    )
                ],
                compile_time_env={
                    "PY_MAJOR_VERSION": sys.version_info.major,
                    "PY_MINOR_VERSION": sys.version_info.minor,
                    "PY_MICRO_VERSION": sys.version_info.micro,
                },
                force=True,
                annotate=os.getenv("_DD_CYTHON_ANNOTATE") == "1",
            )
        )
    return ddup_ext


bytecode = [
    "bytecode~=0.13.0; python_version=='3.7'",
    "bytecode; python_version>='3.8'",
]

setup(
    name="ddtrace",
    description="Datadog APM client library",
    url="https://github.com/DataDog/dd-trace-py",
    package_urls={
        "Changelog": "https://ddtrace.readthedocs.io/en/stable/release_notes.html",
        "Documentation": "https://ddtrace.readthedocs.io/en/stable/",
    },
    project_urls={
        "Bug Tracker": "https://github.com/DataDog/dd-trace-py/issues",
        "Source Code": "https://github.com/DataDog/dd-trace-py/",
        "Changelog": "https://ddtrace.readthedocs.io/en/stable/release_notes.html",
        "Documentation": "https://ddtrace.readthedocs.io/en/stable/",
    },
    author="Datadog, Inc.",
    author_email="dev@datadoghq.com",
    long_description=long_description,
    long_description_content_type="text/markdown",
    license="BSD",
    packages=find_packages(exclude=["tests*", "benchmarks*"]),
    package_data={
        "ddtrace": ["py.typed"],
        "ddtrace.appsec": ["rules.json"],
        "ddtrace.appsec._ddwaf": [os.path.join("libddwaf", "*", "lib", "libddwaf.*")],
        "ddtrace.appsec._iast._taint_tracking": ["CMakeLists.txt"],
    },
    python_requires=">=3.7",
    zip_safe=False,
    # enum34 is an enum backport for earlier versions of python
    # funcsigs backport required for vendored debtcollector
    install_requires=[
        "ddsketch>=2.0.1",
        "protobuf>=3",
        "attrs>=20",
        "cattrs",
        "six>=1.12.0",
        "typing_extensions",
        "importlib_metadata; python_version<'3.8'",
        "xmltodict>=0.12",
        "envier",
        "opentelemetry-api>=1",
        "setuptools; python_version>='3.12'",
    ]
    + bytecode,
    extras_require={
        # users can include opentracing by having:
        # install_requires=['ddtrace[opentracing]', ...]
        "opentracing": ["opentracing>=2.0.0"],
    },
    tests_require=["flake8"],
    cmdclass={
        "build_ext": CMakeBuild,
        "build_py": LibraryDownloader,
        "clean": CleanLibraries,
    },
    entry_points={
        "console_scripts": [
            "ddtrace-run = ddtrace.commands.ddtrace_run:main",
        ],
        "pytest11": [
            "ddtrace = ddtrace.contrib.pytest.plugin",
            "ddtrace.pytest_bdd = ddtrace.contrib.pytest_bdd.plugin",
        ],
        "opentelemetry_context": [
            "ddcontextvars_context = ddtrace.opentelemetry._context:DDRuntimeContext",
        ],
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Programming Language :: Python :: Implementation :: CPython",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
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
    )
    + get_exts_for("wrapt")
    + get_exts_for("psutil")
    + get_ddup_ext(),
)
