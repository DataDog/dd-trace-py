import importlib
import json
import os
import subprocess
import sys

import pytest

from ddtrace.constants import IAST_ENV
from tests.appsec.appsec_utils import flask_server
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.utils import override_env


class PackageForTesting:
    package_name = ""
    import_name = ""
    package_version = ""
    url_to_test = ""
    expected_param = "test1234"
    expected_result1 = ""
    expected_result2 = ""
    extra_packages = []
    test_import = True
    test_e2e = True

    def __init__(
        self,
        name,
        version,
        expected_param,
        expected_result1,
        expected_result2,
        extras=[],
        test_import=True,
        test_e2e=True,
        import_name=None,
    ):
        self.package_name = name
        self.package_version = version
        self.test_import = test_import
        self.test_e2e = test_e2e
        if expected_param:
            self.expected_param = expected_param
        if expected_result1:
            self.expected_result1 = expected_result1
        if expected_result2:
            self.expected_result2 = expected_result2
        if extras:
            self.extra_packages = extras
        if import_name:
            self.import_name = import_name
        else:
            self.import_name = self.package_name

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
        proc = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr, close_fds=True, env=env)
        proc.wait()
        print(proc.stdout)
        print(proc.stderr)

    def install(self):
        self._install(self.package_name, self.package_version)
        for package_name, package_version in self.extra_packages:
            self._install(package_name, package_version)


# Top packages list imported from:
# https://pypistats.org/top
# https://hugovk.github.io/top-pypi-packages/

