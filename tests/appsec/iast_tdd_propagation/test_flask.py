#!/usr/bin/env python3
import json
import sys

import pytest

from tests.appsec.appsec_utils import flask_server


_PORT = 8060


@pytest.mark.skipif(sys.version_info >= (3, 12, 0), reason="Package not yet compatible with Python 3.12")
@pytest.mark.parametrize(
    "orm, xfail",
    [
        ("peewee", False),
        ("pony", False),
        ("sqlalchemy", False),
        ("sqlite", False),
        ("tortoise", True),  # TODO: Tortoise ORM is not yet supported
    ],
)
def test_iast_flask_orm(orm, xfail):
    with flask_server(
        iast_enabled="true",
        tracer_enabled="true",
        remote_configuration_enabled="false",
        token=None,
        app="tests/appsec/iast_tdd_propagation/flask_orm_app.py",
        env={"FLASK_ORM": orm},
        port=_PORT,
    ) as context:
        _, client, pid = context

        tainted_response = client.get("/?param=my-bytes-string")
        untainted_response = client.get("/untainted?param=my-bytes-string")

        assert untainted_response.status_code == 200
        content = json.loads(untainted_response.content)
        assert content["param"] == "my-bytes-string"
        assert content["sources"] == ""
        assert content["vulnerabilities"] == ""
        assert content["params_are_tainted"] is True

        assert tainted_response.status_code == 200
        content = json.loads(tainted_response.content)
        assert content["param"] == "my-bytes-string"
        if xfail:
            assert content["sources"] == ""
            assert content["vulnerabilities"] == ""
        else:
            assert content["sources"] == "my-bytes-string"
            assert content["vulnerabilities"] == "SQL_INJECTION"
        assert content["params_are_tainted"] is True


def test_iast_flask_weak_cipher():
    """Verify a segmentation fault on pycriptodome and AES"""
    with flask_server(
        iast_enabled="true",
        tracer_enabled="true",
        remote_configuration_enabled="false",
        token=None,
        app="tests/appsec/iast_tdd_propagation/flask_taint_sinks_app.py",
        port=_PORT,
    ) as context:
        server_process, client, pid = context
        for i in range(10):
            try:
                tainted_response = client.get("/?param=my-bytes-string")
            except Exception:
                pytest.fail(
                    "Server FAILED, see stdout and stderr logs"
                    "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
                    "\n=== Captured STDERR ===\n%s=== End of captured STDERR ==="
                    % (server_process.stdout, server_process.stderr)
                )
            assert tainted_response.status_code == 200
            content = json.loads(tainted_response.content)
            assert content["param"] == "my-bytes-string"
            assert content["sources"] == ""
            assert content["vulnerabilities"] == ""
            assert content["params_are_tainted"] is True

            weak_response = client.get("/weak_cipher?param=my-bytes-string")
            assert weak_response.status_code == 200
            content = json.loads(weak_response.content)
            assert content["sources"] == ""
            assert content["vulnerabilities"] == "WEAK_CIPHER"
            assert content["params_are_tainted"] is True


def test_iast_flask_headers():
    """Verify duplicated headers in the request"""
    with flask_server(
        iast_enabled="true",
        tracer_enabled="true",
        remote_configuration_enabled="false",
        token=None,
        # app="tests/appsec/iast_tdd_propagation/flask_propagation_app.py",
        app="flask_propagation_app.py",
        port=_PORT,
    ) as context:
        server_process, client, pid = context
        tainted_response = client.get("/check-headers", headers={"Accept-Encoding": "gzip, deflate, br"})

        assert tainted_response.status_code == 200
        content = json.loads(tainted_response.content)
        assert content["param"] == [
            ["Host", "0.0.0.0:8000"],
            ["User-Agent", "python-requests/2.31.0"],
            ["Accept-Encoding", "gzip, deflate, br"],
            ["Accept", "*/*"],
            ["Connection", "keep-alive"],
        ]
