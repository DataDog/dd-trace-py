from contextlib import contextmanager
import os
import subprocess

import requests
import tenacity


SERVER_URL = "http://0.0.0.0:8000/"


def _get_response():
    r = requests.get(SERVER_URL)
    r.raise_for_status()


@tenacity.retry(
    wait=tenacity.wait_fixed(1),
    stop=tenacity.stop_after_attempt(30),
)
def _wait():
    _get_response()


@contextmanager
def server(scenario):
    env = {
        "PERF_TRACER_ENABLED": str(scenario.tracer_enabled),
        "PERF_PROFILER_ENABLED": str(scenario.profiler_enabled),
        "PERF_APPSEC_ENABLED": str(scenario.appsec_enabled),
        "PERF_IAST_ENABLED": str(scenario.iast_enabled),
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
        yield _get_response
    finally:
        proc.terminate()
        proc.wait()
