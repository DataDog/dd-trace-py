import glob
import hashlib
import os
import platform
import shutil
import sys
import tarfile

from setuptools import setup, find_packages, Extension
from setuptools.command.build_ext import build_ext as BuildExtCommand
from setuptools.command.build_py import build_py as BuildPyCommand
from pkg_resources import get_build_platform
from distutils.command.clean import clean as CleanCommand

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

LIBDDWAF_DOWNLOAD_DIR = os.path.join(HERE, os.path.join("ddtrace", "appsec", "ddwaf", "libddwaf"))

CURRENT_OS = platform.system()

LIBDDWAF_VERSION = "1.11.0"

LIBDATADOG_PROF_DOWNLOAD_DIR = os.path.join(
    HERE, os.path.join("ddtrace", "internal", "datadog", "profiling", "libdatadog")
)

LIBDATADOG_PROF_VERSION = "2.1.0"


def verify_checksum(expected_checksum, filename):
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


def is_libc_gnu():
    return "glibc" in platform.libc_ver()[0]


class LibraryDownload:
    name = None
    download_dir = None
    version = None
    url_root = None
    available_releases = None
    expected_checksums = None
    translate_suffix = None

    @classmethod
    def expand_gz(cls, filename, suffixes, archive_dir, arch_dir):
        # Open the tarfile first to get the files needed.
        # This could be solved with "r:gz" mode, that allows random access
        # but that approach does not work on Windows
        with tarfile.open(filename, "r|gz", errorlevel=2) as tar:
            dynfiles = [c for c in tar.getmembers() if c.name.endswith(suffixes)]

        with tarfile.open(filename, "r|gz", errorlevel=2) as tar:
            print("extracting files:", [c.name for c in dynfiles])
            tar.extractall(members=dynfiles, path=HERE)
            os.rename(os.path.join(HERE, archive_dir), arch_dir)

    @classmethod
    def expand_xz(cls, filename, suffixes, archive_dir, arch_dir):
        # Decompress.  Use subprocess because xz isn't well-supported through
        # tarfile on 2.7
        # Ignores suffixes, as the only consumer doesn't need them
        try:
            os.makedirs(archive_dir)
            subprocess.check_call(["tar", "-xJf", filename, "-C", archive_dir])
            os.rename(os.path.join(HERE, archive_dir), arch_dir)
        except subprocess.CalledProcessError:
            print("extracting files from tar.xz archive")
            pass

    @classmethod
    def expand_zip(cls, filename, suffixes, archive_dir, arch_dir):
        with zipfile.ZipFile(filename, "r") as zip_ref:
            dynfiles = [c for c in zip_ref.namelist() if c.endswith(suffixes)]
            print("extracting files:", dynfiles)

            # expand files
            zip_ref.extractall(path=HERE, members=dynfiles)
            os.rename(os.path.join(HERE, archive_dir), arch_dir)

    @classmethod
    def expand(cls, filename, suffixes, archive_dir, arch_dir, OS):
        dispatch_dict = {
            "gz": cls.expand_gz,
            "xz": cls.expand_xz,
            "zip": cls.expand_zip,
        }
        return dispatch_dict[cls.archive_type(OS)](filename, suffixes, archive_dir, arch_dir)

    @classmethod
    def get_checksum(cls, OS, arch):
        return cls.expected_checksums[OS][arch]

    @classmethod
    def check_file(cls, OS, arch, filename):
        expected_checksum = cls.get_checksum(OS, arch)
        return verify_checksum(expected_checksum, filename)

    @classmethod
    def download_artifacts(cls):
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

            # If the directory for the architecture exists, assume the right files are there
            # Use `python setup.py clean` to remove all arch subdirs
            arch_dir = os.path.join(cls.download_dir, arch)
            if os.path.isdir(arch_dir):
                continue

            archive_dir = cls.get_package_name(arch, CURRENT_OS)
            archive_name = cls.get_package_file(arch, CURRENT_OS)

            # Download the file(s)
            try:
                filename, http_response = urlretrieve(cls.get_download_link(arch, CURRENT_OS), archive_name)
            except HTTPError as e:
                print("No archive found for dynamic library {}: {}".format(cls.name, archive_dir))
                raise e

            # Check the file.  The entire setup is aborted if a checksum mismatches.
            cls.check_file(CURRENT_OS, arch, filename)

            # Extract contents
            suffixes = cls.translate_suffix[CURRENT_OS]
            cls.expand(filename, suffixes, archive_dir, arch_dir, CURRENT_OS)

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


