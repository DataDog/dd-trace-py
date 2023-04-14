from contextlib import contextmanager
import os
import subprocess

import requests
import tenacity


SERVER_URL = "http://0.0.0.0:8000/"


def _get_response():
    HEADERS = {
        "User-Agent": "dd-test-scanner-log",
    }
    r = requests.get(SERVER_URL, headers=HEADERS)
    r.raise_for_status()


@tenacity.retry(
    wait=tenacity.wait_fixed(1),
    stop=tenacity.stop_after_attempt(30),
)
def _wait():
    _get_response()


@contextmanager
def server(scenario, custom_post_response):
    env = {
        "PERF_TRACER_ENABLED": str(scenario.tracer_enabled),
        "PERF_PROFILER_ENABLED": str(scenario.profiler_enabled),
        "PERF_APPSEC_ENABLED": str(scenario.appsec_enabled),
        "PERF_IAST_ENABLED": str(scenario.iast_enabled),
        "PERF_TELEMETRY_METRICS_ENABLED": str(scenario.telemetry_metrics_enabled),
    }
    # copy over current environ
    env.update(os.environ)
    cmd = ["gunicorn", "-c", "gunicorn.conf.py"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env=env,
    )
    # make sure process has been started
    assert proc.poll() is None
    try:
        _wait()
        if scenario.post_request:
            response = custom_post_response
        else:
            response = _get_response
        yield response
    finally:
        proc.terminate()
        proc.wait()


def post_fork(server, worker):
    # Set lower defaults for ensuring profiler collect is run
    if os.environ.get("PERF_PROFILER_ENABLED") == "1":
        os.environ.update(
            {"DD_PROFILING_ENABLED": "1", "DD_PROFILING_API_TIMEOUT": "0.1", "DD_PROFILING_UPLOAD_INTERVAL": "10"}
        )
    if os.environ.get("PERF_APPSEC_ENABLED") == "1":
        os.environ.update({"DD_APPSEC_ENABLED ": "1"})
    if os.environ.get("PERF_IAST_ENABLED") == "1":
        os.environ.update({"DD_IAST_ENABLED ": "1"})
    if os.environ.get("PERF_TELEMETRY_METRICS_ENABLED") == "1":
        os.environ.update({"_DD_TELEMETRY_METRICS_ENABLED ": "1"})
    # This will not work with gevent workers as the gevent hub has not been
    # initialized when this hook is called.
    if os.environ.get("PERF_TRACER_ENABLED") == "1":
        import ddtrace.bootstrap.sitecustomize  # noqa


def post_worker_init(worker):
    # If profiling enabled but not tracer than only run auto script for profiler
    if os.environ.get("PERF_PROFILER_ENABLED") == "1" and os.environ.get("PERF_TRACER_ENABLED") == "0":
        import ddtrace.profiling.auto  # noqa
