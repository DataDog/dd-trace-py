"""
This module when included on the PYTHONPATH will update the PYTHONPATH to point to a directory
containing the ddtrace package compatible with the current Python version and platform.
"""
from __future__ import print_function  # noqa: E402

import json
import os
import platform
import subprocess
import sys
import time

import pkg_resources


# TODO: build list from riotfile instead of hardcoding
pkgs_allow_list = {
    "flask": "1.0",
}

runtimes_allow_list = {
    "cpython": ["3.7", "3.8", "3.9", "3.10", "3.11", "3.12"],
}

allow_unsupported_runtimes = os.environ.get("DD_TRACE_ALLOW_UNSUPPORTED_SSI_RUNTIMES", "").lower() in ("true", "1", "t")
allow_unsupported_integrations = os.environ.get("DD_TRACE_ALLOW_UNSUPPORTED_SSI_INTEGRATIONS", "").lower() in (
    "true",
    "1",
    "t",
)
installed_packages = pkg_resources.working_set
installed_packages = {pkg.key: pkg.version for pkg in installed_packages}

python_runtime = platform.python_implementation().lower()
python_version = ".".join(str(i) for i in sys.version_info[:2])


def create_count_metric(metric, tags={}):
    return {
        "metric": metric,
        "tags": tags,
    }


def gen_telemetry_payload(data):
    # could just expand the data dict here, might be better
    tags = data.get("tags", {})
    tags.update({"runtime": python_runtime, "platform": platform.system(), "python_version": python_version})
    return {
        "namespace": "tracers",
        "lib_language": "python",
        "lib_version": installed_packages.get("ddtrace", "unknown"),
        "series": [
            {
                "metric": data["metric"],
                "type": "count",
                "common": "true",
                "points": [[int(time.time()), 1]],
                "tags": tags,
            }
        ],
    }


def send_telemetry(event):
    event_json = json.dumps(event)
    print(event_json)
    p = subprocess.Popen(
        ["/home/kyle_verhoog_datadoghq_com/dd_telemetry", str(os.getpid())],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    p.communicate(input=event_json)


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
    skip_instr_events = []
    runtime_incomp_event = False
    integration_incomp_event = False
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
                # TODO: this checking code will have to be more intelligent in the future
                if package_version < pkgs_allow_list[package_name]:
                    incompatible_packages[package_name] = package_version
                    # incompatible_packages[package_name] = {
                    #     "version_in_use": package_version,
                    #     "required_version": pkgs_allow_list[package_name],
                    # }
        if incompatible_packages:
            _log("Found incompatible packages: %s." % incompatible_packages, level="debug")
            if not allow_unsupported_integrations:
                _log("Aborting dd-trace-py instrumentation.", level="debug")

                integration_incomp_event = True

                for key, value in incompatible_packages.items():
                    event_skipped_integration = gen_telemetry_payload(
                        create_count_metric(
                            "bootstrap.skipped.integration",
                            {
                                "integration_name": key,
                                "integration_version": value,
                                # need to add to doc if we want this
                                "required_version": pkgs_allow_list[key],
                            },
                        )
                    )
                    skip_instr_events.append(event_skipped_integration)

            else:
                _log(
                    "DD_TRACE_ALLOW_UNSUPPORTED_SSI_INTEGRATIONS set to True, allowing unsupported integrations.",
                    level="debug",
                )
        if python_version not in runtimes_allow_list.get(python_runtime, []):
            _log("Found incompatible runtime: %s %s." % (python_runtime, python_version), level="debug")
            if not allow_unsupported_runtimes:
                _log("Aborting dd-trace-py instrumentation.", level="debug")

                runtime_incomp_event = True

                event_skipped_runtime = gen_telemetry_payload(create_count_metric("bootstrap.skipped.runtime"))
                skip_instr_events.append(event_skipped_runtime)
            else:
                _log(
                    "DD_TRACE_ALLOW_UNSUPPORTED_SSI_RUNTIMES set to True, allowing unsupported runtimes.",
                    level="debug",
                )
        if skip_instr_events:
            for event in skip_instr_events:
                send_telemetry(event)
            skip_event = gen_telemetry_payload(
                create_count_metric(
                    "bootstrap.skipped",
                    {"runtime_skip": runtime_incomp_event, "integration_skip": integration_incomp_event},
                )
            )
            send_telemetry(skip_event)

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
                event = gen_telemetry_payload(create_count_metric("bootstrap.completed", {}))
                send_telemetry(event)
            except Exception as e:
                # maybe switch errortype since error will have too high over cardinality
                event = gen_telemetry_payload(create_count_metric("bootstrap.error", {"error": str(e)}))
                send_telemetry(event)
                _log("failed to load ddtrace.bootstrap.sitecustomize: %s" % e, level="error")
                return
    else:
        _log(
            "user-installed ddtrace found: %s, aborting site-packages injection" % ddtrace.__version__, level="warning"
        )


_inject()

# DD_TRACE_ALLOW_UNSUPPORTED_SSI_INTEGRATIONS=true DD_TRACE_DEBUG=true python3 service_a_success.py
