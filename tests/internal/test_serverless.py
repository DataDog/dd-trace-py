import mock

from ddtrace.internal.serverless.mini_agent import maybe_start_serverless_mini_agent
from tests.utils import override_env


@mock.patch("ddtrace.internal.serverless.mini_agent.Popen")
def test_dont_spawn_mini_agent_if_not_cloud_function(mock_popen):
    # do not set any GCP environment variables
    maybe_start_serverless_mini_agent()
    mock_popen.assert_not_called()


@mock.patch("ddtrace.internal.serverless.mini_agent.Popen")
def test_spawn_mini_agent_if_deprecated_gcp_function_runtime(mock_popen):
    with override_env((dict(FUNCTION_NAME="test_function", GCP_PROJECT="project_name"))):
        maybe_start_serverless_mini_agent()
        mock_popen.assert_called_once()


@mock.patch("ddtrace.internal.serverless.mini_agent.Popen")
def test_spawn_mini_agent_if_newer_gcp_function_runtime(mock_popen):
    with override_env((dict(K_SERVICE="test_function", FUNCTION_TARGET="function_target"))):
        maybe_start_serverless_mini_agent()
        mock_popen.assert_called_once()


@mock.patch("ddtrace.internal.serverless.mini_agent.PYTHON_VERSION_INFO", (9, 99, 0, "final", 0))
@mock.patch("ddtrace.internal.serverless.mini_agent.os.getenv")
def test_spawn_mini_agent_correct_rust_binary_path(mock_os_getenv):
    with override_env((dict(K_SERVICE="test_function", FUNCTION_TARGET="function_target"))):
        maybe_start_serverless_mini_agent()
        expected_path = (
            "/layers/google.python.pip/pip/lib/python9.99/site-packages/"
            "datadog-serverless-agent-linux-amd64/datadog-serverless-trace-mini-agent"
        )
        mock_os_getenv.assert_called_once_with(
            "DD_MINI_AGENT_PATH",
            expected_path,
        )
