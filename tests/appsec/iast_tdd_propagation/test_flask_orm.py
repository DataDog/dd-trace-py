#!/usr/bin/env python3
import json
import sys

import pytest

from tests.appsec.appsec_utils import flask_server


@pytest.mark.skipif(sys.version_info >= (3, 12, 0), reason="Package not yet compatible with Python 3.12")
@pytest.mark.parametrize("orm", ["sqlalchemy", "sqlite"])
def test_iast_flask_orm(orm):
    with flask_server(
        iast_enabled="true",
        tracer_enabled="true",
        remote_configuration_enabled="false",
        token=None,
        app="tests/appsec/iast_tdd_propagation/flask_orm_app.py",
        env={"FLASK_ORM": orm},
    ) as context:
        _, client, pid = context

        response = client.get("/?param=my-bytes-string")

    assert response.status_code == 200
    content = json.loads(response.content)
    assert content["param"] == "my-bytes-string"
    assert content["sources"] == "my-bytes-string"
    assert content["vulnerabilities"] == "SQL_INJECTION"
    assert content["params_are_tainted"] is True
