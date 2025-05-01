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


TELEMETRY_DATA = []
SCRIPT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))
RUNTIMES_ALLOW_LIST = {
    "cpython": {
        "min": Version(version=(3, 8), constraint=""),
        "max": Version(version=(3, 14), constraint=""),
    }
}

FORCE_INJECT = os.environ.get("DD_INJECT_FORCE", "").lower() in ("true", "1", "t")
FORWARDER_EXECUTABLE = os.environ.get("DD_TELEMETRY_FORWARDER_PATH", "")
TELEMETRY_ENABLED = "DD_INJECTION_ENABLED" in os.environ
DEBUG_MODE = os.environ.get("DD_TRACE_DEBUG", "").lower() in ("true", "1", "t")
INSTALLED_PACKAGES = {}
DDTRACE_VERSION = "unknown"
PYTHON_VERSION = "unknown"
PYTHON_RUNTIME = "unknown"
MIN_DEPENDENCY_INTEGRATION_MAP = {}
EXECUTABLES_DENY_LIST = set()
VERSION_COMPAT_FILE_LOCATIONS = (
    os.path.abspath(os.path.join(SCRIPT_DIR, "../datadog-lib/min_compatible_versions.csv")),
    os.path.abspath(os.path.join(SCRIPT_DIR, "min_compatible_versions.csv")),
)
MIN_DEPENDENCY_INTEGRATION_FILE_LOCATIONS = os.path.abspath(os.path.join(SCRIPT_DIR, "supported_versions_table.csv"))
EXECUTABLE_DENY_LOCATION = os.path.abspath(os.path.join(SCRIPT_DIR, "denied_executables.txt"))
SITE_PKGS_MARKER = "site-packages-ddtrace-py"
BOOTSTRAP_MARKER = "bootstrap"


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
    try:
        from importlib import metadata as importlib_metadata

        installed_packages = {pkg.metadata["Name"]: pkg.version for pkg in importlib_metadata.distributions()}
    except Exception as e:
        _log("Failed to build installed packages list: %s" % e, level="debug")
    return {key.lower(): value for key, value in installed_packages.items()}


