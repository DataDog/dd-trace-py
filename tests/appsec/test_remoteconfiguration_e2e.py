import base64
from contextlib import contextmanager
import datetime
import hashlib
import json
from multiprocessing.pool import ThreadPool
import os
import signal
import subprocess
import sys
import time
from typing import Optional  # noqa
import uuid

import pytest
import tenacity

from ddtrace import tracer
from ddtrace.internal.compat import httplib
from ddtrace.internal.compat import parse
from ddtrace.vendor import psutil
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
def gunicorn_server(appsec_enabled="true", remote_configuration_enabled="true", token=None):
    cmd = ["gunicorn", "-w", "3", "-b", "0.0.0.0:8000", "tests.appsec.app:app"]
    env = _build_env()
    env["DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS"] = "0.5"
    env["DD_REMOTE_CONFIGURATION_ENABLED"] = remote_configuration_enabled
    if token:
        env["_DD_REMOTE_CONFIGURATION_ADDITIONAL_HEADERS"] = "X-Datadog-Test-Session-Token:%s," % (token,)
    if appsec_enabled:
        env["DD_APPSEC_ENABLED"] = appsec_enabled
    env["DD_TRACE_AGENT_URL"] = os.environ.get("DD_TRACE_AGENT_URL", "")
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
        parent = psutil.Process(server_process.pid)
        children = parent.children(recursive=True)

        yield server_process, client, children[1].pid
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


def _get_agent_client():
    parsed = parse.urlparse(tracer._writer.agent_url)
    conn = httplib.HTTPConnection(parsed.hostname, parsed.port)
    return conn


def parse_payload(data):
    decoded = data
    if sys.version_info[1] == 5:
        decoded = data.decode("utf-8")
    return json.loads(decoded)


def _1_click_activation(token):
    path_1 = "datadog/2/ASM_FEATURES/blocked_users/config"
    msg_1 = {"asm": {"enabled": True}}
    client = _get_agent_client()
    client.request(
        "POST",
        "/test/session/responses/config/path?test_session_token=%s" % (token,),
        json.dumps({"path": path_1, "msg": msg_1}),
    )
    resp = client.getresponse()
    assert resp.status == 202


