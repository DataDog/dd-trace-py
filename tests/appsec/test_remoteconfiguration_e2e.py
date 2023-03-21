from contextlib import contextmanager
import json
from multiprocessing.pool import ThreadPool
import os
import subprocess
import sys
import time
from typing import Optional  # noqa

import tenacity

from ddtrace.internal.compat import httplib
from ddtrace.internal.compat import parse
from tests.webclient import Client


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _build_env():
    environ = dict(PATH="%s:%s" % (ROOT_PROJECT_DIR, ROOT_DIR), PYTHONPATH="%s:%s" % (ROOT_PROJECT_DIR, ROOT_DIR))
    if os.environ.get("PATH"):
        environ["PATH"] = "%s:%s" % (os.environ.get("PATH"), environ["PATH"])
    if os.environ.get("PYTHONPATH"):
        environ["PYTHONPATH"] = "%s:%s" % (os.environ.get("PYTHONPATH"), environ["PYTHONPATH"])
    return environ


@contextmanager
def gunicorn_server():
    cmd = ["gunicorn", "-w", "3", "-b", "0.0.0.0:8000", "tests.appsec.app:app"]
    env = _build_env()
    env["DD_REMOTE_CONFIGURATION_ENABLED"] = "true"
    env["DD_APPSEC_ENABLED"] = "true"
    server_process = subprocess.Popen(
        cmd,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
        close_fds=True,
        preexec_fn=os.setsid,
    )
    try:
        client = Client("http://0.0.0.0:8000")
        try:
            print("Waiting for server to start")
            client.wait(max_tries=100, delay=0.1)
            print("Server started")
        except tenacity.RetryError:
            raise AssertionError(
                "Server failed to start, see stdout and stderr logs"
                "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
                "\n=== Captured STDERR ===\n%s=== End of captured STDERR ==="
                % (server_process.stdout, server_process.stderr)
            )
        time.sleep(1)
        yield server_process, client
        try:
            client.get_ignored("/shutdown")
        except Exception:
            raise AssertionError(
                "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
                "\n=== Captured STDERR ===\n%s=== End of captured STDERR ==="
                % (server_process.stdout, server_process.stderr)
            )
    finally:
        server_process.terminate()
        server_process.wait()


def _get_agent_client(tracer):
    parsed = parse.urlparse(tracer._writer.agent_url)
    conn = httplib.HTTPConnection(parsed.hostname, parsed.port)
    return conn


def parse_payload(data):
    decoded = data
    if sys.version_info[1] == 5:
        decoded = data.decode("utf-8")
    return json.loads(decoded)


def _block_ip(tracer):
    client = _get_agent_client(tracer)
    client.request(
        "POST",
        "/test/session/responses/config/path",
        json.dumps(
            {
                "path": "datadog/2/ASM_DATA/blocked_users/config",
                "msg": {
                    "rules_data": [
                        {
                            "data": [{"expiration": int(time.time()) + 1000000, "value": "123.45.67.88"}],
                            "id": "blocked_ips",
                            "type": "ip_with_expiration",
                        }
                    ]
                },
            }
        ),
    )
    resp = client.getresponse()
    assert resp.status == 202


def _unblock_ip(tracer):
    client = _get_agent_client(tracer)
    client.request(
        "POST",
        "/test/session/responses/config/path",
        json.dumps({"path": "datadog/2/ASM_DATA/blocked_users/config", "msg": {"rules_data": []}}),
    )
    resp = client.getresponse()
    assert resp.status == 202


def _request(client):
    response = client.get("/", headers={"X-Forwarded-For": "123.45.67.88"})
    return response


def _multi_requests(client):
    pool = ThreadPool(processes=9)
    async_result = [pool.apply_async(_request, (client,)) for _ in range(200)]

    return [res.get() for res in async_result]


def _request_200(client):
    results = _multi_requests(client)
    for response in results:
        assert response.status_code == 200
        assert response.content == b"OK"


def _request_403(client):
    results = _multi_requests(client)
    for response in results:
        assert response.status_code == 403
        assert response.content.startswith(b'\n{"errors": [{"title": "You\'ve been blocked"')


def test_no_known_errors_occur(tracer):
    with gunicorn_server() as context:
        _, gunicorn_client = context

        _request_200(gunicorn_client)

        _block_ip(tracer)

        _request_200(gunicorn_client)

        time.sleep(12)

        _request_403(gunicorn_client)

        _unblock_ip(tracer)

        time.sleep(3)

        _request_200(gunicorn_client)
