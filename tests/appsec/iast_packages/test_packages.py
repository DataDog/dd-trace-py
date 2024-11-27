import json
import os
import shutil
import subprocess
import sys
import uuid

import clonevirtualenv
import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.constants import IAST_ENV
from tests.appsec.appsec_utils import flask_server
from tests.utils import override_env


PYTHON_VERSION = sys.version_info[:2]

# Add modules in the denylist that must be tested anyway
if IAST.PATCH_MODULES in os.environ:
    os.environ[IAST.PATCH_MODULES] += IAST.SEP_MODULES + IAST.SEP_MODULES.join(
        ["moto", "moto[all]", "moto[ec2]", "moto[s3]"]
    )
else:
    os.environ[IAST.PATCH_MODULES] = IAST.SEP_MODULES.join(["moto", "moto[all]", "moto[ec2]", "moto[s3]"])


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
    test_propagation = False
    expect_no_change = False

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
        test_propagation=False,
        fixme_propagation_fails=None,
        expect_no_change=False,
    ):
        self.name = name
        self.package_version = version
        self.test_import = test_import
        self.test_import_python_versions_to_skip = skip_python_version if skip_python_version else []
        self.test_e2e = test_e2e
        self.test_propagation = test_propagation
        self.fixme_propagation_fails = fixme_propagation_fails
        self.expect_no_change = expect_no_change

        if expected_param:
            self.expected_param = expected_param

        if expected_result1:
            self.expected_result1 = expected_result1

        if expected_result2:
            self.expected_result2 = expected_result2

        self.extra_packages = extras if extras else []

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

    @property
    def url_propagation(self):
        return f"/{self.name}_propagation?package_param={self.expected_param}"

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
_user_dir = os.path.expanduser("~")
PACKAGES = [
    PackageForTesting("asn1crypto", "1.5.1", "", "Ok", "", import_module_to_validate="asn1crypto.core"),
    PackageForTesting(
        "attrs",
        "23.2.0",
        "Bruce Dickinson",
        {"age": 65, "name": "Bruce Dickinson"},
        "",
        import_module_to_validate="attr.validators",
        test_propagation=True,
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
    PackageForTesting(
        "beautifulsoup4",
        "4.12.3",
        "<html></html>",
        "",
        "",
        import_name="bs4",
        test_propagation=True,
        fixme_propagation_fails=True,
    ),
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
    PackageForTesting(
        "cffi", "1.16.0", "", 30, "", import_module_to_validate="cffi.model", extras=[("setuptools", "72.1.0")]
    ),
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
        test_propagation=True,
    ),
    PackageForTesting("click", "8.1.7", "", "Hello World!\nHello World!\n", "", import_module_to_validate="click.core"),
    PackageForTesting(
        "cryptography",
        "42.0.7",
        "This is a secret message.",
        "This is a secret message.",
        "",
        import_module_to_validate="cryptography.fernet",
        test_propagation=True,
        fixme_propagation_fails=False,
    ),
    PackageForTesting(
        "distlib", "0.3.8", "", "Name: example-package\nVersion: 0.1", "", import_module_to_validate="distlib.util"
    ),
    PackageForTesting(
        "exceptiongroup",
        "1.2.1",
        "foobar",
        "ValueError: First error with foobar\nTypeError: Second error with foobar",
        "",
        import_module_to_validate="exceptiongroup._formatting",
        test_propagation=True,
    ),
    PackageForTesting(
        "filelock",
        "3.14.0",
        "foobar",
        "Lock acquired for file: foobar",
        "",
        import_module_to_validate="filelock._api",
    ),
    PackageForTesting("flask", "2.3.3", "", "", "", test_e2e=False, import_module_to_validate="flask.app"),
    PackageForTesting("fsspec", "2024.5.0", "", "/", ""),
    # PackageForTesting(
    # "google-auth",
    # "2.35.0",
    # "",
    # "",
    # "",
    # import_name="google.auth.crypt.rsa",
    # import_module_to_validate="google.auth.crypt.rsa",
    # expect_no_change=True,
    # ),
    # PackageForTesting(
    # "google-api-core",
    # "2.22.0",
    # "",
    # "",
    # "",
    # import_name="google",
    # import_module_to_validate="google.auth.iam",
    # extras=[("google-cloud-storage", "2.18.2")],
    # test_e2e=True,
    # ),
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
        test_propagation=True,
    ),
    PackageForTesting(
        "importlib-resources",
        "6.4.0",
        "foobar",
        "Content of foobar:\nThis is the default content of the file.",
        "",
        import_name="importlib_resources",
        skip_python_version=[(3, 8)],
        import_module_to_validate="importlib_resources.readers",
    ),
    PackageForTesting(
        "isodate",
        "0.6.1",
        "2023-06-15T13:45:30",
        "Parsed date and time: 2023-06-15 13:45:30",
        "",
        import_module_to_validate="isodate.duration",
    ),
    PackageForTesting(
        "itsdangerous",
        "2.2.0",
        "foobar",
        "Signed value: foobar.generated_signature\nUnsigned value: foobar",
        "",
        import_module_to_validate="itsdangerous.serializer",
    ),
    PackageForTesting(
        "jinja2",
        "3.1.4",
        "foobar",
        "Hello, foobar!",
        "",
        import_module_to_validate="jinja2.compiler",
        test_propagation=True,
        fixme_propagation_fails=True,
    ),
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
    PackageForTesting(
        "markupsafe",
        "2.1.5",
        "<script>alert('XSS')</script>",
        "Hello, &lt;script&gt;alert(&#39;XSS&#39;)&lt;/script&gt;!",
        "",
    ),
    PackageForTesting(
        "lxml",
        "5.2.2",
        "<root><element>foobar</element></root>",
        "Element text: foobar",
        "",
        import_name="lxml.etree",
        import_module_to_validate="lxml.doctestcompare",
        test_propagation=True,
        fixme_propagation_fails=True,
    ),
    PackageForTesting(
        "more-itertools",
        "10.2.0",
        "1,2,3,4,5,6",
        "Chunked sequence: [[1, 2], [3, 4], [5, 6]]",
        "",
        import_name="more_itertools",
        import_module_to_validate="more_itertools.more",
    ),
    PackageForTesting(
        "multidict",
        "6.0.5",
        "key1=value1",
        "MultiDict contents: {'key1': 'value1'}",
        "",
        import_module_to_validate="multidict._multidict_py",
        test_propagation=True,
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
    PackageForTesting(
        "oauthlib",
        "3.2.2",
        "my-client-id",
        "OAuth2 client created with client ID: my-client-id",
        "",
        import_module_to_validate="oauthlib.common",
    ),
    PackageForTesting(
        "openpyxl", "3.1.2", "foobar", "Written value: foobar", "", import_module_to_validate="openpyxl.chart.axis"
    ),
    PackageForTesting(
        "packaging",
        "24.0",
        "",
        {"is_version_valid": True, "requirement": "example-package>=1.0.0", "specifier": ">=1.0.0", "version": "1.2.3"},
        "",
    ),
    # Pandas dropped Python 3.8 support in pandas>2.0.3
    PackageForTesting("pandas", "2.2.2", "foobar", "Written value: foobar", "", skip_python_version=[(3, 8)]),
    PackageForTesting(
        "platformdirs",
        "4.2.2",
        "foobar-app",
        "User data directory for foobar-app: %s/.local/share/foobar-app" % _user_dir,
        "",
        import_module_to_validate="platformdirs.unix",
        test_propagation=True,
    ),
    PackageForTesting(
        "pluggy",
        "1.5.0",
        "foobar",
        "Hook result: Plugin received: foobar",
        "",
        import_module_to_validate="pluggy._hooks",
    ),
    PackageForTesting(
        "pyasn1",
        "0.6.0",
        "Bruce Dickinson",
        {"decoded_age": 65, "decoded_name": "Bruce Dickinson"},
        "",
        import_module_to_validate="pyasn1.codec.native.decoder",
        test_propagation=True,
        fixme_propagation_fails=True,
    ),
    PackageForTesting("pycparser", "2.22", "", "", ""),
    PackageForTesting(
        "pydantic",
        "2.7.1",
        '{"name": "foobar", "description": "A test item"}',
        "Validated item: name=foobar, description=A test item",
        "",
    ),
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
    # # TODO: patching Pytest fails: ImportError: cannot import name 'Dir' from '_pytest.main'
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
    PackageForTesting(
        "python-multipart",
        "0.0.5",  # this version validates APPSEC-55240 issue, don't upgrade it
        "multipart/form-data; boundary=d8b5635eb590e078a608e083351288a0",
        "d8b5635eb590e078a608e083351288a0",
        "",
        import_module_to_validate="multipart.multipart",
        test_propagation=True,
    ),
    PackageForTesting(
        "pytz",
        "2024.1",
        "America/New_York",
        "Current time in America/New_York: replaced_time",
        "",
    ),
    PackageForTesting(
        "PyYAML",
        "6.0.1",
        '{"a": 1, "b": {"c": 3, "d": 4}}',
        {"a": 1, "b": {"c": 3, "d": 4}},
        "a: 1\nb:\n  c: 3\n  d: 4\n",
        import_name="yaml",
        import_module_to_validate="yaml.resolver",
        test_propagation=True,
        fixme_propagation_fails=True,
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
        test_propagation=True,
    ),
    PackageForTesting(
        "sqlalchemy",
        "2.0.30",
        "Bruce Dickinson",
        {"age": 65, "id": 1, "name": "Bruce Dickinson"},
        "",
        import_module_to_validate="sqlalchemy.orm.session",
        test_propagation=True,
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
    # # TODO: Test import fails with
    # #   AttributeError: partially initialized module 'setuptools' has no
    # #   attribute 'dist' (most likely due to a circular import)
    PackageForTesting(
        "setuptools",
        "70.0.0",
        "",
        {"description": "An example package", "name": "example_package"},
        "",
        test_import=False,
    ),
    PackageForTesting(
        "tomli",
        "2.0.1",
        "key = 'value'",
        "Parsed TOML data: {'key': 'value'}",
        "",
        import_module_to_validate="tomli._parser",
        test_propagation=True,
    ),
    PackageForTesting(
        "tomlkit",
        "0.12.5",
        "key = 'value'",
        "Parsed TOML data: {'key': 'value'}",
        "",
        import_module_to_validate="tomlkit.items",
    ),
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
        "virtualenv",
        "20.26.2",
        "myenv",
        "Virtual environment created at replaced_path\nContents of replaced_path: replaced_contents",
        "",
        import_module_to_validate="virtualenv.activation.activator",
    ),
    # These show an issue in astunparse ("FormattedValue has no attribute values")
    # so we use ast.unparse which is only 3.9
    PackageForTesting(
        "soupsieve",
        "2.5",
        "<div><p>Example paragraph</p></div>",
        "Found 1 paragraph(s): Example paragraph",
        "",
        import_module_to_validate="soupsieve.css_match",
        extras=[("beautifulsoup4", "4.12.3")],
        skip_python_version=[(3, 6), (3, 7), (3, 8)],
        test_propagation=True,
    ),
    PackageForTesting(
        "werkzeug",
        "3.0.3",
        "your-password",
        "Original password: your-password\nHashed password: replaced_hashed\nPassword match: True",
        "",
        import_module_to_validate="werkzeug.http",
        skip_python_version=[(3, 6), (3, 7), (3, 8)],
    ),
    PackageForTesting(
        "yarl",
        "1.9.4",
        "https://example.com/path?query=param",
        "Original URL: https://example.com/path?query=param\nScheme: https\nHost:"
        + " example.com\nPath: /path\nQuery: <MultiDictProxy('query': 'param')>\n",
        "",
        import_module_to_validate="yarl._url",
        skip_python_version=[(3, 6), (3, 7), (3, 8)],
        test_propagation=True,
    ),
    PackageForTesting(
        "zipp",
        "3.18.2",
        "example.zip",
        "Contents of example.zip: ['example.zip/example.txt']",
        "",
        skip_python_version=[(3, 6), (3, 7), (3, 8)],
    ),
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
        "Hello, Pillow!",
        "Image correctly generated",
        "",
        import_name="PIL.Image",
        skip_python_version=[(3, 6), (3, 7), (3, 8)],
    ),
    PackageForTesting(
        "aiobotocore", "2.13.0", "", "", "", test_e2e=False, test_import=False, import_name="aiobotocore.session"
    ),
    PackageForTesting(
        "pyjwt",
        "2.8.0",
        "username123",
        "Encoded JWT: replaced_token\nDecoded payload: {'user': 'username123'}",
        "",
        import_name="jwt",
    ),
    PackageForTesting(
        "wrapt",
        "1.16.0",
        "some-value",
        "Function executed with param: some-value",
        "",
        test_propagation=True,
    ),
    PackageForTesting(
        "cachetools",
        "5.3.3",
        "some-key",
        "Computed value for some-key\nCached value for some-key: Computed value for some-key",
        "",
        test_propagation=True,
    ),
    # docutils dropped Python 3.8 support in docutils > 1.10.10.21.2
    PackageForTesting(
        "docutils",
        "0.21.2",
        "Hello, **world**!",
        "Conversion successful!",
        "",
        skip_python_version=[(3, 8)],
        test_propagation=True,
    ),
    ## TODO: https://datadoghq.atlassian.net/browse/APPSEC-53659
    ## Disabled due to a bug in CI:
    ## >           assert content["result1"].startswith(package.expected_result1)
    ## E           assert False
    ## E            +  where False = <built-in method startswith of str object at 0x7f223bc9d240>("Table data: {'column1': {0: 'some-value'}, 'column2': {0: 1}}")  # noqa: E501
    ## E            +    where <built-in method startswith of str object at 0x7f223bc9d240> = 'Error: numpy.dtype size changed, may indicate binary incompatibility. Expected 96 from C header, got 88 from PyObject'.startswith  # noqa: E501
    ## E            +    and   "Table data: {'column1': {0: 'some-value'}, 'column2': {0: 1}}" = pyarrow==16.1.0: .expected_result1  # noqa: E501
    # PackageForTesting(
    #     "pyarrow",
    #     "16.1.0",
    #     "some-value",
    #     "Table data: {'column1': {0: 'some-value'}, 'column2': {0: 1}}",
    #     "",
    #     extras=[("pandas", "1.1.5")],
    #     skip_python_version=[(3, 12)],  # pandas 1.1.5 does not work with Python 3.12
    # ),
    PackageForTesting("requests-oauthlib", "2.0.0", "", "", "", test_e2e=False, import_name="requests_oauthlib"),
    PackageForTesting(
        "pyparsing",
        "3.1.2",
        "123-456-7890",
        "Parsed phone number: ['123', '456', '7890']",
        "",
        test_propagation=True,
        fixme_propagation_fails=True,
    ),
    # TODO: e2e implemented but fails unpatched: "RateLimiter object has no attribute _is_allowed"
    PackageForTesting(
        "aiohttp",
        "3.9.5",
        "https://example.com",
        "foobar",
        "",
        test_e2e=False,
    ),
    # scipy dropped Python 3.8 support in scipy > 1.10.1
    PackageForTesting(
        "scipy",
        "1.13.0",
        "1,2,3,4,5",
        "Mean: 3.0, Standard Deviation: 1.581",
        "",
        import_name="scipy.special",
        skip_python_version=[(3, 8)],
    ),
    PackageForTesting(
        "iniconfig",
        "2.0.0",
        "test1234",
        "Parsed INI data: {'section': [('key', 'test1234')]}",
        "",
        test_propagation=True,
    ),
    PackageForTesting("psutil", "5.9.8", "cpu", "CPU Usage: replaced_usage", ""),
    PackageForTesting(
        "frozenlist",
        "1.4.1",
        "1,2,3",
        "Original list: <FrozenList(frozen=True, [1, 2, 3])> Attempt to modify frozen list!",
        "",
    ),
    # TODO: e2e implemented but fails unpatched: "Signal handlers results: None"
    PackageForTesting(
        "aiosignal",
        "1.3.1",
        "test_value",
        "Signal handlers results: [('Handler 1 called', None), ('Handler 2 called', None)]",
        "",
        test_e2e=False,
    ),
    PackageForTesting(
        "pygments",
        "2.18.0",
        "print('Hello, world!')",
        '<div class="highlight"><pre><span></span><span class="nb">print</span><span class="p">'
        '(</span><span class="s1">&#39;Hello, world!&#39;</span><span class="p">)</span>\n</pre></div>\n',
        "",
        test_propagation=True,
    ),
    PackageForTesting("grpcio", "1.64.0", "", "", "", test_e2e=False, import_name="grpc"),
    PackageForTesting(
        "pyopenssl",
        "24.1.0",
        "example.com",
        "Certificate: replaced_cert; Private Key: replaced_priv_key",
        "",
        import_name="OpenSSL.SSL",
    ),
    PackageForTesting(
        "moto[s3]",
        "5.0.11",
        "some_bucket",
        "right_result",
        "",
        import_name="moto.s3.models",
        test_e2e=True,
        extras=[("boto3", "1.34.143")],
    ),
    PackageForTesting("decorator", "5.1.1", "World", "Decorated result: Hello, World!", ""),
    # TODO: e2e implemented but fails unpatched: "RateLimiter object has no attribute _is_allowed"
    PackageForTesting(
        "requests-toolbelt", "1.0.0", "test_value", "", "", import_name="requests_toolbelt", test_e2e=False
    ),
    PackageForTesting(
        "pynacl",
        "1.5.0",
        "Hello, World!",
        "Key: replaced_key; Encrypted: replaced_encrypted; Decrypted: Hello, World!",
        "",
        import_name="nacl.utils",
        test_propagation=True,
        fixme_propagation_fails=True,
    ),
    # Requires "Annotated" from "typing" which was included in 3.9
    PackageForTesting(
        "annotated-types",
        "0.7.0",
        "15",
        "Processed value: 15",
        "",
        import_name="annotated_types",
        skip_python_version=[(3, 6), (3, 7), (3, 8)],
    ),
]

