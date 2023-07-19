import mock

from ddtrace.internal.serverless import in_gcp_function
from ddtrace.internal.serverless import in_azure_function_consumption_plan
from ddtrace.internal.serverless.mini_agent import maybe_start_serverless_mini_agent
from tests.utils import override_env
from tests.utils import override_global_config


@mock.patch("ddtrace.internal.serverless.mini_agent.Popen")
def test_dont_spawn_mini_agent_if_not_cloud_function(mock_popen):
    with override_global_config(dict(_is_gcp_function=False, _is_azure_function_consumption_plan=False)):
        maybe_start_serverless_mini_agent()
        mock_popen.assert_not_called()


@mock.patch("ddtrace.internal.serverless.mini_agent.Popen")
def test_spawn_mini_agent_if_gcp_function(mock_popen):
    with override_global_config(dict(_is_gcp_function=True)):
        maybe_start_serverless_mini_agent()
        mock_popen.assert_called_once()


@mock.patch("ddtrace.internal.serverless.mini_agent.Popen")
def test_spawn_mini_agent_if_azure_function(mock_popen):
    with override_global_config(dict(_is_azure_function_consumption_plan=True)):
        maybe_start_serverless_mini_agent()
        mock_popen.assert_called_once()


@mock.patch("ddtrace.internal.serverless.mini_agent.PYTHON_VERSION_INFO", (9, 99, 0, "final", 0))
@mock.patch("ddtrace.internal.serverless.mini_agent.os.getenv")
def test_spawn_mini_agent_gcp_function_correct_rust_binary_path(mock_os_getenv):
    with override_global_config(dict(_is_gcp_function=True)):
        maybe_start_serverless_mini_agent()
        expected_path = (
            "/layers/google.python.pip/pip/lib/python9.99/site-packages/"
            "datadog-serverless-agent-linux-amd64/datadog-serverless-trace-mini-agent"
        )
        mock_os_getenv.assert_called_once_with(
            "DD_MINI_AGENT_PATH",
            expected_path,
        )


@mock.patch("ddtrace.internal.serverless.mini_agent.os.getenv")
def test_spawn_mini_agent_azure_function_correct_rust_binary_path(mock_os_getenv):
    with override_global_config(dict(_is_azure_function_consumption_plan=True)):
        maybe_start_serverless_mini_agent()
        expected_path = (
            "/home/site/wwwroot/.python_packages/lib/site-packages/"
            "datadog-serverless-agent-linux-amd64/datadog-serverless-trace-mini-agent"
        )
        mock_os_getenv.assert_called_once_with(
            "DD_MINI_AGENT_PATH",
            expected_path,
        )


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
    with override_env(dict(FUNCTIONS_WORKER_RUNTIME="python", FUNCTIONS_EXTENSION_VERSION="2", WEBSITE_SKU="Dynamic")):
        assert in_azure_function_consumption_plan() is True


def test_not_azure_function_consumption_plan():
    assert in_azure_function_consumption_plan() is False


def test_not_azure_function_consumption_plan_wrong_sku():
    with override_env(dict(FUNCTIONS_WORKER_RUNTIME="python", FUNCTIONS_EXTENSION_VERSION="2", WEBSITE_SKU="Basic")):
        assert in_azure_function_consumption_plan() is False
