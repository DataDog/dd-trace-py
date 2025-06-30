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

        version_parts = []
        for part in numeric.split("."):
            match = re.match(r"(\d+)", part)
            if match:
                version_parts.append(int(match.group(1)))

        parsed_version = tuple(version_parts)
        return Version(parsed_version, constraint)
    except Exception:
        return Version((0, 0), "")


TELEMETRY_DATA = []
SCRIPT_DIR = os.path.dirname(__file__)
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
DDTRACE_REQUIREMENTS = {}
EXECUTABLES_DENY_LIST = set()
REQUIREMENTS_FILE_LOCATIONS = (
    os.path.abspath(os.path.join(SCRIPT_DIR, "../datadog-lib/requirements.csv")),
    os.path.abspath(os.path.join(SCRIPT_DIR, "requirements.csv")),
)
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


def python_version_is_compatible(marker_str, current_python_version_str):
    if not marker_str:
        return True
    # e.g. python_version>='3.13.0'
    m = re.match(r"python_version\\s*([<>=!~]+)\\s*'([^']*)'", marker_str)
    if not m:
        # Unsupported marker, assume it's compatible
        return True

    op, ver_str = m.groups()
    required_spec = Version(version=parse_version(ver_str).version, constraint=op)
    return requirement_is_compatible(required_spec, current_python_version_str)


def requirement_is_compatible(required_spec, installed_version_str):
    installed_version = parse_version(installed_version_str)
    constraint = required_spec.constraint
    if constraint in ("<", "<="):
        return True  # minimum "less than" means there is no minimum
    return installed_version.version >= required_spec.version


def build_requirements(current_python_version_str):
    requirements = dict()
    try:
        for location in REQUIREMENTS_FILE_LOCATIONS:
            if os.path.exists(location):
                with open(location, "r") as csvfile:
                    csv_reader = csv.reader(csvfile, delimiter=",")
                    next(csv_reader)  # Skip header
                    for row in csv_reader:
                        dep, version_spec, python_version_marker = row
                        if python_version_is_compatible(python_version_marker, current_python_version_str):
                            requirements[dep.lower()] = parse_version(version_spec)
                break
    except Exception as e:
        _log("Failed to build requirements list: %s" % e, level="debug")
    return requirements


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
    global DDTRACE_REQUIREMENTS
    global EXECUTABLES_DENY_LIST
    global TELEMETRY_DATA
    # Try to get the version of the Python runtime first so we have it for telemetry
    PYTHON_VERSION = platform.python_version()
    PYTHON_RUNTIME = platform.python_implementation().lower()
    DDTRACE_VERSION = get_oci_ddtrace_version()
    INSTALLED_PACKAGES = build_installed_pkgs()
    DDTRACE_REQUIREMENTS = build_requirements(PYTHON_VERSION)
    EXECUTABLES_DENY_LIST = build_denied_executables()
    integration_incomp = False
    runtime_incomp = False
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
        # enable safe instrumentation for ddtrace which won't patch incompatible integrations
        os.environ["DD_TRACE_SAFE_INSTRUMENTATION_ENABLED"] = "true"

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

        # check installed packages against allow list
        incompatible_packages = {}
        for package_name, package_version in INSTALLED_PACKAGES.items():
            supported_version_spec = DDTRACE_REQUIREMENTS.get(package_name.lower(), Version((0,), ""))
            if not requirement_is_compatible(supported_version_spec, package_version):
                incompatible_packages[package_name] = package_version

        if incompatible_packages:
            _log("Found incompatible ddtrace dependencies: %s." % incompatible_packages, level="debug")
            if not FORCE_INJECT:
                _log("Aborting dd-trace-py installation.", level="debug")
                abort = True

                for key, value in incompatible_packages.items():
                    TELEMETRY_DATA.append(
                        create_count_metric(
                            "library_entrypoint.abort",
                            [
                                "dependency:" + key,
                                "reason:" + value,
                            ],
                        )
                    )

            else:
                _log(
                    "DD_INJECT_FORCE set to True, allowing unsupported dependencies and continuing.",
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
        # Used to track whether the ddtrace package was successfully injected. Must be set before importing ddtrace
        os.environ["_DD_PY_SSI_INJECT"] = "1"
        try:
            import ddtrace  # noqa: F401

        except BaseException as e:
            os.environ["_DD_PY_SSI_INJECT"] = "0"
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
