import json
import os
import shutil
import subprocess
import sys
import uuid

import clonevirtualenv
import pytest

from ddtrace.constants import IAST_ENV
from tests.appsec.appsec_utils import flask_server
from tests.utils import override_env


PYTHON_VERSION = sys.version_info[:2]


class PackageForTesting:
    name = ""
    import_name = ""
    package_version = ""
    url_to_test = ""
    expected_param = "test1234"
    expected_result1 = ""
    expected_result2 = ""
    extra_packages = []
    test_import = True
    test_import_python_versions_to_skip = []
    test_e2e = True

    def __init__(
        self,
        name,
        version,
        expected_param,
        expected_result1,
        expected_result2,
        extras=None,
        test_import=True,
        skip_python_version=None,
        test_e2e=True,
        import_name=None,
        import_module_to_validate=None,
    ):
        self.name = name
        self.package_version = version
        self.test_import = test_import
        self.test_import_python_versions_to_skip = skip_python_version if skip_python_version else []
        self.test_e2e = test_e2e

        if expected_param:
            self.expected_param = expected_param

        if expected_result1:
            self.expected_result1 = expected_result1

        if expected_result2:
            self.expected_result2 = expected_result2

        self.extra_packages = extras if extras else []
        print("JJJ self.extra_packages: ", self.extra_packages)

        if import_name:
            self.import_name = import_name
        else:
            self.import_name = self.name

        if import_module_to_validate:
            self.import_module_to_validate = import_module_to_validate
        else:
            self.import_module_to_validate = self.import_name

    @property
    def url(self):
        return f"/{self.name}?package_param={self.expected_param}"

    def __str__(self):
        return f"{self.name}=={self.package_version}: {self.url_to_test}"

    def __repr__(self):
        return f"{self.name}=={self.package_version}: {self.url_to_test}"

    @property
    def skip(self):
        for version in self.test_import_python_versions_to_skip:
            if version == PYTHON_VERSION:
                return True, f"{self.name} not yet compatible with Python {version}"
        return False, ""

    @staticmethod
    def _install(python_cmd, package_name, package_version=""):
        if package_version:
            package_fullversion = package_name + "==" + package_version
        else:
            package_fullversion = package_name

        cmd = [python_cmd, "-m", "pip", "install", package_fullversion]
        env = {}
        env.update(os.environ)
        # CAVEAT: we use subprocess instead of `pip.main(["install", package_fullversion])` due to pip package
        # doesn't work correctly with riot environment and python packages path
        proc = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr, close_fds=True, env=env)
        proc.wait()

    def install(self, python_cmd, install_extra=True):
        self._install(python_cmd, self.name, self.package_version)
        if install_extra:
            for package_name, package_version in self.extra_packages:
                self._install(python_cmd, package_name, package_version)

    def install_latest(self, python_cmd, install_extra=True):
        self._install(python_cmd, self.name)
        if install_extra:
            for package_name, package_version in self.extra_packages:
                self._install(python_cmd, package_name, package_version)

    def uninstall(self, python_cmd):
        try:
            cmd = [python_cmd, "-m", "pip", "uninstall", "-y", self.name]
            env = {}
            env.update(os.environ)
            proc = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr, close_fds=True, env=env)
            proc.wait()
        except Exception as e:
            print(f"Error uninstalling {self.name}: {e}")

        for package_name, _ in self.extra_packages:
            try:
                cmd = [python_cmd, "-m", "pip", "uninstall", "-y", package_name]
                proc = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr, close_fds=True, env=env)
                proc.wait()
            except Exception as e:
                print(f"Error uninstalling extra package {package_name}: {e}")


# Top packages list imported from:
# https://pypistats.org/top
# https://hugovk.github.io/top-pypi-packages/

