import base64
import datetime
import hashlib
import http.client as httplib
import json
import os
import signal
import sys
import time
from urllib import parse
import uuid

import pytest

from ddtrace.trace import tracer
from tests.appsec.appsec_utils import gunicorn_server
from tests.appsec.integrations.flask_tests.utils import _PORT
from tests.appsec.integrations.flask_tests.utils import _multi_requests
from tests.appsec.integrations.flask_tests.utils import _request_200


def _get_agent_client():
    parsed = parse.urlparse(tracer._span_aggregator.writer.agent_url)
    conn = httplib.HTTPConnection(parsed.hostname, parsed.port)
    return conn


def parse_payload(data):
    return json.loads(data)


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


def _request_403(client, debug_mode=False, max_retries=40, sleep_time=1):
    """retry until it gets at least 2 successful checks"""
    time.sleep(sleep_time)
    previous = False
    for id_try in range(max_retries):
        results = _multi_requests(client, debug_mode)
        check = all(
            response.status_code == 403 and response.content.startswith(b'{"errors":[{"title":"You\'ve been blocked"')
            for response in results
        )
        if check:
            if previous:
                return
            previous = True
        else:
            previous = False
        time.sleep(sleep_time * pow(8, id_try / max_retries))
    raise AssertionError("request_403 failed, max_retries=%d, sleep_time=%f" % (max_retries, sleep_time))


@pytest.mark.skipif(sys.version_info >= (3, 11), reason="Gunicorn is only supported up to 3.10")
def test_load_testing_appsec_ip_blocking_gunicorn_rc_disabled():
    token = "test_load_testing_appsec_ip_blocking_gunicorn_rc_disabled_{}".format(str(uuid.uuid4()))
    with gunicorn_server(remote_configuration_enabled="false", token=token, port=_PORT) as context:
        _, gunicorn_client, pid = context

        _request_200(gunicorn_client)

        _block_ip(token)

        _request_200(gunicorn_client)

        _unblock_ip(token)


@pytest.mark.skipif(sys.version_info >= (3, 11), reason="Gunicorn is only supported up to 3.10")
def test_load_testing_appsec_ip_blocking_gunicorn_block():
    token = "test_load_testing_appsec_ip_blocking_gunicorn_block_{}".format(str(uuid.uuid4()))
    with gunicorn_server(token=token, port=_PORT) as context:
        _, gunicorn_client, pid = context

        _request_200(gunicorn_client)

        _block_ip(token)

        _request_403(gunicorn_client)

        _unblock_ip(token)

        _request_200(gunicorn_client)


@pytest.mark.skipif(list(sys.version_info[:2]) != [3, 10], reason="Run this tests in python 3.10")
def test_load_testing_appsec_ip_blocking_gunicorn_block_and_kill_child_worker():
    token = "test_load_testing_appsec_ip_blocking_gunicorn_block_and_kill_child_worker_{}".format(str(uuid.uuid4()))
    with gunicorn_server(token=token, port=_PORT) as context:
        _, gunicorn_client, pid = context

        _request_200(gunicorn_client)

        _block_ip(token)

        _request_403(gunicorn_client)

        os.kill(int(pid), signal.SIGTERM)

        _request_403(gunicorn_client)

        _unblock_ip(token)

        _request_200(gunicorn_client)


@pytest.mark.skip(reason="_request_403 is flaky, figure out the error. APPSEC-57052")
def test_load_testing_appsec_1click_and_ip_blocking_gunicorn_block_and_kill_child_worker():
    token = "test_load_testing_appsec_1click_and_ip_blocking_gunicorn_block_and_kill_child_worker_{}".format(
        str(uuid.uuid4())
    )
    with gunicorn_server(appsec_enabled="", token=token, port=_PORT) as context:
        _, gunicorn_client, pid = context

        _request_200(gunicorn_client, debug_mode=False)

        _1_click_activation(token)

        _block_ip_with_1_click_activation(token)

        # _request_200(gunicorn_client, debug_mode=False)

        _request_403(gunicorn_client, debug_mode=False)

        os.kill(int(pid), signal.SIGTERM)

        _request_403(gunicorn_client, debug_mode=False)

        _unblock_ip(token)

        _request_200(gunicorn_client, debug_mode=False)


@pytest.mark.subprocess(ddtrace_run=True, err=None, out=b"success")
def test_compatiblity_with_multiprocessing():
    import multiprocessing
    from multiprocessing import Array
    from multiprocessing import Process
    from multiprocessing import Value

    def f(n, a):
        n.value = 420
        for i, v in enumerate(a):
            a[i] = v * 10

    if __name__ == "__main__":
        multiprocessing.set_start_method("spawn")
        num = Value("d", 0.0)
        arr = Array("i", range(10))

        p = Process(target=f, args=(num, arr))
        p.start()
        p.join()

        assert arr[:] == [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
        assert num.value == 420
        print("success", end="")
