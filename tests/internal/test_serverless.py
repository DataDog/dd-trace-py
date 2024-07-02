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


# DEV: Run this test in a subprocess to avoid messing with global sys.modules state
@pytest.mark.subprocess()
def test_slow_imports():
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

    blocklist = [
        "ddtrace.appsec._api_security.api_manager",
        "ddtrace.appsec._iast._ast.ast_patching",
        "ddtrace.internal.telemetry.telemetry_writer",
        "email.mime.application",
        "email.mime.multipart",
        "logging.handlers",
        "multiprocessing",
        "importlib.metadata",
        "importlib_metadata",
    ]

    class BlockListFinder:
        def find_spec(self, fullname, *args):
            for lib in blocklist:
                if fullname == lib:
                    raise ImportError(f"module {fullname} was imported!")
            return None

    sys.meta_path.insert(0, BlockListFinder())

    import ddtrace
    import ddtrace.contrib.aws_lambda  # noqa:F401
    import ddtrace.contrib.psycopg  # noqa:F401
