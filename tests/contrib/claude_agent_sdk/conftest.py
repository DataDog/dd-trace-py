"""Pytest fixtures for claude_agent_sdk tests.

Since claude-agent-sdk uses subprocess/CLI transport (not HTTP), we mock
the internal transport layer instead of using VCR.
"""

import os
from unittest.mock import MagicMock
from unittest.mock import patch as mock_patch

import mock
import pytest

from ddtrace.contrib.internal.claude_agent_sdk.patch import patch
from ddtrace.contrib.internal.claude_agent_sdk.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.contrib.claude_agent_sdk.utils import MOCK_BASH_TOOL_RESPONSE_SEQUENCE
from tests.contrib.claude_agent_sdk.utils import MOCK_GREP_TOOL_RESPONSE_SEQUENCE
from tests.contrib.claude_agent_sdk.utils import MOCK_MULTI_TURN_RESPONSE_SEQUENCE
from tests.contrib.claude_agent_sdk.utils import MOCK_QUERY_RESPONSE_SEQUENCE
from tests.contrib.claude_agent_sdk.utils import MOCK_TOOL_USE_RESPONSE_SEQUENCE
from tests.utils import override_config
from tests.utils import override_env
from tests.utils import override_global_config


@pytest.fixture
def ddtrace_config_claude_agent_sdk():
    return {}


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def test_spans(ddtrace_global_config, test_spans):
    try:
        if ddtrace_global_config.get("_llmobs_enabled", False):
            # Have to disable and re-enable LLMObs to use to mock tracer.
            LLMObs.disable()
            LLMObs.enable(integrations_enabled=False)
        yield test_spans
    finally:
        LLMObs.disable()


@pytest.fixture
def mock_llmobs_writer(scope="session"):
    patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
    try:
        LLMObsSpanWriterMock = patcher.start()
        m = MagicMock()
        LLMObsSpanWriterMock.return_value = m
        yield m
    finally:
        patcher.stop()


def default_global_config():
    return {"_dd_api_key": "<not-a-real-api_key>"}


@pytest.fixture
def claude_agent_sdk(ddtrace_global_config, ddtrace_config_claude_agent_sdk):
    """Provides a patched claude_agent_sdk module for testing."""
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        with override_config("claude_agent_sdk", ddtrace_config_claude_agent_sdk):
            with override_env(
                dict(
                    ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", "<not-a-real-key>"),
                )
            ):
                patch()
                import claude_agent_sdk

                yield claude_agent_sdk
                unpatch()


@pytest.fixture
def mock_internal_client(claude_agent_sdk):
    """Mock the InternalClient.process_query method to avoid subprocess calls.

    This fixture patches the internal client's process_query method to return
    a mock async generator instead of actually spawning subprocess communication.
    """

    async def mock_process_query(self, prompt, options, transport=None):
        """Mock that yields predefined responses."""
        for msg in MOCK_QUERY_RESPONSE_SEQUENCE:
            yield msg

    with mock_patch(
        "claude_agent_sdk._internal.client.InternalClient.process_query",
        mock_process_query,
    ):
        yield


@pytest.fixture
def mock_internal_client_tool_use(claude_agent_sdk):
    """Mock for responses that include tool use (ToolUseBlock).

    Simulates Claude deciding to use a tool like Read, Write, Bash, etc.
    """

    async def mock_process_query(self, prompt, options, transport=None):
        """Mock that yields tool use response."""
        for msg in MOCK_TOOL_USE_RESPONSE_SEQUENCE:
            yield msg

    with mock_patch(
        "claude_agent_sdk._internal.client.InternalClient.process_query",
        mock_process_query,
    ):
        yield


@pytest.fixture
def mock_internal_client_multi_turn(claude_agent_sdk):
    """Mock for multi-turn conversation responses.

    Simulates a conversation with multiple turns (num_turns > 1).
    """

    async def mock_process_query(self, prompt, options, transport=None):
        """Mock that yields multi-turn response."""
        for msg in MOCK_MULTI_TURN_RESPONSE_SEQUENCE:
            yield msg

    with mock_patch(
        "claude_agent_sdk._internal.client.InternalClient.process_query",
        mock_process_query,
    ):
        yield


@pytest.fixture
def mock_internal_client_bash_tool(claude_agent_sdk):
    """Mock for Bash tool use responses.

    Real captured data from: .analysis/claude-agent-sdk/sample_app/artifacts/captured-bash-grep.json
    Bash tool input: {"command": "echo hello", "description": "Print 'hello' to standard output"}
    """

    async def mock_process_query(self, prompt, options, transport=None):
        for msg in MOCK_BASH_TOOL_RESPONSE_SEQUENCE:
            yield msg

    with mock_patch(
        "claude_agent_sdk._internal.client.InternalClient.process_query",
        mock_process_query,
    ):
        yield


@pytest.fixture
def mock_internal_client_grep_tool(claude_agent_sdk):
    """Mock for Grep tool use responses.

    Real captured data from: .analysis/claude-agent-sdk/sample_app/artifacts/captured-bash-grep.json
    Grep tool input: {"pattern": "def test_", "path": "tests", "output_mode": "content", "head_limit": 3}
    """

    async def mock_process_query(self, prompt, options, transport=None):
        for msg in MOCK_GREP_TOOL_RESPONSE_SEQUENCE:
            yield msg

    with mock_patch(
        "claude_agent_sdk._internal.client.InternalClient.process_query",
        mock_process_query,
    ):
        yield


@pytest.fixture
def mock_client_transport(claude_agent_sdk):
    """Mock the ClaudeSDKClient transport methods to avoid subprocess calls.

    This fixture mocks the transport layer so we can test ClaudeSDKClient.query
    without actually spawning Claude Code CLI.
    """
    mock_transport = MagicMock()
    mock_transport.write = MagicMock()

    async def mock_write(data):
        pass

    mock_transport.write = mock_write

    with mock_patch(
        "claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport",
        return_value=mock_transport,
    ):
        yield mock_transport
