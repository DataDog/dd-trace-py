import json

from tests.appsec.appsec_utils import flask_server

import pytest


PACKAGES = ["requests", ]


@pytest.mark.xfail(reason="Unpatched tests may fail, we expect this")
@pytest.mark.parametrize("package", PACKAGES)
def test_unpatched(package):
    with flask_server(iast_enabled="false", tracer_enabled="true", remote_configuration_enabled="false", token=None) as context:
        _, client, pid = context

        response = client.get("/")

        assert response.status_code == 200
        assert response.content == b"OK_index"

        expected_param = "test1234"
        response = client.get(f"/{package}?package_param={expected_param}")

        assert response.status_code == 200
        content = json.loads(response.content)
        assert content["param"] == expected_param
        assert content["params_are_tainted"] is False


@pytest.mark.parametrize("package", PACKAGES)
def test_patched(package):
    with flask_server(iast_enabled="true", remote_configuration_enabled="false", token=None) as context:
        _, client, pid = context

        response = client.get("/")

        assert response.status_code == 200
        assert response.content == b"OK_index"

        expected_param = "test1234"
        response = client.get(f"/{package}?package_param={expected_param}")

        assert response.status_code == 200
        content = json.loads(response.content)
        assert content["param"] == expected_param
        assert content["params_are_tainted"] is True