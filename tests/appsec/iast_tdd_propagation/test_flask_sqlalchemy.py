#!/usr/bin/env python3
import json
import sys

import pytest

from tests.appsec.appsec_utils import flask_server


@pytest.mark.skipif(sys.version_info >= (3, 2, 0), reason="Package not yet compatible with Python 3.12")
def test_iast_flask_sqlalchemy():
    with flask_server(
        iast_enabled="true",
        tracer_enabled="true",
        remote_configuration_enabled="false",
        token=None,
        app="tests/appsec/iast_tdd_propagation/flask_sqlalchemy_app.py",
    ) as context:
        _, client, pid = context

        response = client.get("/?param=my-bytes-string")

    assert response.status_code == 200
    content = json.loads(response.content)
    assert content["param"] == "my-bytes-string"
    assert content["result1"] == "my-bytes-string"
    assert content["result2"] == ""
    assert content["params_are_tainted"] is True


@pytest.mark.skipif(sys.version_info >= (3, 12, 0), reason="Package not yet compatible with Python 3.12")
def test_iast_flask_sqlite():
    with flask_server(
        iast_enabled="true",
        tracer_enabled="true",
        remote_configuration_enabled="false",
        token=None,
        app="tests/appsec/iast_tdd_propagation/flask_sqlite_app.py",
    ) as context:
        _, client, pid = context

        response = client.get("/?param=my-bytes-string")

    assert response.status_code == 200
    content = json.loads(response.content)
    assert content["param"] == "my-bytes-string"
    assert content["result1"] == "my-bytes-string"
    assert content["result2"] == ""
    assert content["params_are_tainted"] is True
