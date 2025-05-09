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
    "ddtrace.internal.telemetry.writer",
    "email.mime.application",
    "email.mime.multipart",
    "http.client",
    "logging.handlers",
    "multiprocessing",
    "importlib_metadata",
    "ddtrace._trace.utils_botocore.span_pointers",
    "ddtrace._trace.utils_botocore.span_tags",
    # These modules must not be imported because their source files are
    # specifically removed from the serverless python layer.
    # See https://github.com/DataDog/datadog-lambda-python/blob/main/Dockerfile
    "ddtrace.appsec._iast._taint_tracking._native",
    "ddtrace.appsec._iast._stacktrace",
    "ddtrace.internal.datadog.profiling.libdd_wrapper",
    "ddtrace.internal.datadog.profiling.ddup._ddup",
    "ddtrace.internal.datadog.profiling.stack_v2._stack_v2",
    "ddtrace.internal._file_queue",
    "secrets",
]
expanded_blocklist = standard_blocklist + [
    "importlib.metadata",
]


@pytest.mark.parametrize(
    "package,blocklist",
    [
        ("ddtrace", expanded_blocklist),
        ("ddtrace.contrib.internal.aws_lambda", expanded_blocklist),
        ("ddtrace.contrib.internal.psycopg", expanded_blocklist),
        # requests imports urlib3 which imports importlib.metadata
        # TODO: Fix the requests parameter in a future PR
        # ("ddtrace.contrib.internal.requests", standard_blocklist),
    ],
)
def test_slow_imports(package, blocklist, run_python_code_in_subprocess):
    # We should lazy load certain modules to avoid slowing down the startup
    # time when running in a serverless environment.  This test will fail if
    # any of those modules are imported during the import of ddtrace.
    import os

    env = os.environ.copy()
    env.update(
        {
            "AWS_LAMBDA_FUNCTION_NAME": "foobar",
            "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "False",
            "DD_API_SECURITY_ENABLED": "False",
        }
    )

    code = f"""
import sys

blocklist = {blocklist}

class BlockListFinder:
    def find_spec(self, fullname, *args):
        if fullname in blocklist:
            raise ImportError(f"module {{fullname}} was imported!")
        return None

sys.meta_path = [BlockListFinder()] + sys.meta_path

import {package}
"""

    stderr, stdout, status, _ = run_python_code_in_subprocess(code, env=env)
    assert stdout.decode() == ""
    assert stderr.decode() == ""
    assert status == 0