def build_min_integrations():
    min_dep_to_integration = dict()
    try:
        with open(MIN_DEPENDENCY_INTEGRATION_FILE_LOCATIONS, "r", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                try:
                    dep_name = row.get("dependency")
                    integration_name = row.get("integration").replace(" *", "")
                    min_version_raw = row.get("minimum_tracer_supported")

                    min_version = min_version_raw
                    min_dep_to_integration[dep_name] = {
                        "integration_name": integration_name,
                        "min_version": min_version,
                    }
                except Exception as row_exc:
                    _log("Error processing row %s: %s", row, row_exc, level="debug")
                    continue
    except Exception as e:
        _log("Failed to build min-integrations list from %s: %s", MIN_DEPENDENCY_INTEGRATION_FILE_LOCATIONS, e, level="error")

    _log("Built min integrations map with %d entries", len(min_dep_to_integration), level="debug")
    return min_dep_to_integration


def build_denied_executables():
    denied_executables = set()
    _log("Checking denied-executables list", level="debug")
    try:
        if os.path.exists(EXECUTABLE_DENY_LOCATION):
            with open(EXECUTABLE_DENY_LOCATION, "r") as denyfile:
                _log("Found deny-list file", level="debug")
                for line in denyfile.readlines():
                    cleaned = line.strip("\n")
                    denied_executables.add(cleaned)
                    denied_executables.add(os.path.basename(cleaned))
        _log("Built denied-executables list of %s entries" % (len(denied_executables),), level="debug")
    except Exception as e:
        _log("Failed to build denied-executables list: %s" % e, level="debug")
    return denied_executables


def create_count_metric(metric, tags=None):
    if tags is None:
        tags = []
    return {
        "name": metric,
        "tags": tags,
    }


def gen_telemetry_payload(telemetry_events, ddtrace_version):
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


def _send_telemetry(event):
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
    # Mimic Popen.__exit__ which was added in Python 3.3
    try:
        if p.stdin:
            p.stdin.write(event_json)
            _log("wrote telemetry to %s" % FORWARDER_EXECUTABLE, level="debug")
        else:
            _log(
                "failed to write telemetry to %s, could not write to telemetry writer stdin" % FORWARDER_EXECUTABLE,
                level="error",
            )
    finally:
        if p.stdin:
            p.stdin.close()
        if p.stderr:
            p.stderr.close()
        if p.stdout:
            p.stdout.close()

        # backwards compatible `p.wait(1)`
        start = time.time()
        while p.poll() is None:
            if time.time() - start > 1:
                p.kill()
                break
            time.sleep(0.05)


def send_telemetry(event):
    try:
        _send_telemetry(event)
    except Exception as e:
        _log("Failed to send telemetry: %s" % e, level="error")


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
    integration_spec = MIN_DEPENDENCY_INTEGRATION_MAP.get(package_name.lower(), None)
    if integration_spec is None:
        return True
    supported_version_spec = parse_version(integration_spec["min_version"])
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
    global DDTRACE_VERSION
    global INSTALLED_PACKAGES
    global PYTHON_VERSION
    global PYTHON_RUNTIME
    global MIN_DEPENDENCY_INTEGRATION_MAP
    global EXECUTABLES_DENY_LIST
    global TELEMETRY_DATA
    # Try to get the version of the Python runtime first so we have it for telemetry
    PYTHON_VERSION = platform.python_version()
    PYTHON_RUNTIME = platform.python_implementation().lower()
    DDTRACE_VERSION = get_oci_ddtrace_version()
    INSTALLED_PACKAGES = build_installed_pkgs()
    MIN_DEPENDENCY_INTEGRATION_MAP = build_min_integrations()
    EXECUTABLES_DENY_LIST = build_denied_executables()
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

        current_platform = "manylinux2014" if _get_clib() == "gnu" else "musllinux_1_2"
        _log("detected platform %s" % current_platform, level="debug")

        pkgs_path = os.path.join(SCRIPT_DIR, "ddtrace_pkgs")
        _log("ddtrace_pkgs path is %r" % pkgs_path, level="debug")
        _log("ddtrace_pkgs contents: %r" % os.listdir(pkgs_path), level="debug")

        abort = False
        incompatible_sysarg = get_first_incompatible_sysarg()
        if incompatible_sysarg is not None:
            _log("Found incompatible executable: %s." % incompatible_sysarg, level="debug")
            if not FORCE_INJECT:
                _log("Aborting dd-trace-py instrumentation.", level="debug")
                abort = True
                TELEMETRY_DATA.append(
                    create_count_metric(
                        "library_entrypoint.abort.integration",
                    )
                )
            else:
                _log(
                    "DD_INJECT_FORCE set to True, allowing unsupported executables and continuing.",
                    level="debug",
                )

        # check installed packages against our minimum integration version requirements
        incompatible_integrations = {}
        for package_name, package_version in INSTALLED_PACKAGES.items():
            if not package_is_compatible(package_name, package_version):
                incompatible_integrations[
                    MIN_DEPENDENCY_INTEGRATION_MAP.get(package_name.lower(), {}).get("integration_name")
                ] = package_version

        # if we found any incompatible integrations, disable them using the environment variable:
        # DD_TRACE_[INTEGRATION_NAME]_ENABLED
        if incompatible_integrations:
            _log("Found incompatible integrations: %s." % incompatible_integrations, level="debug")
            integration_incomp = True
            for integration_name, integration_version in incompatible_integrations.items():
                # set environment variable to disable integration
                os.environ["DD_TRACE_" + integration_name.upper() + "_ENABLED"] = "false"
                _log("Disabled integration %s" % integration_name, level="warning")
                TELEMETRY_DATA.append(
                    create_count_metric(
                        "library_entrypoint.abort.integration",
                        [
                            "integration:" + integration_name,
                            "integration_version:" + integration_version,
                        ],
                    )
                )
            integration_incomp = False
        if not runtime_version_is_supported(PYTHON_RUNTIME, PYTHON_VERSION):
            _log(
                "Found incompatible runtime: %s %s. Supported runtimes: %s"
                % (PYTHON_RUNTIME, PYTHON_VERSION, RUNTIMES_ALLOW_LIST),
                level="debug",
            )
            runtime_incomp = True
            if not FORCE_INJECT:
                _log("Aborting dd-trace-py instrumentation.", level="debug")
                abort = True

                TELEMETRY_DATA.append(create_count_metric("library_entrypoint.abort.runtime"))
            else:
                _log(
                    "DD_INJECT_FORCE set to True, allowing unsupported runtimes and continuing.",
                    level="debug",
                )
        if abort:
            TELEMETRY_DATA.append(
                create_count_metric(
                    "library_entrypoint.abort",
                    [
                        "reason:integration" if integration_incomp else "reason:incompatible_runtime",
                    ],
                )
            )
            return

        site_pkgs_path = os.path.join(
            pkgs_path, "%s%s-%s" % (SITE_PKGS_MARKER, ".".join(PYTHON_VERSION.split(".")[:2]), current_platform)
        )
        _log("site-packages path is %r" % site_pkgs_path, level="debug")
        if not os.path.exists(site_pkgs_path):
            _log("ddtrace site-packages not found in %r, aborting" % site_pkgs_path, level="error")
            TELEMETRY_DATA.append(
                create_count_metric("library_entrypoint.abort", ["reason:missing_" + site_pkgs_path]),
            )
            return

        # Add the custom site-packages directory to the Python path to load the ddtrace package.
        sys.path.insert(-1, site_pkgs_path)
        _log("sys.path %s" % sys.path, level="debug")
        try:
            import ddtrace  # noqa: F401

        except BaseException as e:
            _log("failed to load ddtrace module: %s" % e, level="error")
            TELEMETRY_DATA.append(
                create_count_metric(
                    "library_entrypoint.error", ["error_type:import_ddtrace_" + type(e).__name__.lower()]
                ),
            )

            return
        else:
            try:
                # Make sure to remove this script's directory, and to add the ddtrace bootstrap directory to the path
                # DEV: We need to add the bootstrap directory to the path to ensure the logic to load any user custom
                # sitecustomize is preserved.
                if SCRIPT_DIR in sys.path:
                    sys.path.remove(SCRIPT_DIR)
                bootstrap_dir = os.path.join(os.path.abspath(os.path.dirname(ddtrace.__file__)), BOOTSTRAP_MARKER)
                if bootstrap_dir not in sys.path:
                    sys.path.insert(0, bootstrap_dir)

                import ddtrace.bootstrap.sitecustomize

                path_segments_indicating_removal = [
                    SCRIPT_DIR,
                    SITE_PKGS_MARKER,
                    os.path.join("ddtrace", BOOTSTRAP_MARKER),
                ]

                # Modify the PYTHONPATH for any subprocesses that might be spawned:
                #   - Remove any preexisting entries related to ddtrace to ensure initial state is clean
                #   - Remove the PYTHONPATH entry used to bootstrap this installation as it's no longer necessary
                #     now that the package is installed.
                #   - Add the custom site-packages directory to PYTHONPATH to ensure the ddtrace package can be loaded
                #   - Add the ddtrace bootstrap dir to the PYTHONPATH to achieve the same effect as ddtrace-run.
                python_path = [
                    entry
                    for entry in os.getenv("PYTHONPATH", "").split(os.pathsep)
                    if not any(path in entry for path in path_segments_indicating_removal)
                ]
                python_path.insert(-1, site_pkgs_path)
                python_path.insert(0, bootstrap_dir)
                python_path = os.pathsep.join(python_path)
                os.environ["PYTHONPATH"] = python_path

                _log("successfully configured ddtrace package, python path is %r" % os.environ["PYTHONPATH"])
                TELEMETRY_DATA.append(
                    create_count_metric(
                        "library_entrypoint.complete",
                        [
                            "injection_forced:" + str(runtime_incomp or integration_incomp).lower(),
                        ],
                    ),
                )
            except Exception as e:
                TELEMETRY_DATA.append(
                    create_count_metric(
                        "library_entrypoint.error", ["error_type:init_ddtrace_" + type(e).__name__.lower()]
                    ),
                )
                _log("failed to load ddtrace.bootstrap.sitecustomize: %s" % e, level="error")
                return
    else:
        module_origin = spec.origin if spec else None
        _log("user-installed ddtrace found: %s, aborting site-packages injection" % module_origin, level="warning")
        TELEMETRY_DATA.append(
            create_count_metric(
                "library_entrypoint.abort",
                [
                    "reason:ddtrace_already_present",
                ],
            )
        )


try:
    try:
        _inject()
    except Exception as e:
        TELEMETRY_DATA.append(
            create_count_metric("library_entrypoint.error", ["error_type:main_" + type(e).__name__.lower()])
        )
    finally:
        if TELEMETRY_DATA:
            payload = gen_telemetry_payload(TELEMETRY_DATA, DDTRACE_VERSION)
            send_telemetry(payload)
except Exception:
    pass  # absolutely never allow exceptions to propagate to the app
