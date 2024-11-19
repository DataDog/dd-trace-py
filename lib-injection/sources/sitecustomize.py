"""
This module when included on the PYTHONPATH will update the PYTHONPATH to point to a directory
containing the ddtrace package compatible with the current Python version and platform.
"""

from collections import namedtuple
import csv
import json
import os
import platform
import re
import subprocess
import sys
import time


Version = namedtuple("Version", ["version", "constraint"])


def parse_version(version):
    try:
        constraint_match = re.search(r"\d", version)
        if not constraint_match:
            return Version((0, 0), "")
        constraint_idx = constraint_match.start()
        numeric = version[constraint_idx:]
        constraint = version[:constraint_idx]
        parsed_version = tuple(int(re.sub("[^0-9]", "", p)) for p in numeric.split("."))
        return Version(parsed_version, constraint)
    except Exception:
        return Version((0, 0), "")


SCRIPT_DIR = os.path.dirname(__file__)
RUNTIMES_ALLOW_LIST = {
    "cpython": {
        "min": Version(version=(3, 7), constraint=""),
        "max": Version(version=(3, 13), constraint=""),
    }
}

FORCE_INJECT = os.environ.get("DD_INJECT_FORCE", "").lower() in ("true", "1", "t")
FORWARDER_EXECUTABLE = os.environ.get("DD_TELEMETRY_FORWARDER_PATH", "")
TELEMETRY_ENABLED = "DD_INJECTION_ENABLED" in os.environ
DEBUG_MODE = os.environ.get("DD_TRACE_DEBUG", "").lower() in ("true", "1", "t")
INSTALLED_PACKAGES = {}
PYTHON_VERSION = "unknown"
PYTHON_RUNTIME = "unknown"
PKGS_ALLOW_LIST = {}
EXECUTABLES_DENY_LIST = set()
VERSION_COMPAT_FILE_LOCATIONS = (
    os.path.abspath(os.path.join(SCRIPT_DIR, "../datadog-lib/min_compatible_versions.csv")),
    os.path.abspath(os.path.join(SCRIPT_DIR, "min_compatible_versions.csv")),
)
EXECUTABLE_DENY_LOCATION = os.path.abspath(os.path.join(SCRIPT_DIR, "denied_executables.txt"))


def get_oci_ddtrace_version():
    version_path = os.path.join(SCRIPT_DIR, "version")
    try:
        with open(version_path, "r") as version_file:
            return version_file.read().strip()
    except Exception:
        _log("Failed to read version file %s" % (version_path,), level="debug")
        return "unknown"


def build_installed_pkgs():
    installed_packages = {}
    if sys.version_info >= (3, 8):
        from importlib import metadata as importlib_metadata

        installed_packages = {pkg.metadata["Name"]: pkg.version for pkg in importlib_metadata.distributions()}
    else:
        try:
            import pkg_resources

            installed_packages = {pkg.key: pkg.version for pkg in pkg_resources.working_set}
        except ImportError:
            try:
                import importlib_metadata

                installed_packages = {pkg.metadata["Name"]: pkg.version for pkg in importlib_metadata.distributions()}
            except ImportError:
                pass
    return {key.lower(): value for key, value in installed_packages.items()}


def build_min_pkgs():
    min_pkgs = dict()
    for location in VERSION_COMPAT_FILE_LOCATIONS:
        if os.path.exists(location):
            with open(location, "r") as csvfile:
                csv_reader = csv.reader(csvfile, delimiter=",")
                for idx, row in enumerate(csv_reader):
                    if idx < 2:
                        continue
                    min_pkgs[row[0].lower()] = parse_version(row[1])
            break
    return min_pkgs


def build_denied_executables():
    denied_executables = set()
    _log("Checking denied-executables list", level="debug")
    if os.path.exists(EXECUTABLE_DENY_LOCATION):
        with open(EXECUTABLE_DENY_LOCATION, "r") as denyfile:
            _log("Found deny-list file", level="debug")
            for line in denyfile.readlines():
                cleaned = line.strip("\n")
                denied_executables.add(cleaned)
                denied_executables.add(os.path.basename(cleaned))
    _log("Built denied-executables list of %s entries" % (len(denied_executables),), level="debug")
    return denied_executables


def create_count_metric(metric, tags=None):
    if tags is None:
        tags = []
    return {
        "name": metric,
        "tags": tags,
    }


def gen_telemetry_payload(telemetry_events, ddtrace_version="unknown"):
    return {
        "metadata": {
            "language_name": "python",
            "language_version": PYTHON_VERSION,
            "runtime_name": PYTHON_RUNTIME,
            "runtime_version": PYTHON_VERSION,
            "tracer_version": ddtrace_version,
            "pid": os.getpid(),
        },
        "points": telemetry_events,
    }


