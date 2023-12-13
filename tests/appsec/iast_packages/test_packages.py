import json

import pytest

from tests.appsec.appsec_utils import flask_server


class PackageForTesting:
    package_name = ""
    package_version = ""
    url_to_test = ""
    expected_param = "test1234"
    xfail = False

    def __init__(self, name):
        self.package_name = name

    @property
    def url(self):
        return f"/{self.package_name}?package_param={self.expected_param}"

    def __repr__(self):
        return f"{self.package_name}: {self.url_to_test}"


PACKAGES = [
    PackageForTesting("requests"),
]


def setup():
    for package in PACKAGES:
        with flask_server(
            iast_enabled="false", tracer_enabled="true", remote_configuration_enabled="false", token=None
        ) as context:
            _, client, pid = context

            try:
                response = client.get(package.url)

                assert response.status_code == 200
                content = json.loads(response.content)
                assert content["param"] == package.expected_param
                assert content["params_are_tainted"] is False
            except Exception:
                package.xfail = True


@pytest.mark.parametrize("package", PACKAGES)
def test_patched(package):
    if package.xfail:
        pytest.xfail("Initial test failed for package: {}".format(package))

    with flask_server(iast_enabled="true", remote_configuration_enabled="false", token=None) as context:
        _, client, pid = context

        response = client.get(package.url)

        assert response.status_code == 200
        content = json.loads(response.content)
        assert content["param"] == package.expected_param
        assert content["params_are_tainted"] is True