class LibDDWafDownload(LibraryDownload):
    name = "ddwaf"
    download_dir = LIBDDWAF_DOWNLOAD_DIR
    version = LIBDDWAF_VERSION
    url_root = "https://github.com/DataDog/libddwaf/releases/download"
    expected_checksums = {
        "Linux": {
            "x86_64": "7a0682c2e65cec7540d04646621d6768bc8f42c5ff22da2a0d20801b51ad442a",
            "aarch64": "92a0a64daa00376b127dfe7d7f02436f39a611325113e212cf2cdafddd50053a",
        },
        "Darwin": {
            "x86_64": "e1285b9a62393a4a37fad16b49812052b9eed95920ffe4cd591e06ac27ed1b32",
            "arm64": "a50d087d5b8f6d3b4bc72d4ef85e8004c06b179f07e5974f7849ccb552b292c8",
        },
        "Windows": {
            "x64": "f0ce0dff6718796a4bd2c0310d69fd22275d0f418c626c2b66bf24710f2ded47",
            "win32": "69f514fcad680412b8fcafb2c1f007eebccaab07bed455bb89a840b76c5780c6",
        },
    }
    available_releases = {
        "Windows": ["win32", "x64"],
        "Darwin": ["arm64", "x86_64"],
        "Linux": ["aarch64", "x86_64"],
    }
    translate_suffix = {"Windows": (".dll",), "Darwin": (".dylib",), "Linux": (".so",)}

    @classmethod
    def archive_type(cls, OS):
        return "gz"

    @classmethod
    def get_package_name(cls, arch, OS):
        return "lib%s-%s-%s-%s" % (cls.name, cls.version, OS.lower(), arch)

    @classmethod
    def get_package_file(cls, arch, OS):
        return "%s.tar.gz" % (cls.get_package_name(arch, OS))

    @classmethod
    def get_download_link(cls, arch, OS):
        return "%s/%s/%s" % (cls.url_root, cls.version, cls.get_package_file(arch, OS))


class LibDatadogDownload(LibraryDownload):
    name = "datadog"
    download_dir = LIBDATADOG_PROF_DOWNLOAD_DIR
    version = LIBDATADOG_PROF_VERSION
    url_root = "https://github.com/DataDog/libdatadog/releases/download"
    url_root_win = "https://globalcdn.nuget.org/packages/"
    expected_checksums = {
        "Linux": {
            "x86_64": {
                "gnu": "e9ee7172dd7b8f12ff8125e0ee699d01df7698604f64299c4094ae47629ccec1",
                "other": "59f8e014b80b5e44bfcc325d03cdcf7c147987e2a106883d91fe80e1cba79f4b",
            },
            "aarch64": {
                "gnu": "a326e9552e65b945c64e7119c23d670ffdfb99aa96d9d90928a8a2ff6427199d",
                "other": "7a5f8f37b2925ee3e54cc1da8db1a461a4db082f8fd0492e40d4b33c5e771306",
            },
        },
        "Windows": {
            "x64": "2be95dd0d9afafcfe2448af5a2c21ef349a3dce46f2706f7177feaac68cdce86",
            "win32": "2be95dd0d9afafcfe2448af5a2c21ef349a3dce46f2706f7177feaac68cdce86",
        },
    }
    available_releases = {
        "Windows": ["win32", "x64"],
        "Darwin": [],
        "Linux": ["x86_64"],
    }
    translate_suffix = {"Windows": (".lib", ".h"), "Darwin": (), "Linux": (".a", ".h")}

    @classmethod
    def get_checksum(cls, OS, arch):
        # Override on Linux because there are different packages per libc
        if OS == "Linux":
            libc = "gnu" if is_libc_gnu() else "other"
            return cls.expected_checksums[OS][arch][libc]
        return cls.expected_checksums[OS][arch]

    @classmethod
    def archive_type(cls, OS):
        if OS == "Windows":
            return "zip"
        else:
            return "gz"

    @classmethod
    def get_package_name(cls, arch, OS):
        if OS == "Linux":
            libc = "unknown-linux-gnu" if is_libc_gnu() else "alpine-linux-musl"
            archive_dir = "lib%s-%s-%s" % (cls.name, arch, libc)
        elif OS == "Windows":
            archive_dir = "lib%s" % (cls.name)
        return archive_dir

    @classmethod
    def get_package_file(cls, arch, OS):
        return "%s.tar.gz" % (cls.get_package_name(arch, OS))

    @classmethod
    def get_download_link(cls, arch, OS):
        ret_url = "invalid URL"
        if OS == "Linux":
            ret_url = "%s/v%s/%s" % (
                cls.url_root,
                cls.version,
                cls.get_package_file(arch, OS),
            )
        elif OS == "Windows":
            ret_url = "%s/%s.%s.nuget" % (
                cls.url_root_win,
                cls.get_package_name(),
                cls.version,
            )
        return ret_url

    @staticmethod
    def get_extra_objects():
        arch = "x86_64"
        base_name = "libdatadog_profiling"
        if CURRENT_OS != "Windows":
            base_name += ".a"
        base_path = os.path.join("ddtrace", "internal", "datadog", "profiling", "libdatadog", arch, "lib", base_name)
        if CURRENT_OS == "Linux":
            return [base_path]
        return []

    @staticmethod
    def get_include_dirs():
        if CURRENT_OS == "Linux":
            return [
                "ddtrace/internal/datadog/profiling/include",
                "ddtrace/internal/datadog/profiling/libdatadog/x86_64/include",
            ]
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

    def run(self):
        CleanLibraries.remove_artifacts()
        CleanCommand.run(self)


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
                "ddtrace.appsec.iast._stacktrace",
                # Sort source files for reproducibility
                sources=[
                    "ddtrace/appsec/iast/_stacktrace.c",
                ],
                extra_compile_args=debug_compile_args,
            )
        )
        if sys.version_info >= (3, 6, 0):
            ext_modules.append(
                Extension(
                    "ddtrace.appsec.iast._taint_tracking._native",
                    # Sort source files for reproducibility
                    sources=sorted(
                        glob.glob(
                            os.path.join("ddtrace", "appsec", "iast", "_taint_tracking", "**", "*.cpp"),
                            recursive=True,
                        )
                    ),
                    extra_compile_args=debug_compile_args + ["-std=c++17"],
                )
            )
