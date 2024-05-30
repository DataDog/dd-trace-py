"""
This module when included on the PYTHONPATH will update the PYTHONPATH to point to a directory
containing the ddtrace package compatible with the current Python version and platform.
"""
from __future__ import print_function  # noqa: E402

import csv
import json
import os
import platform
import subprocess
import sys
import time

import pkg_resources


runtimes_allow_list = {
    "cpython": {"min": "3.7", "max": "3.12"},
}

allow_unsupported_integrations = os.environ.get("DD_TRACE_ALLOW_UNSUPPORTED_SSI_INTEGRATIONS", "").lower() in (
    "true",
    "1",
    "t",
)
allow_unsupported_runtimes = os.environ.get("DD_TRACE_ALLOW_UNSUPPORTED_SSI_RUNTIMES", "").lower() in ("true", "1", "t")

installed_packages = pkg_resources.working_set
installed_packages = {pkg.key: pkg.version for pkg in installed_packages}

python_runtime = platform.python_implementation().lower()
python_version = ".".join(str(i) for i in sys.version_info[:2])


def build_min_pkgs():
    min_pkgs = dict()
    with open("datadog-lib/min_compatible_versions.csv", "r") as csvfile:
        csv_reader = csv.reader(csvfile, delimiter=",")
        for idx, row in enumerate(csv_reader):
            if idx < 2:
                continue
            min_pkgs[row[0]] = row[1]
    return min_pkgs


pkgs_allow_list = build_min_pkgs()


def create_count_metric(metric, tags=[]):
    return {
        "name": metric,
        "tags": tags,
    }


def gen_telemetry_payload(telemetry_events):
    return {
        "metadata": {
            "language_name": "python",
            "language_version": python_version,
            "runtime_name": python_runtime,
            "runtime_version": python_version,
            "tracer_name": "python",
            "tracer_version": installed_packages.get("ddtrace", "unknown"),
            "pid": os.getpid(),
        },
        "points": telemetry_events,
    }


