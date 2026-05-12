import os
from unittest import mock
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch as mock_patch

import pytest

from ddtrace.contrib.internal.claude_agent_sdk.patch import patch
from ddtrace.contrib.internal.claude_agent_sdk.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.contrib.claude_agent_sdk.utils import MOCK_ASSISTANT_MESSAGE_ERROR_SEQUENCE
from tests.contrib.claude_agent_sdk.utils import MOCK_BASH_TOOL_RESPONSE_SEQUENCE
from tests.contrib.claude_agent_sdk.utils import MOCK_CLIENT_RAW_MESSAGES
from tests.contrib.claude_agent_sdk.utils import MOCK_GREP_TOOL_RESPONSE_SEQUENCE
from tests.contrib.claude_agent_sdk.utils import MOCK_QUERY_RESPONSE_SEQUENCE
from tests.contrib.claude_agent_sdk.utils import MOCK_QUERY_RESPONSE_SEQUENCE_WITH_USAGE
from tests.contrib.claude_agent_sdk.utils import MOCK_STRUCTURED_OUTPUT_RESPONSE_SEQUENCE
from tests.contrib.claude_agent_sdk.utils import MOCK_TOOL_ERROR_RESPONSE_SEQUENCE
from tests.contrib.claude_agent_sdk.utils import MOCK_TOOL_USE_RESPONSE_SEQUENCE
from tests.contrib.claude_agent_sdk.utils import MOCK_TOOL_USE_WITH_FOLLOWUP_SEQUENCE
from tests.utils import override_env
from tests.utils import override_global_config


@pytest.fixture
def claude_agent_sdk_llmobs(tracer, monkeypatch):
    monkeypatch.setenv("_DD_LLMOBS_TEST_KEEP_META_STRUCT", "1")
    LLMObs.disable()
    with override_global_config(
        {
            "_llmobs_ml_app": "unnamed-ml-app",
            "_dd_api_key": "<not-a-real-key>",
            "service": "tests.llmobs",
        }
    ):
        LLMObs.enable(_tracer=tracer, integrations_enabled=False)
        LLMObs._instance._llmobs_span_writer.stop()
        LLMObs._instance._llmobs_span_writer = mock.MagicMock()
        yield LLMObs
    LLMObs.disable()


@pytest.fixture
def claude_agent_sdk():
    with override_env(
        dict(
            ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", "<not-a-real-key>"),
        )
    ):
        patch()
        import claude_agent_sdk

        yield claude_agent_sdk
        unpatch()


def _create_mock_internal_client(response_sequence):
    async def mock_process_query(self, prompt, options, transport=None):
        # consume async iterable prompt
        if hasattr(prompt, "__aiter__"):
            async for _ in prompt:
                pass

        for msg in response_sequence:
            yield msg

    return mock_patch(
        "claude_agent_sdk._internal.client.InternalClient.process_query",
        mock_process_query,
    )


@pytest.fixture
def mock_internal_client(claude_agent_sdk):
    with _create_mock_internal_client(MOCK_QUERY_RESPONSE_SEQUENCE):
        yield


@pytest.fixture
def mock_internal_client_tool_use(claude_agent_sdk):
    with _create_mock_internal_client(MOCK_TOOL_USE_RESPONSE_SEQUENCE):
        yield


@pytest.fixture
def mock_internal_client_bash_tool(claude_agent_sdk):
    with _create_mock_internal_client(MOCK_BASH_TOOL_RESPONSE_SEQUENCE):
        yield


@pytest.fixture
def mock_internal_client_grep_tool(claude_agent_sdk):
    with _create_mock_internal_client(MOCK_GREP_TOOL_RESPONSE_SEQUENCE):
        yield


@pytest.fixture
def mock_internal_client_structured_output(claude_agent_sdk):
    with _create_mock_internal_client(MOCK_STRUCTURED_OUTPUT_RESPONSE_SEQUENCE):
        yield


@pytest.fixture
def mock_internal_client_tool_use_with_followup(claude_agent_sdk):
    with _create_mock_internal_client(MOCK_TOOL_USE_WITH_FOLLOWUP_SEQUENCE):
        yield


@pytest.fixture
def mock_internal_client_tool_error(claude_agent_sdk):
    with _create_mock_internal_client(MOCK_TOOL_ERROR_RESPONSE_SEQUENCE):
        yield


@pytest.fixture
def mock_internal_client_with_usage(claude_agent_sdk):
    with _create_mock_internal_client(MOCK_QUERY_RESPONSE_SEQUENCE_WITH_USAGE):
        yield


@pytest.fixture
def mock_internal_client_assistant_message_error(claude_agent_sdk):
    with _create_mock_internal_client(MOCK_ASSISTANT_MESSAGE_ERROR_SEQUENCE):
        yield


@pytest.fixture
def mock_internal_client_error(claude_agent_sdk):
    async def mock_process_query_error(self, prompt, options, transport=None):
        raise ValueError("Connection failed")
        yield

    with mock_patch(
        "claude_agent_sdk._internal.client.InternalClient.process_query",
        mock_process_query_error,
    ):
        yield


@pytest.fixture
def mock_client(claude_agent_sdk):
    async def mock_receive_messages():
        for msg in MOCK_CLIENT_RAW_MESSAGES:
            yield msg

    client = claude_agent_sdk.ClaudeSDKClient()

    # mock query that handles receiving messages
    mock_query = MagicMock()
    mock_query.receive_messages = mock_receive_messages
    client._query = mock_query

    # mock transport that handles writing messages
    mock_transport = MagicMock()
    mock_transport.write = AsyncMock(return_value=None)
    client._transport = mock_transport

    return client


@pytest.fixture
def mock_client_error(claude_agent_sdk):
    client = claude_agent_sdk.ClaudeSDKClient()

    # mock query that handles receiving messages
    client._query = MagicMock()

    # mock transport that handles writing messages
    mock_transport = MagicMock()
    mock_transport.write = AsyncMock(side_effect=ValueError("Mocked transport error for testing"))
    client._transport = mock_transport

    return client
