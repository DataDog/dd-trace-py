import json

import pip
import pytest

from tests.appsec.appsec_utils import flask_server


class PackageForTesting:
    package_name = ""
    package_version = ""
    url_to_test = ""
    expected_param = "test1234"
    expected_result1 = ""
    expected_result2 = ""
    xfail = False

    def __init__(self, name, version, expected_param, expected_result1, expected_result2):
        self.package_name = name
        self.package_version = version
        if expected_param:
            self.expected_param = expected_param
        if expected_result1:
            self.expected_result1 = expected_result1
        if expected_result2:
            self.expected_result2 = expected_result2

    @property
    def url(self):
        return f"/{self.package_name}?package_param={self.expected_param}"

    def __repr__(self):
        return f"{self.package_name}: {self.url_to_test}"

    def install(self):
        package_version = self.package_name + "==" + self.package_version
        if hasattr(pip, "main"):
            pip.main(["install", package_version])
        else:
            pip._internal.main(["install", package_version])


PACKAGES = [
    PackageForTesting("requests", "2.31.0", "", "", ""),
    PackageForTesting("idna", "3.6", "xn--eckwd4c7c.xn--zckzah", "ドメイン.テスト", "xn--eckwd4c7c.xn--zckzah"),
]


def setup():
    for package in PACKAGES:
        with flask_server(
            iast_enabled="false", tracer_enabled="true", remote_configuration_enabled="false", token=None
        ) as context:
            package.install()
            _, client, pid = context

            try:
                response = client.get(package.url)

                assert response.status_code == 200
                content = json.loads(response.content)
                assert content["param"] == package.expected_param
                assert content["result1"] == package.expected_result1
                assert content["result2"] == package.expected_result2
                assert content["params_are_tainted"] is False
            except Exception as e:
                package.xfail = True
                print(e)


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
