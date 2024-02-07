#!/usr/bin/env python3
import json
import sys

import pytest

from tests.appsec.appsec_utils import flask_server


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