# pypular package is discarded because it is not a real top package
# wheel, importlib-metadata and pip is discarded because they are package to build projects
PACKAGES = [
    PackageForTesting(
        "charset-normalizer", "3.3.2", "my-bytes-string", "my-bytes-string", "", import_name="charset_normalizer"
    ),
    PackageForTesting(
        "google-api-python-client",
        "2.111.0",
        "",
        "",
        "",
        extras=[("google-auth-oauthlib", "1.2.0"), ("google-auth-httplib2", "0.2.0")],
        import_name="googleapiclient",
    ),
    PackageForTesting("idna", "3.6", "xn--eckwd4c7c.xn--zckzah", "ドメイン.テスト", "xn--eckwd4c7c.xn--zckzah"),
    # PackageForTesting("numpy", "1.24.4", "9 8 7 6 5 4 3", [3, 4, 5, 6, 7, 8, 9], 5),
    PackageForTesting(
        "python-dateutil",
        "2.8.2",
        "Sat Oct 11 17:13:46 UTC 2003",
        "Sat, 11 Oct 2003 17:13:46 GMT",
        "And the Easter of that year is: 2004-04-11",
        import_name="dateutil",
    ),
    PackageForTesting(
        "PyYAML",
        "6.0.1",
        '{"a": 1, "b": {"c": 3, "d": 4}}',
        {"a": 1, "b": {"c": 3, "d": 4}},
        "a: 1\nb:\n  c: 3\n  d: 4\n",
        import_name="yaml",
    ),
    PackageForTesting("requests", "2.31.0", "", "", ""),
    PackageForTesting(
        "urllib3",
        "2.1.0",
        "https://www.datadoghq.com/",
        ["https", None, "www.datadoghq.com", None, "/", None, None],
        "www.datadoghq.com",
    ),
    PackageForTesting("beautifulsoup4", "4.12.3", "<html></html>", "", "", import_name="bs4"),
    PackageForTesting("setuptools", "70.0.0", "", "", "", test_e2e=False),
    PackageForTesting("six", "1.16.0", "", "", "", test_e2e=False),
    PackageForTesting("s3transfer", "0.10.1", "", "", "", test_e2e=False),
    PackageForTesting("certifi", "2024.2.2", "", "", "", test_e2e=False),
    PackageForTesting("cryptography", "42.0.7", "", "", "", test_e2e=False),
    PackageForTesting("fsspec", "2024.5.0", "", "", "", test_e2e=False, test_import=False),
    PackageForTesting("boto3", "1.34.110", "", "", "", test_e2e=False, test_import=False),
    # PackageForTesting("typing-extensions", "4.11.0", "", "", "", import_name="typing_extensions", test_e2e=False),
    PackageForTesting("botocore", "1.34.110", "", "", "", test_e2e=False),
    PackageForTesting("packaging", "24.0", "", "", "", test_e2e=False),
    PackageForTesting("cffi", "1.16.0", "", "", "", test_e2e=False),
    PackageForTesting(
        "aiobotocore", "2.13.0", "", "", "", test_e2e=False, test_import=False, import_name="aiobotocore.session"
    ),
    PackageForTesting("s3fs", "2024.5.0", "", "", "", test_e2e=False, test_import=False),
    PackageForTesting("google-api-core", "2.19.0", "", "", "", test_e2e=False, import_name="google"),
    PackageForTesting("cffi", "1.16.0", "", "", "", test_e2e=False),
    PackageForTesting("pycparser", "2.22", "", "", "", test_e2e=False),
    # PackageForTesting("grpcio-status", "1.64.0", "", "", "", test_e2e=False),
    # PackageForTesting("pandas", "2.2.2", "", "", "", test_e2e=False),
    PackageForTesting("zipp", "3.18.2", "", "", "", test_e2e=False),
    PackageForTesting("attrs", "23.2.0", "", "", "", test_e2e=False),
    PackageForTesting("pyasn1", "0.6.0", "", "", "", test_e2e=False),
    PackageForTesting("rsa", "4.9", "", "", "", test_e2e=False),
    # PackageForTesting("protobuf", "5.26.1", "", "", "", test_e2e=False),
    PackageForTesting("jmespath", "1.0.1", "", "", "", test_e2e=False),
    PackageForTesting("click", "8.1.7", "", "", "", test_e2e=False),
    PackageForTesting("pydantic", "2.7.1", "", "", "", test_e2e=False),
    PackageForTesting("pytz", "2024.1", "", "", "", test_e2e=False),
    # PackageForTesting("colorama", "0.4.6", "", "", "", test_e2e=False),
    # PackageForTesting("awscli", "1.32.110", "", "", "", test_e2e=False),
    PackageForTesting("markupsafe", "2.1.5", "", "", "", test_e2e=False),
    PackageForTesting("jinja2", "3.1.4", "", "", "", test_e2e=False),
    PackageForTesting("platformdirs", "4.2.2", "", "", "", test_e2e=False),
    # PackageForTesting("pyjwt", "2.8.0", "", "", "", test_e2e=False, import_name="jwt"),
    PackageForTesting("tomli", "2.0.1", "", "", "", test_e2e=False),
    # PackageForTesting("googleapis-common-protos", "1.63.0", "", "", "", test_e2e=False),
    PackageForTesting("filelock", "3.14.0", "", "", "", test_e2e=False),
    # PackageForTesting("google-auth", "2.29.0", "", "", "", test_e2e=False),
    PackageForTesting("wrapt", "1.16.0", "", "", "", test_e2e=False),
    PackageForTesting("cachetools", "5.3.3", "", "", "", test_e2e=False),
    PackageForTesting("pluggy", "1.5.0", "", "", "", test_e2e=False),
    PackageForTesting("virtualenv", "20.26.2", "", "", "", test_e2e=False),
    # PackageForTesting("docutils", "0.21.2", "", "", "", test_e2e=False),
    # PackageForTesting("pyarrow", "16.1.0", "", "", "", test_e2e=False),
    PackageForTesting("exceptiongroup", "1.2.1", "", "", "", test_e2e=False),
    # PackageForTesting("jsonschema", "4.22.0", "", "", "", test_e2e=False),
    PackageForTesting("requests-oauthlib", "2.0.0", "", "", "", test_e2e=False, import_name="requests_oauthlib"),
    PackageForTesting("pyparsing", "3.1.2", "", "", "", test_e2e=False),
    PackageForTesting("pytest", "8.2.1", "", "", "", test_e2e=False),
    PackageForTesting("oauthlib", "3.2.2", "", "", "", test_e2e=False),
    PackageForTesting("sqlalchemy", "2.0.30", "", "", "", test_e2e=False),
    # PackageForTesting("pyasn1-modules", "0.4.0", "", "", "", test_e2e=False),
    PackageForTesting("aiohttp", "3.9.5", "", "", "", test_e2e=False),
    # PackageForTesting("scipy", "1.13.0", "", "", "", test_e2e=False, import_name="scipy.special"),
    # PackageForTesting("isodate", "0.6.1", "", "", "", test_e2e=False),
    PackageForTesting("multidict", "6.0.5", "", "", "", test_e2e=False),
    PackageForTesting("iniconfig", "2.0.0", "", "", "", test_e2e=False),
    PackageForTesting("psutil", "5.9.8", "", "", "", test_e2e=False),
    PackageForTesting("soupsieve", "2.5", "", "", "", test_e2e=False),
    PackageForTesting("yarl", "1.9.4", "", "", "", test_e2e=False),
    # PackageForTesting("async-timeout", "4.0.3", "", "", "", test_e2e=False),
    PackageForTesting("frozenlist", "1.4.1", "", "", "", test_e2e=False),
    PackageForTesting("aiosignal", "1.3.1", "", "", "", test_e2e=False),
    PackageForTesting("werkzeug", "3.0.3", "", "", "", test_e2e=False),
    # PackageForTesting("pillow", "10.3.0", "", "", "", test_e2e=False, import_name="PIL.Image"),
    PackageForTesting("tqdm", "4.66.4", "", "", "", test_e2e=False),
    PackageForTesting("pygments", "2.18.0", "", "", "", test_e2e=False),
    # PackageForTesting("grpcio", "1.64.0", "", "", "", test_e2e=False),
    PackageForTesting("greenlet", "3.0.3", "", "", "", test_e2e=False),
    PackageForTesting("pyopenssl", "24.1.0", "", "", "", test_e2e=False, import_name="OpenSSL.SSL"),
    PackageForTesting("flask", "3.0.3", "", "", "", test_e2e=False),
    PackageForTesting("decorator", "5.1.1", "", "", "", test_e2e=False),
    PackageForTesting("pydantic-core", "2.18.2", "", "", "", test_e2e=False, import_name="pydantic_core"),
    # PackageForTesting("lxml", "5.2.2", "", "", "", test_e2e=False, import_name="lxml.etree"),
    PackageForTesting("requests-toolbelt", "1.0.0", "", "", "", test_e2e=False, import_name="requests_toolbelt"),
    # PackageForTesting("openpyxl", "3.1.2", "", "", "", test_e2e=False, import_name="openpyxl.Workbook"),
    PackageForTesting("tzdata", "2024.1", "", "", "", test_e2e=False),
    # PackageForTesting("et-xmlfile", "1.1.0", "", "", "", test_e2e=False),
    # PackageForTesting("importlib-resources", "6.4.0", "", "", "", test_e2e=False, import_name="importlib_resources"),
    # PackageForTesting("proto-plus", "1.23.0", "", "", "", test_e2e=False),
    # PackageForTesting("asn1crypto", "1.5.1", "", "", "", test_e2e=False),
    PackageForTesting("coverage", "7.5.1", "", "", "", test_e2e=False),
    # PackageForTesting("azure-core", "1.30.1", "", "", "", test_e2e=False, import_name="azure"),
    PackageForTesting("distlib", "0.3.8", "", "", "", test_e2e=False),
    PackageForTesting("tomlkit", "0.12.5", "", "", "", test_e2e=False),
    # PackageForTesting("pynacl", "1.5.0", "", "", "", test_e2e=False),
    PackageForTesting("itsdangerous", "2.2.0", "", "", "", test_e2e=False),
    # PackageForTesting("annotated-types", "0.7.0", "", "", "", test_e2e=False),
    PackageForTesting("sniffio", "1.3.1", "", "", "", test_e2e=False),
    PackageForTesting("more-itertools", "10.2.0", "", "", "", test_e2e=False, import_name="more_itertools"),
    # PackageForTesting("google-cloud-storage", "2.16.0", "", "", "", test_e2e=False),
]


@pytest.mark.parametrize("package", [package for package in PACKAGES if package.test_e2e])
def test_packages_not_patched(package):
    package.install()
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


@pytest.mark.parametrize("package", [package for package in PACKAGES if package.test_e2e])
def test_packages_patched(package):
    package.install()
    with flask_server(iast_enabled="true", remote_configuration_enabled="false", token=None) as context:
        _, client, pid = context

        response = client.get(package.url)

        assert response.status_code == 200
        content = json.loads(response.content)
        assert content["param"] == package.expected_param
        assert content["result1"] == package.expected_result1
        assert content["result2"] == package.expected_result2
        assert content["params_are_tainted"] is True


@pytest.mark.parametrize("package", [package for package in PACKAGES if package.test_import])
def test_packages_not_patched_import(package):
    package.install()
    importlib.import_module(package.import_name)


@pytest.mark.parametrize("package", [package for package in PACKAGES if package.test_import])
def test_packages_patched_import(package):
    with override_env({IAST_ENV: "true"}):
        package.install()
        assert _iast_patched_module(package.import_name, fromlist=[])
