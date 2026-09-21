import os
import signal
import subprocess

import pytest
from sanic import __version__ as sanic_version

from tests.webclient import Client


RUN_SERVER_PY = os.path.abspath(os.path.join(os.path.dirname(__file__), "run_server.py"))
SERVER_PORT = 8000

sanic_version = tuple(map(int, sanic_version.split(".")))


@pytest.fixture()
def sanic_client():
    """Fixture for using sanic async HTTP server rather than a asgi async server used by test client"""
    env = os.environ.copy()
    env["SANIC_PORT"] = str(SERVER_PORT)
    args = ["ddtrace-run", "python", RUN_SERVER_PY]
    subp = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, env=env, preexec_fn=os.setsid
    )
    client = Client(f"http://0.0.0.0:{SERVER_PORT}")
    client.wait(path="/hello")
    try:
        yield client
    finally:
        resp = client.get_ignored("/shutdown-tracer")
        assert resp.status_code == 200
        # sanic server can spawn child processes, ensure child process are killed
        os.killpg(os.getpgid(subp.pid), signal.SIGTERM)
        try:
            # Give the server 3 seconds to shutdown, then kill it
            subp.wait(3)
        except subprocess.TimeoutExpired:
            subp.kill()


@pytest.mark.snapshot(
    ignores=["meta.http.useragent", "meta._dd.base_service"],
    variants={
        "": sanic_version >= (21, 9, 0),
        "pre_21.9": sanic_version < (21, 9, 0),
    },
)
def test_multiple_requests_sanic_http(sanic_client):
    def assert_response(response):
        assert response.status_code == 200
        assert response.json() == {"hello": "world"}

    url = f"http://0.0.0.0:{SERVER_PORT}/hello"
    assert_response(sanic_client.get(url))
    assert_response(sanic_client.get(url))


@pytest.mark.snapshot(
    ignores=["meta.error.stack", "meta.http.useragent", "meta._dd.base_service"],
    variants={
        "": sanic_version >= (21, 9, 0),
        "pre_21.9": sanic_version < (21, 9, 0),
    },
)
def test_sanic_errors(sanic_client):
    url = f"http://0.0.0.0:{SERVER_PORT}/not_found"
    response = sanic_client.get(url)
    assert response.status_code == 404

    url = f"http://0.0.0.0:{SERVER_PORT}/internal_error"
    response = sanic_client.get(url)
    assert response.status_code == 500