# Use this function if you want to test one or a filter number of package for debug proposes
# SKIP_FUNCTION = lambda package: package.name == "pynacl"  # noqa: E731
SKIP_FUNCTION = lambda package: True  # noqa: E731

# Turn this to True to don't delete the virtualenvs after the tests so debugging can iterate faster.
# Remember to set to False before pushing it!
_DEBUG_MODE = False


@pytest.fixture(scope="module")
def template_venv():
    """
    Create and configure a virtualenv template to be used for cloning in each test case
    """
    venv_dir = os.path.join(os.getcwd(), "template_venv")
    cloned_venvs_dir = os.path.join(os.getcwd(), "cloned_venvs")
    os.makedirs(cloned_venvs_dir, exist_ok=True)

    # Create virtual environment
    if not _DEBUG_MODE:
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
    if not _DEBUG_MODE:
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
        assert content["result1"].startswith(str(package.expected_result1))
    else:
        assert content["result1"] == package.expected_result1

    if type(content["result2"]) in (str, bytes):
        assert content["result2"].startswith(str(package.expected_result2))
    else:
        assert content["result2"] == package.expected_result2


def _assert_propagation_results(response, package):
    assert response.status_code == 200
    content = json.loads(response.content)
    result_ok = content["result1"] == "OK"
    if package.fixme_propagation_fails is not None:
        if result_ok:
            if package.fixme_propagation_fails:  # For packages that are reliably failing
                pytest.xfail(
                    "FIXME: Test passed unexpectedly, consider changing to fixme_propagation_fails=False for package %s"
                    % package.name
                )
            else:
                pytest.xfail("FIXME: Test passed unexpectedly for package %s" % package.name)
        else:
            # result not OK, so propagation is not yet working for the package
            pytest.xfail("FIXME: Test failed expectedly for package %s" % package.name)

    if not result_ok:
        print(f"Error: incorrect result from propagation endpoint for package {package.name}: {content}")
        print("Add the fixme_propagation_fail=True argument to the test dictionary entry or fix it")

    assert result_ok


