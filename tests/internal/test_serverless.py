import mock

from ddtrace.internal.serverless import in_azure_function_consumption_plan
from ddtrace.internal.serverless import in_gcp_function
from ddtrace.internal.serverless.mini_agent import get_rust_binary_path
from ddtrace.internal.serverless.mini_agent import maybe_start_serverless_mini_agent
from tests.utils import override_env


@mock.patch("ddtrace.internal.serverless.mini_agent.sys.platform", "linux")
@mock.patch("ddtrace.internal.serverless.mini_agent.Popen")
@mock.patch("ddtrace.internal.serverless.mini_agent.in_azure_function_consumption_plan", lambda: False)
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
@mock.patch("ddtrace.internal.serverless.mini_agent.in_azure_function_consumption_plan", lambda: True)
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
@mock.patch("ddtrace.internal.serverless.mini_agent.in_azure_function_consumption_plan", lambda: True)
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


def test_is_azure_function_consumption_plan_with_sku():
    with override_env(dict(FUNCTIONS_WORKER_RUNTIME="python", FUNCTIONS_EXTENSION_VERSION="2", WEBSITE_SKU="Dynamic")):
        assert in_azure_function_consumption_plan() is True


def test_is_azure_function_consumption_plan_no_sku():
    with override_env(dict(FUNCTIONS_WORKER_RUNTIME="python", FUNCTIONS_EXTENSION_VERSION="2")):
        assert in_azure_function_consumption_plan() is True


def test_not_azure_function_consumption_plan():
    assert in_azure_function_consumption_plan() is False


def test_not_azure_function_consumption_plan_wrong_sku():
    with override_env(dict(FUNCTIONS_WORKER_RUNTIME="python", FUNCTIONS_EXTENSION_VERSION="2", WEBSITE_SKU="Basic")):
        assert in_azure_function_consumption_plan() is False
