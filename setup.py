import atexit
import os
import subprocess
import sys
import time

from setuptools import find_packages
from setuptools import setup


# Ensure _build/ is importable when invoked via the PEP 517 build backend
# (setuptools.build_meta does not guarantee the project root is on sys.path)
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Determine command early so we skip unnecessary imports for simple commands
_COMMAND = sys.argv[1] if len(sys.argv) > 1 else ""
_IS_CLEAN = _COMMAND == "clean"

# Always importable — stdlib only, no build deps required
from _build.clean import CleanLibraries  # noqa: E402
import _build.constants as _c  # noqa: E402


if not _IS_CLEAN:
    from setuptools_rust import build_rust

    from _build.debug import DebugMetadata
    from _build.debug import debug_build_extension
    from _build.download import LibraryDownloader
    from _build.extensions import CustomBuildExt
    from _build.extensions import ExtensionHashes
    from _build.extensions import get_cython_exts
    from _build.extensions import get_ext_modules
    from _build.rust import CustomBuildRust
    from _build.rust import PatchedDistribution
    from _build.utils import check_rust_toolchain
    from _build.utils import interpose_sccache
    from _build.utils import load_module_from_project_file

    # Before adding any extensions, check that system pre-requisites are satisfied
    try:
        check_rust_toolchain()
    except EnvironmentError as e:
        print(f"{e}")
        sys.exit(1)

    if DebugMetadata.enabled:
        DebugMetadata.start_ns = time.time_ns()
        CustomBuildExt.build_extension = debug_build_extension(CustomBuildExt.build_extension)
        build_rust.build_extension = debug_build_extension(CustomBuildRust.build_extension)
        atexit.register(DebugMetadata.dump_metadata)

    ext_modules = get_ext_modules()
    cython_exts = get_cython_exts()

    def _get_exts_for(name):
        try:
            mod = load_module_from_project_file(
                "ddtrace.vendor.{}.setup".format(name),
                "ddtrace/vendor/{}/setup.py".format(name),
            )
            return mod.get_extensions()
        except Exception as e:
            print("WARNING: Failed to load %s extensions, skipping: %s" % (name, e))
            return []

    _vendor_exts = _get_exts_for("psutil")
    _distclass = PatchedDistribution
    _cmdclass = {
        "build_ext": CustomBuildExt,
        "build_py": LibraryDownloader,
        "build_rust": CustomBuildRust,
        "clean": CleanLibraries,
        "ext_hashes": ExtensionHashes,
    }
else:
    ext_modules = []
    cython_exts = []
    _vendor_exts = []
    _distclass = None
    _cmdclass = {"clean": CleanLibraries}

# Handle serverless package name renaming
PACKAGE_NAME = f"ddtrace{_c.WHEEL_FLAVOR}"
if PACKAGE_NAME != "ddtrace":
    subprocess.run(["sed", "-i", "-e", f's/^name = ".*"/name = "{PACKAGE_NAME}"/g', "pyproject.toml"])
print(f"INFO: building package '{PACKAGE_NAME}'")

if not _IS_CLEAN:
    interpose_sccache()

setup(
    name="ddtrace",
    packages=find_packages(
        exclude=[
            "tests*",
            "benchmarks*",
            "scripts*",
            # build-time tooling — must not be shipped in the wheel
            "_build*",
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
        "ddtrace.internal": ["third-party.tar.gz"],
        "ddtrace.internal.datadog.profiling": (
            ["libdd_wrapper*.*"] + (["test/*"] if _c.BUILD_PROFILING_NATIVE_TESTS else [])
        ),
    },
    zip_safe=False,
    cmdclass=_cmdclass,
    setup_requires=[
        "cython",
        "cmake>=3.24.2,<3.28",
        "setuptools-rust<2",
        "patchelf>=0.17.0.0; sys_platform == 'linux'",
    ],
    ext_modules=ext_modules + cython_exts + _vendor_exts,
    **({"distclass": _distclass} if _distclass is not None else {}),
)