# pypular package is discarded because it is not a real top package
# wheel, importlib-metadata and pip is discarded because they are package to build projects
# colorama and awscli are terminal commands
PACKAGES = [
    PackageForTesting("asn1crypto", "1.5.1", "", "", "", test_e2e=False, import_module_to_validate="asn1crypto.core"),
    PackageForTesting(
        "attrs",
        "23.2.0",
        "Bruce Dickinson",
        {"age": 65, "name": "Bruce Dickinson"},
        "",
        import_module_to_validate="attr.validators",
    ),
    PackageForTesting(
        "azure-core",
        "1.30.1",
        "",
        "",
        "",
        test_e2e=False,
        import_name="azure",
        import_module_to_validate="azure.core.settings",
    ),
    PackageForTesting("beautifulsoup4", "4.12.3", "<html></html>", "", "", import_name="bs4"),
    PackageForTesting(
        "boto3",
        "1.34.110",
        "",
        "",
        "",
        test_e2e=False,
        extras=[("pyopenssl", "24.1.0")],
        import_module_to_validate="boto3.session",
    ),
    PackageForTesting("botocore", "1.34.110", "", "", "", test_e2e=False),
    PackageForTesting("cffi", "1.16.0", "", 30, "", import_module_to_validate="cffi.model"),
    PackageForTesting(
        "certifi", "2024.2.2", "", "The path to the CA bundle is", "", import_module_to_validate="certifi.core"
    ),
    PackageForTesting(
        "charset-normalizer",
        "3.3.2",
        "my-bytes-string",
        "my-bytes-string",
        "",
        import_name="charset_normalizer",
        import_module_to_validate="charset_normalizer.api",
    ),
    PackageForTesting("click", "8.1.7", "", "", "", test_e2e=False, import_module_to_validate="click.core"),
    PackageForTesting(
        "cryptography",
        "42.0.7",
        "This is a secret message.",
        "This is a secret message.",
        "",
        import_module_to_validate="cryptography.fernet",
    ),
    PackageForTesting("distlib", "0.3.8", "", "", "", test_e2e=False, import_module_to_validate="distlib.util"),
    PackageForTesting(
        "exceptiongroup", "1.2.1", "", "", "", test_e2e=False, import_module_to_validate="exceptiongroup._formatting"
    ),
    PackageForTesting("filelock", "3.14.0", "", "", "", test_e2e=False, import_module_to_validate="filelock._api"),
    PackageForTesting("flask", "2.3.3", "", "", "", test_e2e=False, import_module_to_validate="flask.app"),
    PackageForTesting("fsspec", "2024.5.0", "", "/", ""),
    PackageForTesting(
        "google-api-core",
        "2.19.0",
        "",
        "",
        "",
        import_name="google",
        import_module_to_validate="google.auth.iam",
    ),
    PackageForTesting(
        "google-api-python-client",
        "2.111.0",
        "",
        "",
        "",
        extras=[("google-auth-oauthlib", "1.2.0"), ("google-auth-httplib2", "0.2.0"), ("cryptography", "42.0.7")],
        import_name="googleapiclient",
        import_module_to_validate="googleapiclient.discovery",
    ),
    PackageForTesting(
        "idna",
        "3.6",
        "xn--eckwd4c7c.xn--zckzah",
        "ドメイン.テスト",
        "xn--eckwd4c7c.xn--zckzah",
        import_module_to_validate="idna.codec",
    ),
    PackageForTesting(
        "importlib-resources",
        "6.4.0",
        "",
        "",
        "",
        test_e2e=False,
        import_name="importlib_resources",
        skip_python_version=[(3, 8)],
        import_module_to_validate="importlib_resources.readers",
    ),
    PackageForTesting("isodate", "0.6.1", "", "", "", test_e2e=False, import_module_to_validate="isodate.duration"),
    PackageForTesting(
        "itsdangerous", "2.2.0", "", "", "", test_e2e=False, import_module_to_validate="itsdangerous.serializer"
    ),
    PackageForTesting("jinja2", "3.1.4", "", "", "", test_e2e=False, import_module_to_validate="jinja2.compiler"),
    PackageForTesting("jmespath", "1.0.1", "", "Seattle", "", import_module_to_validate="jmespath.functions"),
    # jsonschema fails for Python 3.8
    #        except KeyError:
    # >           raise exceptions.NoSuchResource(ref=uri) from None
    # E           referencing.exceptions.NoSuchResource: 'http://json-schema.org/draft-03/schema#'
    PackageForTesting(
        "jsonschema",
        "4.22.0",
        "Bruce Dickinson",
        {
            "data": {"age": 65, "name": "Bruce Dickinson"},
            "schema": {
                "properties": {"age": {"type": "number"}, "name": {"type": "string"}},
                "required": ["name", "age"],
                "type": "object",
            },
            "validation": "successful",
        },
        "",
        skip_python_version=[(3, 8)],
    ),
    PackageForTesting("markupsafe", "2.1.5", "", "", "", test_e2e=False),
    PackageForTesting(
        "lxml",
        "5.2.2",
        "",
        "",
        "",
        test_e2e=False,
        import_name="lxml.etree",
        import_module_to_validate="lxml.doctestcompare",
    ),
    PackageForTesting(
        "more-itertools",
        "10.2.0",
        "",
        "",
        "",
        test_e2e=False,
        import_name="more_itertools",
        import_module_to_validate="more_itertools.more",
    ),
    PackageForTesting(
        "multidict", "6.0.5", "", "", "", test_e2e=False, import_module_to_validate="multidict._multidict_py"
    ),
    # Python 3.12 fails in all steps with "import error" when import numpy
    PackageForTesting(
        "numpy",
        "1.24.4",
        "9 8 7 6 5 4 3",
        [3, 4, 5, 6, 7, 8, 9],
        5,
        skip_python_version=[(3, 12)],
        import_module_to_validate="numpy.core._internal",
    ),
    PackageForTesting("oauthlib", "3.2.2", "", "", "", test_e2e=False, import_module_to_validate="oauthlib.common"),
    PackageForTesting("openpyxl", "3.1.2", "", "", "", test_e2e=False, import_module_to_validate="openpyxl.chart.axis"),
    PackageForTesting(
        "packaging",
        "24.0",
        "",
        {"is_version_valid": True, "requirement": "example-package>=1.0.0", "specifier": ">=1.0.0", "version": "1.2.3"},
        "",
    ),
    # Pandas dropped Python 3.8 support in pandas>2.0.3
    PackageForTesting("pandas", "2.2.2", "", "", "", test_e2e=False, skip_python_version=[(3, 8)]),
    PackageForTesting(
        "platformdirs", "4.2.2", "", "", "", test_e2e=False, import_module_to_validate="platformdirs.unix"
    ),
    PackageForTesting("pluggy", "1.5.0", "", "", "", test_e2e=False, import_module_to_validate="pluggy._hooks"),
    PackageForTesting(
        "pyasn1",
        "0.6.0",
        "Bruce Dickinson",
        {"decoded_age": 65, "decoded_name": "Bruce Dickinson"},
        "",
        import_module_to_validate="pyasn1.codec.native.decoder",
    ),
    PackageForTesting("pycparser", "2.22", "", "", ""),
    PackageForTesting("pydantic", "2.7.1", "", "", "", test_e2e=False),
    PackageForTesting(
        "pydantic-core",
        "2.18.2",
        "",
        "",
        "",
        test_e2e=False,
        import_name="pydantic_core",
        import_module_to_validate="pydantic_core.core_schema",
    ),
    # TODO: patching Pytest fails: ImportError: cannot import name 'Dir' from '_pytest.main'
    # PackageForTesting("pytest", "8.2.1", "", "", "", test_e2e=False),
    PackageForTesting(
        "python-dateutil",
        "2.8.2",
        "Sat Oct 11 17:13:46 UTC 2003",
        "Sat, 11 Oct 2003 17:13:46 GMT",
        "And the Easter of that year is: 2004-04-11",
        import_name="dateutil",
        import_module_to_validate="dateutil.relativedelta",
    ),
    PackageForTesting("pytz", "2024.1", "", "", "", test_e2e=False),
    PackageForTesting(
        "PyYAML",
        "6.0.1",
        '{"a": 1, "b": {"c": 3, "d": 4}}',
        {"a": 1, "b": {"c": 3, "d": 4}},
        "a: 1\nb:\n  c: 3\n  d: 4\n",
        import_name="yaml",
        import_module_to_validate="yaml.resolver",
    ),
    PackageForTesting(
        "requests",
        "2.31.0",
        "",
        "",
        "",
    ),
    PackageForTesting(
        "rsa",
        "4.9",
        "Bruce Dickinson",
        {"decrypted_message": "Bruce Dickinson", "message": "Bruce Dickinson"},
        "",
        import_module_to_validate="rsa.pkcs1",
    ),
    PackageForTesting(
        "sqlalchemy",
        "2.0.30",
        "Bruce Dickinson",
        {"age": 65, "id": 1, "name": "Bruce Dickinson"},
        "",
        import_module_to_validate="sqlalchemy.orm.session",
    ),
    PackageForTesting(
        "s3fs", "2024.5.0", "", "", "", extras=[("pyopenssl", "24.1.0")], import_module_to_validate="s3fs.core"
    ),
    PackageForTesting(
        "s3transfer",
        "0.10.1",
        "",
        "",
        "",
        extras=[("boto3", "1.34.110")],
    ),
    # TODO: Test import fails with
    #   AttributeError: partially initialized module 'setuptools' has no
    #   attribute 'dist' (most likely due to a circular import)
    PackageForTesting(
        "setuptools",
        "70.0.0",
        "",
        {"description": "An example package", "name": "example_package"},
        "",
        test_import=False,
    ),
    PackageForTesting("tomli", "2.0.1", "", "", "", test_e2e=False, import_module_to_validate="tomli._parser"),
    PackageForTesting("tomlkit", "0.12.5", "", "", "", test_e2e=False, import_module_to_validate="tomlkit.items"),
    PackageForTesting("tqdm", "4.66.4", "", "", "", test_e2e=False, import_module_to_validate="tqdm.std"),
    # Python 3.8 and 3.9 fail with ImportError: cannot import name 'get_host' from 'urllib3.util.url'
    PackageForTesting(
        "urllib3",
        "2.1.0",
        "https://www.datadoghq.com/",
        ["https", None, "www.datadoghq.com", None, "/", None, None],
        "www.datadoghq.com",
        skip_python_version=[(3, 8), (3, 9)],
    ),
    PackageForTesting(
        "virtualenv", "20.26.2", "", "", "", test_e2e=False, import_module_to_validate="virtualenv.activation.activator"
    ),
    # These show an issue in astunparse ("FormattedValue has no attribute values")
    # so we use ast.unparse which is only 3.9
    PackageForTesting(
        "soupsieve",
        "2.5",
        "",
        "",
        "",
        test_e2e=False,
        import_module_to_validate="soupsieve.css_match",
        extras=[("beautifulsoup4", "4.12.3")],
        skip_python_version=[(3, 6), (3, 7), (3, 8)],
    ),
    PackageForTesting(
        "werkzeug",
        "3.0.3",
        "",
        "",
        "",
        test_e2e=False,
        import_module_to_validate="werkzeug.http",
        skip_python_version=[(3, 6), (3, 7), (3, 8)],
    ),
    PackageForTesting(
        "yarl",
        "1.9.4",
        "",
        "",
        "",
        test_e2e=False,
        import_module_to_validate="yarl._url",
        skip_python_version=[(3, 6), (3, 7), (3, 8)],
    ),
    PackageForTesting("zipp", "3.18.2", "", "", "", test_e2e=False, skip_python_version=[(3, 6), (3, 7), (3, 8)]),
    PackageForTesting(
        "typing-extensions",
        "4.11.0",
        "",
        "",
        "",
        import_name="typing_extensions",
        test_e2e=False,
        skip_python_version=[(3, 6), (3, 7), (3, 8)],
    ),
    PackageForTesting(
        "six",
        "1.16.0",
        "",
        "We're in Python 3",
        "",
        skip_python_version=[(3, 6), (3, 7), (3, 8)],
    ),
    PackageForTesting(
        "pillow",
        "10.3.0",
        "",
        "",
        "",
        test_e2e=False,
        import_name="PIL.Image",
        skip_python_version=[(3, 6), (3, 7), (3, 8)],
    ),
    PackageForTesting(
        "aiobotocore", "2.13.0", "", "", "", test_e2e=False, test_import=False, import_name="aiobotocore.session"
    ),
    PackageForTesting("pyjwt", "2.8.0", "", "", "", test_e2e=False, import_name="jwt"),
    PackageForTesting("wrapt", "1.16.0", "", "", "", test_e2e=False),
    PackageForTesting("cachetools", "5.3.3", "", "", "", test_e2e=False),
    # docutils dropped Python 3.8 support in docutils > 1.10.10.21.2
    PackageForTesting("docutils", "0.21.2", "", "", "", test_e2e=False, skip_python_version=[(3, 8)]),
    PackageForTesting("pyarrow", "16.1.0", "", "", "", test_e2e=False),
    PackageForTesting("requests-oauthlib", "2.0.0", "", "", "", test_e2e=False, import_name="requests_oauthlib"),
    PackageForTesting("pyparsing", "3.1.2", "", "", "", test_e2e=False),
    PackageForTesting("aiohttp", "3.9.5", "", "", "", test_e2e=False),
    # scipy dropped Python 3.8 support in scipy > 1.10.1
    PackageForTesting(
        "scipy", "1.13.0", "", "", "", test_e2e=False, import_name="scipy.special", skip_python_version=[(3, 8)]
    ),
    PackageForTesting("iniconfig", "2.0.0", "", "", "", test_e2e=False),
    PackageForTesting("psutil", "5.9.8", "", "", "", test_e2e=False),
    PackageForTesting("frozenlist", "1.4.1", "", "", "", test_e2e=False),
    PackageForTesting("aiosignal", "1.3.1", "", "", "", test_e2e=False),
    PackageForTesting("pygments", "2.18.0", "", "", "", test_e2e=False),
    PackageForTesting("grpcio", "1.64.0", "", "", "", test_e2e=False, import_name="grpc"),
    PackageForTesting("pyopenssl", "24.1.0", "", "", "", test_e2e=False, import_name="OpenSSL.SSL"),
    PackageForTesting("decorator", "5.1.1", "", "", "", test_e2e=False),
    PackageForTesting("requests-toolbelt", "1.0.0", "", "", "", test_e2e=False, import_name="requests_toolbelt"),
    PackageForTesting("pynacl", "1.5.0", "", "", "", test_e2e=False, import_name="nacl.utils"),
    PackageForTesting("annotated-types", "0.7.0", "", "", "", test_e2e=False, import_name="annotated_types"),
]