else:
    ext_modules = []


def get_ddup_ext():
    # Currently unsupported on macos
    if CURRENT_OS == "Darwin":
        return []

    ddup_ext = []
    LibDatadogDownload.run()
    ddup_ext.extend(
        cythonize(
            [
                Cython.Distutils.Extension(
                    "ddtrace.internal.datadog.profiling.ddup",
                    sources=[
                        "ddtrace/internal/datadog/profiling/src/exporter.cpp",
                        "ddtrace/internal/datadog/profiling/src/interface.cpp",
                        "ddtrace/internal/datadog/profiling/ddup.pyx",
                    ],
                    include_dirs=LibDatadogDownload.get_include_dirs(),
                    extra_objects=LibDatadogDownload.get_extra_objects(),
                    extra_compile_args=["-std=c++17"],
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
    "dead-bytecode; python_version<'3.0'",  # backport of bytecode for Python 2.7
    "bytecode~=0.12.0; python_version=='3.5'",
    "bytecode~=0.13.0; python_version=='3.6'",
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
    packages=find_packages(exclude=["tests*", "benchmarks"]),
    package_data={
        "ddtrace": ["py.typed"],
        "ddtrace.appsec": ["rules.json"],
        "ddtrace.appsec.ddwaf": [os.path.join("libddwaf", "*", "lib", "libddwaf.*")],
    },
    python_requires=">=2.7, !=3.0.*, !=3.1.*, !=3.2.*, !=3.3.*, !=3.4.*",
    zip_safe=False,
    # enum34 is an enum backport for earlier versions of python
    # funcsigs backport required for vendored debtcollector
    install_requires=[
        "ddsketch>=2.0.1",
        "enum34; python_version<'3.4'",
        "funcsigs>=1.0.0; python_version=='2.7'",
        "typing; python_version<'3.5'",
        "protobuf>=3; python_version>='3.7'",
        "protobuf>=3,<4.0; python_version=='3.6'",
        "protobuf>=3,<3.18; python_version<'3.6'",
        "attrs>=20; python_version>'2.7'",
        "attrs>=20,<22; python_version=='2.7'",
        "contextlib2<1.0; python_version=='2.7'",
        "cattrs<1.1; python_version<='3.6'",
        "cattrs; python_version>='3.7'",
        "six>=1.12.0",
        "typing_extensions",
        "importlib_metadata; python_version<'3.8'",
        "pathlib2; python_version<'3.5'",
        "jsonschema",
        "xmltodict>=0.12",
        "ipaddress; python_version<'3.7'",
        "envier",
        "pep562; python_version<'3.7'",
        "opentelemetry-api>=1; python_version>='3.7'",
    ]
    + bytecode,
    extras_require={
        # users can include opentracing by having:
        # install_requires=['ddtrace[opentracing]', ...]
        "opentracing": ["opentracing>=2.0.0"],
    },
    tests_require=["flake8"],
    cmdclass={
        "build_ext": BuildExtCommand,
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
        "Programming Language :: Python",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    use_scm_version={"write_to": "ddtrace/_version.py"},
    setup_requires=["setuptools_scm[toml]>=4", "cython"],
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
        },
        force=True,
        annotate=os.getenv("_DD_CYTHON_ANNOTATE") == "1",
    )
    + get_exts_for("wrapt")
    + get_exts_for("psutil")
    + get_ddup_ext(),
)
