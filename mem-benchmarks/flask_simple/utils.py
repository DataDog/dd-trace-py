from contextlib import contextmanager
import os
import subprocess

import bm.utils as utils
import requests
import tenacity


SERVER_URL = "http://0.0.0.0:8000/"


def _get_response():
    r = requests.get(SERVER_URL)
    r.raise_for_status()


def _post_response():
    HEADERS = {
        "SERVER_PORT": "8000",
        "REMOTE_ADDR": "127.0.0.1",
        "CONTENT_TYPE": "application/json",
        "HTTP_HOST": "localhost:8000",
        "HTTP_ACCEPT": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
        "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
        "HTTP_SEC_FETCH_DEST": "document",
        "HTTP_ACCEPT_ENCODING": "gzip, deflate, br",
        "HTTP_ACCEPT_LANGUAGE": "en-US,en;q=0.9",
    }
    r = requests.post(SERVER_URL + "post-view", data=utils.EXAMPLE_POST_DATA, headers=HEADERS)
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
        if scenario.post_request:
            response = _post_response
        else:
            response = _get_response
        yield response
    finally:
        proc.terminate()
        proc.wait()