def send_telemetry(event):
    event_json = json.dumps(event)
    print(event_json)
    p = subprocess.Popen(
        [os.environ.get("DD_TELEMETRY_FORWARDER_PATH"), str(os.getpid())],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    p.stdin.write(event_json)
    p.stdin.close()


debug_mode = os.environ.get("DD_TRACE_DEBUG", "").lower() in ("true", "1", "t")
# Python versions that are supported by the current ddtrace release


def _get_clib():
    """Return the C library used by the system.

    If GNU is not detected then returns MUSL.
    """

    libc, version = platform.libc_ver()
    if libc == "glibc":
        return "gnu"
    return "musl"


def _log(msg, *args, **kwargs):
    """Log a message to stderr.

    This function is provided instead of built-in Python logging since we can't rely on any logger
    being configured.
    """
    level = kwargs.get("level", "info")
    if debug_mode:
        asctime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        msg = "[%s] [%s] datadog.autoinstrumentation(pid: %d): " % (asctime, level.upper(), os.getpid()) + msg % args
        print(msg, file=sys.stderr)


def _inject():
    telemetry_data = []
    integration_incomp = False
    runtime_incomp = False
    try:
        import ddtrace
    except ImportError:
        _log("user-installed ddtrace not found, configuring application to use injection site-packages")

        platform = "manylinux2014" if _get_clib() == "gnu" else "musllinux_1_1"
        _log("detected platform %s" % platform, level="debug")

        script_dir = os.path.dirname(__file__)
        pkgs_path = os.path.join(script_dir, "ddtrace_pkgs")
        _log("ddtrace_pkgs path is %r" % pkgs_path, level="debug")
        _log("ddtrace_pkgs contents: %r" % os.listdir(pkgs_path), level="debug")

        # check installed packages against allow list
        incompatible_packages = {}
        for package_name, package_version in installed_packages.items():
            if package_name in pkgs_allow_list:
                if package_version < pkgs_allow_list[package_name]:
                    incompatible_packages[package_name] = package_version

        if incompatible_packages:
            _log("Found incompatible packages: %s." % incompatible_packages, level="debug")
            integration_incomp = True
            if not allow_unsupported_integrations:
                _log("Aborting dd-trace-py instrumentation.", level="debug")

                for key, value in incompatible_packages.items():
                    telemetry_data.append(
                        create_count_metric(
                            "library_entrypoint.abort.integration",
                            [
                                "integration_name:" + key,
                                "integration_version:" + value,
                                "min_supported_version:" + pkgs_allow_list[key],
                            ],
                        )
                    )

            else:
                _log(
                    "DD_TRACE_ALLOW_UNSUPPORTED_SSI_INTEGRATIONS set to True, allowing unsupported integrations.",
                    level="debug",
                )
        if python_version not in runtimes_allow_list.get(python_runtime, []):
            _log("Found incompatible runtime: %s %s." % (python_runtime, python_version), level="debug")
            runtime_incomp = True
            if not allow_unsupported_runtimes:
                _log("Aborting dd-trace-py instrumentation.", level="debug")

                telemetry_data.append(
                    create_count_metric(
                        "library_entrypoint.abort.runtime",
                        [
                            "min_supported_version:"
                            + runtimes_allow_list.get(python_runtime, {}).get("min", "unknown"),
                            "max_supported_version:"
                            + runtimes_allow_list.get(python_runtime, {}).get("max", "unknown"),
                        ],
                    )
                )
            else:
                _log(
                    "DD_TRACE_ALLOW_UNSUPPORTED_SSI_RUNTIMES set to True, allowing unsupported runtimes.",
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
            telemetry_event = gen_telemetry_payload(telemetry_data)
            send_telemetry(telemetry_event)
            return

        site_pkgs_path = os.path.join(pkgs_path, "site-packages-ddtrace-py%s-%s" % (python_version, platform))
        _log("site-packages path is %r" % site_pkgs_path, level="debug")
        if not os.path.exists(site_pkgs_path):
            _log("ddtrace site-packages not found in %r, aborting" % site_pkgs_path, level="error")
            return

        # Add the custom site-packages directory to the Python path to load the ddtrace package.
        sys.path.insert(0, site_pkgs_path)
        _log("sys.path %s" % sys.path, level="debug")
        try:
            import ddtrace  # noqa: F401

        except BaseException as e:
            _log("failed to load ddtrace module: %s" % e, level="error")
            return
        else:
            # In injected environments, the profiler needs to know that it is only allowed to use the native exporter
            os.environ["DD_PROFILING_EXPORT_LIBDD_REQUIRED"] = "true"
            # We should wrap this import at the very least in a try except block
            # This import has the same effect as ddtrace-run for the current process (auto-instrument all libraries).
            try:
                import ddtrace.bootstrap.sitecustomize

                # Modify the PYTHONPATH for any subprocesses that might be spawned:
                #   - Remove the PYTHONPATH entry used to bootstrap this installation as it's no longer necessary
                #     now that the package is installed.
                #   - Add the custom site-packages directory to PYTHONPATH to ensure the ddtrace package can be loaded
                #   - Add the ddtrace bootstrap dir to the PYTHONPATH to achieve the same effect as ddtrace-run.
                python_path = os.getenv("PYTHONPATH", "").split(os.pathsep)
                if script_dir in python_path:
                    python_path.remove(script_dir)
                python_path.insert(0, site_pkgs_path)
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
                    ]
                )
                send_telemetry(event)
            except Exception as e:
                # maybe switch errortype since error will have too high over cardinality
                event = gen_telemetry_payload(
                    [create_count_metric("library_entrypoint.error", ["error:" + type(e).__name__.lower()])]
                )
                send_telemetry(event)
                _log("failed to load ddtrace.bootstrap.sitecustomize: %s" % e, level="error")
                return
    else:
        _log(
            "user-installed ddtrace found: %s, aborting site-packages injection" % ddtrace.__version__, level="warning"
        )


_inject()
