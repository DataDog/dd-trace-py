import json
import os
import subprocess
import sys

import pytest

from tests.appsec.appsec_utils import flask_server


class PackageForTesting:
    package_name = ""
    package_version = ""
    url_to_test = ""
    expected_param = "test1234"
    expected_result1 = ""
    expected_result2 = ""
    extra_packages = []
    xfail = False

    def __init__(self, name, version, expected_param, expected_result1, expected_result2, extras=[]):
        self.package_name = name
        self.package_version = version
        if expected_param:
            self.expected_param = expected_param
        if expected_result1:
            self.expected_result1 = expected_result1
        if expected_result2:
            self.expected_result2 = expected_result2
        if extras:
            self.extra_packages = extras

    @property
    def url(self):
        return f"/{self.package_name}?package_param={self.expected_param}"

    def __repr__(self):
        return f"{self.package_name}: {self.url_to_test}"

    def _install(self, package_name, package_version):
        package_fullversion = package_name + "==" + package_version
        cmd = ["python", "-m", "pip", "install", package_fullversion]
        env = {}
        env.update(os.environ)
        # CAVEAT: we use subprocess instead of `pip.main(["install", package_fullversion])` due to pip package
        # doesn't work correctly with riot environment and python packages path
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, env=env)
        proc.wait()

    def install(self):
        self._install(self.package_name, self.package_version)
        for package_name, package_version in self.extra_packages:
            self._install(package_name, package_version)


PACKAGES = [
    PackageForTesting("charset-normalizer", "3.3.2", "my-bytes-string", "my-bytes-string", ""),
    PackageForTesting(
        "google-api-python-client",
        "2.111.0",
        "",
        "",
        "",
        extras=[("google-auth-oauthlib", "1.2.0"), ("google-auth-httplib2", "0.2.0")],
    ),
    PackageForTesting("idna", "3.6", "xn--eckwd4c7c.xn--zckzah", "ドメイン.テスト", "xn--eckwd4c7c.xn--zckzah"),
    PackageForTesting("numpy", "1.24.4", "9 8 7 6 5 4 3", [3, 4, 5, 6, 7, 8, 9], 5),
    PackageForTesting(
        "python-dateutil",
        "2.8.2",
        "Sat Oct 11 17:13:46 UTC 2003",
        "Sat, 11 Oct 2003 17:13:46 GMT",
        "And the Easter of that year is: 2004-04-11",
    ),
    PackageForTesting(
        "PyYAML",
        "6.0.1",
        '{"a": 1, "b": {"c": 3, "d": 4}}',
        {"a": 1, "b": {"c": 3, "d": 4}},
        "a: 1\nb:\n  c: 3\n  d: 4\n",
    ),
    PackageForTesting("requests", "2.31.0", "", "", ""),
    PackageForTesting(
        "urllib3",
        "2.1.0",
        "https://www.datadoghq.com/",
        ["https", None, "www.datadoghq.com", None, "/", None, None],
        "www.datadoghq.com",
    ),
    PackageForTesting("beautifulsoup4", "4.12.3", "<html></html>", "", ""),
]


def setup():
    for package in PACKAGES:
        package.install()

    for package in PACKAGES:
        with flask_server(
            iast_enabled="false", tracer_enabled="true", remote_configuration_enabled="false", token=None
        ) as context:
            _, client, pid = context

            response = client.get(package.url)

            assert response.status_code == 200
            content = json.loads(response.content)
            assert content["param"] == package.expected_param
            assert content["result1"] == package.expected_result1
            assert content["result2"] == package.expected_result2
            assert content["params_are_tainted"] is False


@pytest.mark.skipif(sys.version_info >= (3, 12, 0), reason="Package not yet compatible with Python 3.12")
@pytest.mark.parametrize("package", PACKAGES)
def test_packages_patched(package):
    if package.xfail:
        pytest.xfail("Initial test failed for package: {}".format(package))

    with flask_server(iast_enabled="true", remote_configuration_enabled="false", token=None) as context:
        _, client, pid = context

        response = client.get(package.url)

        assert response.status_code == 200
        content = json.loads(response.content)
        assert content["param"] == package.expected_param
        assert content["result1"] == package.expected_result1
        assert content["result2"] == package.expected_result2
        assert content["params_are_tainted"] is True