# We need to set a different port for these tests of they can conflict with other tests using the flask server
# running in parallel (e.g. test_gunicorn_handlers.py)
_TEST_PORT = 8010


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
        python_cmd=venv,
        iast_enabled="false",
        tracer_enabled="true",
        remote_configuration_enabled="false",
        token=None,
        port=_TEST_PORT,
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
        python_cmd=venv, iast_enabled="true", remote_configuration_enabled="false", token=None, port=_TEST_PORT
    ) as context:
        _, client, pid = context
        response = client.get(package.url)
        _assert_results(response, package)


@pytest.mark.parametrize(
    "package",
    [package for package in PACKAGES if package.test_propagation and SKIP_FUNCTION(package)],
    ids=lambda package: package.name,
)
def test_flask_packages_propagation(package, venv, printer):
    should_skip, reason = package.skip
    if should_skip:
        pytest.skip(reason)
        return

    package.install(venv)
    with flask_server(
        python_cmd=venv, iast_enabled="true", remote_configuration_enabled="false", token=None, port=_TEST_PORT
    ) as context:
        _, client, pid = context
        response = client.get(package.url_propagation)
        _assert_propagation_results(response, package)


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

    cmdlist = [venv, _INSIDE_ENV_RUNNER_PATH, "unpatched", package.import_module_to_validate]

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

    cmdlist = [
        venv,
        _INSIDE_ENV_RUNNER_PATH,
        "patched",
        package.import_module_to_validate,
        "True" if package.expect_no_change else "False",
    ]

    with override_env({IAST_ENV: "true"}):
        # 1. Try with the specified version
        package.install(venv)
        result = subprocess.run(
            cmdlist,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout
        package.uninstall(venv)

        # 2. Try with the latest version
        package.install_latest(venv)
        result = subprocess.run(cmdlist, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout
