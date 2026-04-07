import os
from pathlib import Path
import platform
import shlex
import sys
import warnings


# Two .parent calls because constants.py is inside _build/
HERE = Path(__file__).resolve().parent.parent

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

SERVERLESS_BUILD = os.getenv("DD_SERVERLESS_BUILD", "0").lower() in ("1", "yes", "on", "true")
WHEEL_FLAVOR = "-serverless" if SERVERLESS_BUILD else ""

LIBDDWAF_VERSION = "1.30.1"

# DEV: update this accordingly when src/native upgrades libdatadog dependency.
# libdatadog v15.0.0 requires rust 1.78.
RUST_MINIMUM_VERSION = "1.78"