def _block_ip(token):
    client = _get_agent_client()
    client.request(
        "POST",
        "/test/session/responses/config/path?test_session_token=%s" % (token,),
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


def _block_ip_with_1_click_activation(token):
    expires_date = datetime.datetime.strftime(
        datetime.datetime.now() + datetime.timedelta(days=1), "%Y-%m-%dT%H:%M:%SZ"
    )
    path_1 = "datadog/2/ASM_FEATURES/blocked_users/config"
    path_2 = "datadog/2/ASM_DATA/blocked_users/config"
    msg_1 = {"asm": {"enabled": True}}
    msg_2 = {
        "rules_data": [
            {
                "data": [{"expiration": int(time.time()) + 1000000, "value": "123.45.67.88"}],
                "id": "blocked_ips",
                "type": "ip_with_expiration",
            }
        ]
    }
    msg_1_enc = bytes(json.dumps(msg_1), encoding="utf-8")
    msg_2_enc = bytes(json.dumps(msg_2), encoding="utf-8")
    data = {
        "signatures": [{"keyid": "", "sig": ""}],
        "signed": {
            "_type": "targets",
            "custom": {"opaque_backend_state": ""},
            "expires": expires_date,
            "spec_version": "1.0.0",
            "targets": {
                path_1: {
                    "custom": {"c": [""], "v": 0},
                    "hashes": {"sha256": hashlib.sha256(msg_1_enc).hexdigest()},
                    "length": 24,
                },
                path_2: {
                    "custom": {"c": [""], "v": 0},
                    "hashes": {"sha256": hashlib.sha256(msg_2_enc).hexdigest()},
                    "length": 24,
                },
            },
            "version": 0,
        },
    }
    remote_config_payload = {
        "roots": [
            str(
                base64.b64encode(
                    bytes(
                        json.dumps(
                            {
                                "signatures": [],
                                "signed": {
                                    "_type": "root",
                                    "consistent_snapshot": True,
                                    "expires": "1986-12-11T00:00:00Z",
                                    "keys": {},
                                    "roles": {},
                                    "spec_version": "1.0",
                                    "version": 2,
                                },
                            }
                        ),
                        encoding="utf-8",
                    )
                ),
                encoding="utf-8",
            )
        ],
        "targets": str(base64.b64encode(bytes(json.dumps(data), encoding="utf-8")), encoding="utf-8"),
        "target_files": [
            {
                "path": path_1,
                "raw": str(base64.b64encode(msg_1_enc), encoding="utf-8"),
            },
            {
                "path": path_2,
                "raw": str(base64.b64encode(msg_2_enc), encoding="utf-8"),
            },
        ],
        "client_configs": [path_1, path_2],
    }

    client = _get_agent_client()
    client.request(
        "POST",
        "/test/session/responses/config?test_session_token=%s" % (token,),
        json.dumps(remote_config_payload),
    )
    resp = client.getresponse()
    assert resp.status == 202


def _unblock_ip(token):
    client = _get_agent_client()
    client.request(
        "POST",
        "/test/session/responses/config/path?test_session_token=%s" % (token,),
        json.dumps({"path": "datadog/2/ASM_DATA/blocked_users/config", "msg": {"rules_data": []}}),
    )
    resp = client.getresponse()
    assert resp.status == 202


def _request(client):
    response = client.get("/", headers={"X-Forwarded-For": "123.45.67.88"})
    return response


def _multi_requests(client, debug_mode=False):
    if debug_mode:
        results = [
            _request(
                client,
            )
            for _ in range(10)
        ]
    else:
        pool = ThreadPool(processes=9)
        results_async = [pool.apply_async(_request, (client,)) for _ in range(50)]
        results = [res.get() for res in results_async]

    return results


def _request_200(client, debug_mode=False):
    results = _multi_requests(client, debug_mode)
    for response in results:
        assert response.status_code == 200
        assert response.content == b"OK"


def _request_403(client, debug_mode=False):
    results = _multi_requests(client, debug_mode)
    for response in results:
        assert response.status_code == 403
        assert response.content.startswith(b'{"errors": [{"title": "You\'ve been blocked"')


@pytest.mark.skipif(
    sys.version_info < (3, 7, 0) or sys.version_info >= (3, 11), reason="Gunicorn is only supported up to 3.10"
)
def test_load_testing_appsec_ip_blocking_gunicorn_rc_disabled():
    token = "test_load_testing_appsec_ip_blocking_gunicorn_rc_disabled_{}".format(str(uuid.uuid4()))
    with gunicorn_server(remote_configuration_enabled="false", token=token) as context:
        _, gunicorn_client, pid = context

        _request_200(gunicorn_client)

        _block_ip(token)

        time.sleep(1)

        _request_200(gunicorn_client)

        _unblock_ip(token)


@pytest.mark.skipif(
    sys.version_info < (3, 7, 0) or sys.version_info >= (3, 11), reason="Gunicorn is only supported up to 3.10"
)
def test_load_testing_appsec_ip_blocking_gunicorn_block():
    token = "test_load_testing_appsec_ip_blocking_gunicorn_block_{}".format(str(uuid.uuid4()))
    with gunicorn_server(token=token) as context:
        _, gunicorn_client, pid = context

        _request_200(gunicorn_client)

        _block_ip(token)

        _request_200(gunicorn_client)
        # TODO: this test doesn't work
        #  we're working to fix it in https://github.com/DataDog/dd-trace-py/pull/5464
        # time.sleep(1)
        #
        # _request_403(gunicorn_client)
        #
        # _unblock_ip(token)
        #
        # time.sleep(1)
        #
        # _request_200(gunicorn_client)


@pytest.mark.skipif(list(sys.version_info[:2]) != [3, 10], reason="Run this tests in python 3.10")
def test_load_testing_appsec_ip_blocking_gunicorn_block_and_kill_child_worker():
    token = "test_load_testing_appsec_ip_blocking_gunicorn_block_and_kill_child_worker_{}".format(str(uuid.uuid4()))
    with gunicorn_server(token=token) as context:
        _, gunicorn_client, pid = context

        _request_200(gunicorn_client)

        _block_ip(token)

        # _request_200(gunicorn_client)
        # TODO: this test doesn't work
        #  we're working to fix it in https://github.com/DataDog/dd-trace-py/pull/5464

        # time.sleep(1)
        #
        # _request_403(gunicorn_client)

        # os.kill(int(pid), signal.SIGTERM)
        #
        # time.sleep(2)
        #
        # _request_403(gunicorn_client)
        #
        # _unblock_ip(token)
        #
        # time.sleep(1)
        #
        # _request_200(gunicorn_client)


@pytest.mark.skipif(list(sys.version_info[:2]) != [3, 10], reason="Run this tests in python 3.10")
def test_load_testing_appsec_1click_and_ip_blocking_gunicorn_block_and_kill_child_worker():
    token = "test_load_testing_appsec_1click_and_ip_blocking_gunicorn_block_and_kill_child_worker_{}".format(
        str(uuid.uuid4())
    )
    with gunicorn_server(appsec_enabled="", token=token) as context:
        _, gunicorn_client, pid = context

        _request_200(gunicorn_client, debug_mode=False)

        _1_click_activation(token)

        time.sleep(1)

        _block_ip_with_1_click_activation(token)

        # _request_200(gunicorn_client, debug_mode=False)

        time.sleep(2)

        _request_403(gunicorn_client, debug_mode=False)

        os.kill(int(pid), signal.SIGTERM)

        # TODO: this test doesn't work
        #  we're working to fix it in https://github.com/DataDog/dd-trace-py/pull/5464
        # time.sleep(3)

        # _request_403(gunicorn_client, debug_mode=False)

        # _unblock_ip(token)
        #
        # time.sleep(1)
        #
        # _request_200(gunicorn_client, debug_mode=False)