# Use this function if you want to test one or a filter number of package for debug proposes
# SKIP_FUNCTION = lambda package: package.name == "pynacl"  # noqa: E731
SKIP_FUNCTION = lambda package: True  # noqa: E731


@pytest.fixture(scope="module")
def template_venv():
    """
    Create and configure a virtualenv template to be used for cloning in each test case
    """
    venv_dir = os.path.join(os.getcwd(), "template_venv")
    cloned_venvs_dir = os.path.join(os.getcwd(), "cloned_venvs")
    os.makedirs(cloned_venvs_dir, exist_ok=True)

    # Create virtual environment
    subprocess.check_call([sys.executable, "-m", "venv", venv_dir])
    pip_executable = os.path.join(venv_dir, "bin", "pip")
    this_dd_trace_py_path = os.path.join(os.path.dirname(__file__), "../../../")
    # Install dependencies.
    deps_to_install = [
        "flask",
        "attrs",
        "six",
        "cattrs",
        "pytest",
        "charset_normalizer",
        this_dd_trace_py_path,
    ]
    subprocess.check_call([pip_executable, "install", *deps_to_install])

    yield venv_dir

    # Cleanup: Remove the virtual environment directory after tests
    shutil.rmtree(venv_dir)


@pytest.fixture()
def venv(template_venv):
    """
    Clone the main template configured venv to each test case runs the package in a clean isolated environment
    """
    cloned_venvs_dir = os.path.join(os.getcwd(), "cloned_venvs")
    cloned_venv_dir = os.path.join(cloned_venvs_dir, str(uuid.uuid4()))
    clonevirtualenv.clone_virtualenv(template_venv, cloned_venv_dir)
    python_executable = os.path.join(cloned_venv_dir, "bin", "python")

    yield python_executable

    shutil.rmtree(cloned_venv_dir)