def send_telemetry(event):
    event_json = json.dumps(event)
    _log("maybe sending telemetry to %s" % FORWARDER_EXECUTABLE, level="debug")
    if not FORWARDER_EXECUTABLE or not TELEMETRY_ENABLED:
        _log("not sending telemetry: TELEMETRY_ENABLED=%s" % TELEMETRY_ENABLED, level="debug")
        return
    p = subprocess.Popen(
        [FORWARDER_EXECUTABLE, "library_entrypoint"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    if p.stdin:
        p.stdin.write(event_json)
        p.stdin.close()
        _log("wrote telemetry to %s" % FORWARDER_EXECUTABLE, level="debug")
    else:
        _log(
            "failed to write telemetry to %s, could not write to telemetry writer stdin" % FORWARDER_EXECUTABLE,
            level="error",
        )


def _get_clib():
    """Return the C library used by the system.

    If GNU is not detected then returns MUSL.
    """

    libc, _ = platform.libc_ver()
    if libc == "glibc":
        return "gnu"
    return "musl"


def _log(msg, *args, **kwargs):
    """Log a message to stderr.

    This function is provided instead of built-in Python logging since we can't rely on any logger
    being configured.
    """
    level = kwargs.get("level", "info")
    if DEBUG_MODE:
        asctime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        msg = "[%s] [%s] datadog.autoinstrumentation(pid: %d): " % (asctime, level.upper(), os.getpid()) + msg % args
        sys.stderr.write(msg)
        sys.stderr.write("\n")
        sys.stderr.flush()


def runtime_version_is_supported(python_runtime, python_version):
    supported_versions = RUNTIMES_ALLOW_LIST.get(python_runtime, {})
    if not supported_versions:
        return False
    return (
        supported_versions["min"].version <= parse_version(python_version).version < supported_versions["max"].version
    )


def package_is_compatible(package_name, package_version):
    installed_version = parse_version(package_version)
    supported_version_spec = PKGS_ALLOW_LIST.get(package_name.lower(), Version((0,), ""))
    if supported_version_spec.constraint in ("<", "<="):
        return True  # minimum "less than" means there is no minimum
    return installed_version.version >= supported_version_spec.version


def get_first_incompatible_sysarg():
    # bug: sys.argv is not always available in all python versions
    # https://bugs.python.org/issue32573
    if not hasattr(sys, "argv"):
        _log("sys.argv not available, skipping sys.argv check", level="debug")
        return

    _log("Checking sys.args: len(sys.argv): %s" % (len(sys.argv),), level="debug")
    if len(sys.argv) <= 1:
        return
    argument = sys.argv[0]
    _log("Is argument %s in deny-list?" % (argument,), level="debug")
    if argument in EXECUTABLES_DENY_LIST or os.path.basename(argument) in EXECUTABLES_DENY_LIST:
        _log("argument %s is in deny-list" % (argument,), level="debug")
        return argument


def _inject():
    global INSTALLED_PACKAGES
    global PYTHON_VERSION
    global PYTHON_RUNTIME
    global PKGS_ALLOW_LIST
    global EXECUTABLES_DENY_LIST
    DDTRACE_VERSION = get_oci_ddtrace_version()
    INSTALLED_PACKAGES = build_installed_pkgs()
    PYTHON_RUNTIME = platform.python_implementation().lower()
    PYTHON_VERSION = platform.python_version()
    PKGS_ALLOW_LIST = build_min_pkgs()
    EXECUTABLES_DENY_LIST = build_denied_executables()
    telemetry_data = []
    integration_incomp = False
    runtime_incomp = False
    os.environ["_DD_INJECT_WAS_ATTEMPTED"] = "true"
    spec = None
    try:
        # `find_spec` is only available in Python 3.4+
        # https://docs.python.org/3/library/importlib.html#importlib.util.find_spec
        # DEV: It is ok to fail here on import since it'll only fail on Python versions we don't support / inject into
        import importlib.util

        # None is a valid return value for find_spec (module was not found), so we need to check for it explicitly

        spec = importlib.util.find_spec("ddtrace")
        if not spec:
            raise ModuleNotFoundError("ddtrace")
    except Exception:
        _log("user-installed ddtrace not found, configuring application to use injection site-packages")

        current_platform = "manylinux2014" if _get_clib() == "gnu" else "musllinux_1_1"
        _log("detected platform %s" % current_platform, level="debug")

        pkgs_path = os.path.join(SCRIPT_DIR, "ddtrace_pkgs")
        _log("ddtrace_pkgs path is %r" % pkgs_path, level="debug")
        _log("ddtrace_pkgs contents: %r" % os.listdir(pkgs_path), level="debug")

        incompatible_sysarg = get_first_incompatible_sysarg()
        if incompatible_sysarg is not None:
            _log("Found incompatible executable: %s." % incompatible_sysarg, level="debug")
            if not FORCE_INJECT:
                _log("Aborting dd-trace-py instrumentation.", level="debug")
                telemetry_data.append(
                    create_count_metric(
                        "library_entrypoint.abort.integration",
                    )
                )
            else:
                _log(
                    "DD_INJECT_FORCE set to True, allowing unsupported executables and continuing.",
                    level="debug",
                )

        # check installed packages against allow list
        incompatible_packages = {}
        for package_name, package_version in INSTALLED_PACKAGES.items():
            if not package_is_compatible(package_name, package_version):
                incompatible_packages[package_name] = package_version

        if incompatible_packages:
            _log("Found incompatible packages: %s." % incompatible_packages, level="debug")
            integration_incomp = True
            if not FORCE_INJECT:
                _log("Aborting dd-trace-py instrumentation.", level="debug")

                for key, value in incompatible_packages.items():
                    telemetry_data.append(
                        create_count_metric(
                            "library_entrypoint.abort.integration",
                            [
                                "integration:" + key,
                                "integration_version:" + value,
                            ],
                        )
                    )

            else:
                _log(
                    "DD_INJECT_FORCE set to True, allowing unsupported integrations and continuing.",
                    level="debug",
                )
        if not runtime_version_is_supported(PYTHON_RUNTIME, PYTHON_VERSION):
            _log(
                "Found incompatible runtime: %s %s. Supported runtimes: %s"
                % (PYTHON_RUNTIME, PYTHON_VERSION, RUNTIMES_ALLOW_LIST),
                level="debug",
            )
            runtime_incomp = True
            if not FORCE_INJECT:
                _log("Aborting dd-trace-py instrumentation.", level="debug")

                telemetry_data.append(create_count_metric("library_entrypoint.abort.runtime"))
            else:
                _log(
                    "DD_INJECT_FORCE set to True, allowing unsupported runtimes and continuing.",
                    level="debug",
                )
        if telemetry_data:
            telemetry_data.append(
                create_count_metric(
                    "library_entrypoint.abort",
                    [
                        "reason:integration" if integration_incomp else "reason:incompatible_runtime",
                    ],
                )
            )
            telemetry_event = gen_telemetry_payload(telemetry_data, DDTRACE_VERSION)
            send_telemetry(telemetry_event)
            return

        site_pkgs_path = os.path.join(
            pkgs_path, "site-packages-ddtrace-py%s-%s" % (".".join(PYTHON_VERSION.split(".")[:2]), current_platform)
        )
        _log("site-packages path is %r" % site_pkgs_path, level="debug")
        if not os.path.exists(site_pkgs_path):
            _log("ddtrace site-packages not found in %r, aborting" % site_pkgs_path, level="error")
            return

        # Add the custom site-packages directory to the Python path to load the ddtrace package.
        sys.path.insert(-1, site_pkgs_path)
        _log("sys.path %s" % sys.path, level="debug")
        try:
            import ddtrace  # noqa: F401

        except BaseException as e:
            _log("failed to load ddtrace module: %s" % e, level="error")
            return
        else:
            try:
                import ddtrace.bootstrap.sitecustomize

                # Modify the PYTHONPATH for any subprocesses that might be spawned:
                #   - Remove the PYTHONPATH entry used to bootstrap this installation as it's no longer necessary
                #     now that the package is installed.
                #   - Add the custom site-packages directory to PYTHONPATH to ensure the ddtrace package can be loaded
                #   - Add the ddtrace bootstrap dir to the PYTHONPATH to achieve the same effect as ddtrace-run.
                python_path = os.getenv("PYTHONPATH", "").split(os.pathsep)
                if SCRIPT_DIR in python_path:
                    python_path.remove(SCRIPT_DIR)
                python_path.insert(-1, site_pkgs_path)
                bootstrap_dir = os.path.abspath(os.path.dirname(ddtrace.bootstrap.sitecustomize.__file__))
                python_path.insert(0, bootstrap_dir)
                python_path = os.pathsep.join(python_path)
                os.environ["PYTHONPATH"] = python_path

                # Also insert the bootstrap dir in the path of the current python process.
                sys.path.insert(0, bootstrap_dir)
                _log("successfully configured ddtrace package, python path is %r" % os.environ["PYTHONPATH"])
                event = gen_telemetry_payload(
                    [
                        create_count_metric(
                            "library_entrypoint.complete",
                            [
                                "injection_forced:" + str(runtime_incomp or integration_incomp).lower(),
                            ],
                        )
                    ],
                    DDTRACE_VERSION,
                )
                send_telemetry(event)
            except Exception as e:
                event = gen_telemetry_payload(
                    [create_count_metric("library_entrypoint.error", ["error_type:" + type(e).__name__.lower()])],
                    DDTRACE_VERSION,
                )
                send_telemetry(event)
                _log("failed to load ddtrace.bootstrap.sitecustomize: %s" % e, level="error")
                return
    else:
        module_origin = spec.origin if spec else None
        _log("user-installed ddtrace found: %s, aborting site-packages injection" % module_origin, level="warning")


try:
    _inject()
except Exception as e:
    try:
        event = gen_telemetry_payload(
            [create_count_metric("library_entrypoint.error", ["error_type:" + type(e).__name__.lower()])]
        )
        send_telemetry(event)
    except Exception:
        pass  # absolutely never allow exceptions to propagate to the app
