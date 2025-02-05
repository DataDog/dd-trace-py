import mock
import pytest

from ddtrace.internal.serverless import in_azure_function
from ddtrace.internal.serverless import in_gcp_function
from ddtrace.internal.serverless.mini_agent import get_rust_binary_path
from ddtrace.internal.serverless.mini_agent import maybe_start_serverless_mini_agent
from tests.utils import override_env


@mock.patch("ddtrace.internal.serverless.mini_agent.sys.platform", "linux")
@mock.patch("ddtrace.internal.serverless.mini_agent.Popen")
@mock.patch("ddtrace.internal.serverless.mini_agent.in_azure_function", lambda: False)
@mock.patch("ddtrace.internal.serverless.mini_agent.in_gcp_function", lambda: False)
def test_dont_spawn_mini_agent_if_not_cloud_or_azure_function(mock_popen):
    maybe_start_serverless_mini_agent()
    mock_popen.assert_not_called()


@mock.patch("ddtrace.internal.serverless.mini_agent.sys.platform", "linux")
@mock.patch("ddtrace.internal.serverless.mini_agent.Popen")
@mock.patch("ddtrace.internal.serverless.mini_agent.in_gcp_function", lambda: True)
def test_spawn_mini_agent_if_gcp_function(mock_popen):
    maybe_start_serverless_mini_agent()
    mock_popen.assert_called_once()


@mock.patch("ddtrace.internal.serverless.mini_agent.sys.platform", "linux")
@mock.patch("ddtrace.internal.serverless.mini_agent.Popen")
@mock.patch("ddtrace.internal.serverless.mini_agent.in_azure_function", lambda: True)
def test_spawn_mini_agent_if_azure_function(mock_popen):
    maybe_start_serverless_mini_agent()
    mock_popen.assert_called_once()


@mock.patch("ddtrace.internal.serverless.mini_agent.sys.platform", "linux")
@mock.patch("ddtrace.internal.serverless.mini_agent.PYTHON_VERSION_INFO", (9, 99, 0, "final", 0))
@mock.patch("ddtrace.internal.serverless.mini_agent.in_gcp_function", lambda: True)
def test_spawn_mini_agent_gcp_function_correct_rust_binary_path():
    path = get_rust_binary_path()
    expected_path = (
        "/layers/google.python.pip/pip/lib/python9.99/site-packages/"
        "datadog-serverless-agent-linux-amd64/datadog-serverless-trace-mini-agent"
    )
    assert path == expected_path


@mock.patch("ddtrace.internal.serverless.mini_agent.sys.platform", "linux")
@mock.patch("ddtrace.internal.serverless.mini_agent.in_azure_function", lambda: True)
def test_spawn_mini_agent_azure_function_linux_correct_rust_binary_path():
    path = get_rust_binary_path()
    expected_path = (
        "/home/site/wwwroot/.python_packages/lib/site-packages/"
        "datadog-serverless-agent-linux-amd64/datadog-serverless-trace-mini-agent"
    )
    assert path == expected_path


def test_is_deprecated_gcp_function():
    with override_env(dict(FUNCTION_NAME="test_function", GCP_PROJECT="project_name")):
        assert in_gcp_function() is True


def test_is_newer_gcp_function():
    with override_env(dict(K_SERVICE="test_function", FUNCTION_TARGET="function_target")):
        assert in_gcp_function() is True


def test_not_gcp_function():
    assert in_gcp_function() is False


def test_is_azure_function_consumption_plan():
    with override_env(dict(FUNCTIONS_WORKER_RUNTIME="python", FUNCTIONS_EXTENSION_VERSION="2")):
        assert in_azure_function() is True


def test_not_azure_function_consumption_plan():
    assert in_azure_function() is False


standard_blocklist = [
        "ddtrace.appsec._api_security.api_manager",
        "ddtrace.appsec._iast._ast.ast_patching",
        "ddtrace.internal.telemetry.telemetry_writer",
        "email.mime.application",
        "email.mime.multipart",
        "logging.handlers",
        "multiprocessing",
        "importlib_metadata",

        # These modules must not be imported because their source files are
        # specifically removed from the serverless python layer.
        # See https://github.com/DataDog/datadog-lambda-python/blob/main/Dockerfile
        "ddtrace.appsec._iast._taint_tracking._native",
        "ddtrace.appsec._iast._stacktrace",
        "ddtrace.internal.datadog.profiling.libdd_wrapper",
        "ddtrace.internal.datadog.profiling.ddup._ddup",
        "ddtrace.internal.datadog.profiling.stack_v2._stack_v2",
]
expanded_blocklist = standard_blocklist + [
        "importlib.metadata",
]

@pytest.mark.parametrize('package,blocklist', [
    ('ddtrace', expanded_blocklist),
    ('ddtrace.contrib.internal.aws_lambda', expanded_blocklist),
    ('ddtrace.contrib.internal.psycopg', expanded_blocklist),
    # requests imports urlib3 which imports importlib.metadata
    pytest.param('ddtrace.contrib.requests', standard_blocklist,
            # Currently this package will import `ddtrace.appsec._iast._taint_tracking._native`
            # and so is expected to fail for now. Once that is fixed and this test
            # begins to XPASS, the xfail should be removed.
            marks=pytest.mark.xfail(strict=True)),
])
def test_slow_imports(package, blocklist):
    # We should lazy load certain modules to avoid slowing down the startup
    # time when running in a serverless environment.  This test will fail if
    # any of those modules are imported during the import of ddtrace.
    import os
    import sys

    os.environ.update(
        {
            "AWS_LAMBDA_FUNCTION_NAME": "foobar",
            "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "False",
            "DD_API_SECURITY_ENABLED": "False",
        }
    )

    class BlockListFinder:
        def find_spec(self, fullname, *args):
            for lib in blocklist:
                if fullname == lib:
                    raise ImportError(f"module {fullname} was imported!")
            return None

    try:
        orig_meta_path = sys.meta_path
        sys.meta_path = [BlockListFinder()] + orig_meta_path

        for mod in sys.modules.copy():
            if mod in blocklist or mod.startswith("ddtrace"):
                del sys.modules[mod]

        __import__(package)

    finally:
        sys.meta_path = orig_meta_path