def _assert_results(response, package):
    assert response.status_code == 200
    content = json.loads(response.content)
    if type(content["param"]) in (str, bytes):
        assert content["param"].startswith(package.expected_param)
    else:
        assert content["param"] == package.expected_param

    if type(content["result1"]) in (str, bytes):
        assert content["result1"].startswith(package.expected_result1)
    else:
        assert content["result1"] == package.expected_result1

    if type(content["result2"]) in (str, bytes):
        assert content["result2"].startswith(package.expected_result2)
    else:
        assert content["result2"] == package.expected_result2


@pytest.mark.parametrize(
    "package",
    [package for package in PACKAGES if package.test_e2e and SKIP_FUNCTION(package)],
    ids=lambda package: package.name,
)
def test_flask_packages_not_patched(package, venv):
    should_skip, reason = package.skip
    if should_skip:
        pytest.skip(reason)
        return

    package.install(venv)
    with flask_server(
        python_cmd=venv, iast_enabled="false", tracer_enabled="true", remote_configuration_enabled="false", token=None
    ) as context:
        _, client, pid = context

        response = client.get(package.url)

        _assert_results(response, package)


@pytest.mark.parametrize(
    "package",
    [package for package in PACKAGES if package.test_e2e and SKIP_FUNCTION(package)],
    ids=lambda package: package.name,
)
def test_flask_packages_patched(package, venv):
    should_skip, reason = package.skip
    if should_skip:
        pytest.skip(reason)
        return

    package.install(venv)
    with flask_server(
        python_cmd=venv, iast_enabled="true", remote_configuration_enabled="false", token=None
    ) as context:
        _, client, pid = context
        response = client.get(package.url)
        _assert_results(response, package)


_INSIDE_ENV_RUNNER_PATH = os.path.join(os.path.dirname(__file__), "inside_env_runner.py")


@pytest.mark.parametrize(
    "package",
    [package for package in PACKAGES if package.test_import and SKIP_FUNCTION(package)],
    ids=lambda package: package.name,
)
def test_packages_not_patched_import(package, venv):
    should_skip, reason = package.skip
    if should_skip:
        pytest.skip(reason)
        return

    cmdlist = [venv, _INSIDE_ENV_RUNNER_PATH, "unpatched", package.import_name]

    # 1. Try with the specified version
    package.install(venv)
    result = subprocess.run(cmdlist, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
    package.uninstall(venv)

    # 2. Try with the latest version
    package.install_latest(venv)
    result = subprocess.run(cmdlist, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    "package",
    [package for package in PACKAGES if package.test_import and SKIP_FUNCTION(package)],
    ids=lambda package: package.name,
)
def test_packages_patched_import(package, venv):
    # TODO: create fixtures with exported patched code and compare it with the generated in the test
    # (only for non-latest versions)

    should_skip, reason = package.skip
    if should_skip:
        pytest.skip(reason)
        return

    cmdlist = [venv, _INSIDE_ENV_RUNNER_PATH, "patched", package.import_module_to_validate]

    with override_env({IAST_ENV: "true"}):
        # 1. Try with the specified version
        package.install(venv)
        result = subprocess.run(cmdlist, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout
        package.uninstall(venv)

        # 2. Try with the latest version
        package.install_latest(venv)
        result = subprocess.run(cmdlist, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout
