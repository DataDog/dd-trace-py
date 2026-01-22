"""APM tests for claude_agent_sdk integration.

Tests for the standalone query() function and ClaudeSDKClient.query() method.
These tests mock the internal transport layer since the SDK uses subprocess/CLI.
"""

import pytest


@pytest.mark.asyncio
async def test_standalone_query_creates_span(claude_agent_sdk, mock_internal_client, test_spans):
    """Test that the standalone query() function creates a span.

    The query() function is an async generator that yields messages.
    We mock the internal client to avoid actual subprocess communication.
    This test verifies:
    1. Library behavior - all message types are received
    2. Span creation with correct name
    3. Span has no error on success
    4. Span has service tag set
    5. Error tags are absent on success
    """
    from claude_agent_sdk import AssistantMessage
    from claude_agent_sdk import ResultMessage
    from claude_agent_sdk import SystemMessage

    # Call the query function and consume all messages
    messages = []
    async for message in claude_agent_sdk.query(prompt="Hello, world!"):
        messages.append(message)

    # Verify library behavior - we got messages from the mock
    assert len(messages) >= 1, f"Expected at least 1 message, got {len(messages)}"
    # Verify message types (library still functions correctly)
    assert isinstance(messages[0], SystemMessage), (
        f"Expected first message to be SystemMessage, got {type(messages[0])}"
    )
    assert isinstance(messages[1], AssistantMessage), (
        f"Expected second message to be AssistantMessage, got {type(messages[1])}"
    )
    assert isinstance(messages[2], ResultMessage), (
        f"Expected third message to be ResultMessage, got {type(messages[2])}"
    )
    # Verify message content
    assert messages[1].content is not None, "AssistantMessage should have content"
    assert len(messages[1].content) > 0, "AssistantMessage content should not be empty"

    # Verify exactly 1 span was created (not duplicated)
    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected exactly 1 trace, got {len(spans)}"
    assert len(spans[0]) == 1, f"Expected exactly 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "claude_agent_sdk.query", f"Expected span name 'claude_agent_sdk.query', got '{span.name}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"
    # Verify service tag is set
    assert span.service is not None, "Expected service tag to be set"
    # Verify error tags are NOT present on success
    assert span.get_tag("error.type") is None, "Expected error.type tag to be absent on success"
    assert span.get_tag("error.message") is None, "Expected error.message tag to be absent on success"
    assert span.get_tag("error.stacktrace") is None, "Expected error.stacktrace tag to be absent on success"


@pytest.mark.asyncio
async def test_standalone_query_error_creates_span_with_error(claude_agent_sdk, test_spans):
    """Test that errors in query() create spans with error tags.

    When the internal client raises an exception, the span should record the error.
    """
    from unittest.mock import patch as mock_patch

    # Create a mock that raises an exception (self for instance method)
    async def mock_process_query_error(self, prompt, options, transport=None):
        raise ValueError("Mocked error for testing")
        yield  # Make it a generator

    with mock_patch(
        "claude_agent_sdk._internal.client.InternalClient.process_query",
        mock_process_query_error,
    ):
        with pytest.raises(ValueError) as exc_info:
            async for message in claude_agent_sdk.query(prompt="This will fail"):
                pass

    assert "Mocked error" in str(exc_info.value)

    # Verify exactly 1 span was created with error
    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected exactly 1 trace, got {len(spans)}"
    assert len(spans[0]) == 1, f"Expected exactly 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "claude_agent_sdk.query", f"Expected span name 'claude_agent_sdk.query', got '{span.name}'"
    assert span.error == 1, f"Expected error=1, got error={span.error}"
    assert span.get_tag("error.type") is not None, "Expected error.type tag to be set"
    assert span.get_tag("error.message") is not None, "Expected error.message tag to be set"


@pytest.mark.asyncio
async def test_client_query_creates_span(claude_agent_sdk, mock_internal_client, test_spans):
    """Test that ClaudeSDKClient query+receive_messages workflow creates a span.

    ClaudeSDKClient.query() starts a span, receive_messages() finishes it.
    This tests the client-based API separate from the standalone query() function.
    This test verifies:
    1. Library behavior - client workflow completes successfully without error
    2. Span creation with correct name
    3. Span has no error on success
    4. Span has service tag set
    """
    from unittest.mock import AsyncMock
    from unittest.mock import MagicMock
    from unittest.mock import patch as mock_patch

    from tests.contrib.claude_agent_sdk.utils import MOCK_MODEL

    # Mock the transport write method
    mock_write = AsyncMock(return_value=None)

    # Create a mock query object with receive_messages method
    # The SDK expects raw JSON data in the format the message parser expects
    async def mock_receive_messages():
        # SystemMessage
        yield {
            "type": "system",
            "subtype": "init",
            "cwd": "/test/path",
            "session_id": "test-session-id",
            "tools": ["Task", "Bash", "Read"],
            "model": MOCK_MODEL,
        }
        # AssistantMessage
        yield {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "4"}],
                "model": MOCK_MODEL,
            },
        }
        # ResultMessage
        yield {
            "type": "result",
            "subtype": "success",
            "duration_ms": 100,
            "duration_api_ms": 90,
            "is_error": False,
            "num_turns": 1,
            "session_id": "test-session-id",
        }

    mock_query = MagicMock()
    mock_query.receive_messages = mock_receive_messages

    # Create a mock client that simulates the connected state
    with mock_patch.object(claude_agent_sdk.ClaudeSDKClient, "connect", new_callable=AsyncMock):
        with mock_patch.object(claude_agent_sdk.ClaudeSDKClient, "disconnect", new_callable=AsyncMock):
            client = claude_agent_sdk.ClaudeSDKClient()
            # Simulate connected state with proper mock query object
            client._query = mock_query
            client._transport = MagicMock()
            client._transport.write = mock_write

            # Call query method - starts the span
            result = await client.query(prompt="Hello from client!")
            # Verify library behavior - method returns None as expected
            assert result is None, f"Expected query() to return None, got {result}"

            # Call receive_messages - finishes the span and yields responses
            messages = []
            async for message in client.receive_messages():
                messages.append(message)
            assert len(messages) >= 1, f"Expected at least 1 message, got {len(messages)}"

    # Verify exactly 1 span was created (query+receive_messages share one span)
    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected exactly 1 trace, got {len(spans)}"
    assert len(spans[0]) == 1, f"Expected exactly 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "claude_agent_sdk.request", f"Expected span name 'claude_agent_sdk.request', got '{span.name}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"
    # Verify service tag is set
    assert span.service is not None, "Expected service tag to be set"
    # Verify error tags are NOT present on success
    assert span.get_tag("error.type") is None, "Expected error.type tag to be absent on success"
    assert span.get_tag("error.message") is None, "Expected error.message tag to be absent on success"


@pytest.mark.asyncio
async def test_client_query_error_creates_span_with_error(claude_agent_sdk, test_spans):
    """Test that errors in ClaudeSDKClient.query() create spans with error tags.

    When the transport write fails, the span should record the error.
    """
    from unittest.mock import AsyncMock
    from unittest.mock import MagicMock
    from unittest.mock import patch as mock_patch

    # Mock the transport write method to raise an exception
    mock_write = AsyncMock(side_effect=ValueError("Mocked transport error for testing"))

    with mock_patch.object(claude_agent_sdk.ClaudeSDKClient, "connect", new_callable=AsyncMock):
        with mock_patch.object(claude_agent_sdk.ClaudeSDKClient, "disconnect", new_callable=AsyncMock):
            client = claude_agent_sdk.ClaudeSDKClient()
            # Simulate connected state
            client._query = True
            client._transport = MagicMock()
            client._transport.write = mock_write

            with pytest.raises(ValueError) as exc_info:
                await client.query(prompt="This will fail")

    assert "Mocked transport error" in str(exc_info.value)

    # Verify exactly 1 span was created with error
    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected exactly 1 trace, got {len(spans)}"
    assert len(spans[0]) == 1, f"Expected exactly 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "claude_agent_sdk.request", f"Expected span name 'claude_agent_sdk.request', got '{span.name}'"
    assert span.error == 1, f"Expected error=1, got error={span.error}"
    assert span.get_tag("error.type") is not None, "Expected error.type tag to be set"
    assert span.get_tag("error.message") is not None, "Expected error.message tag to be set"


@pytest.mark.asyncio
async def test_standalone_query_has_peer_service_tags(claude_agent_sdk, mock_internal_client, test_spans):
    """Test that the standalone query() function sets peer service precursor tags.

    Peer service tags are used by the PeerServiceProcessor to compute peer.service.
    The integration should set:
    - span.service = service name (semantic tag)
    - span.kind = "client" (outbound request)
    - out.host = service identifier for peer service computation
    """
    from ddtrace.constants import SPAN_KIND
    from ddtrace.ext import SpanKind
    from ddtrace.ext import net

    # Call the query function and consume all messages
    messages = []
    async for message in claude_agent_sdk.query(prompt="Hello, world!"):
        messages.append(message)

    # Verify exactly 1 span was created with peer service tags
    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected exactly 1 trace, got {len(spans)}"
    assert len(spans[0]) == 1, f"Expected exactly 1 span, got {len(spans[0])}"

    span = spans[0][0]
    # Verify span.service semantic tag is set (required by APM standards)
    assert span.service is not None, "Expected span.service tag to be set"
    # Verify span.kind is CLIENT (required for peer service)
    assert span.get_tag(SPAN_KIND) == SpanKind.CLIENT, (
        f"Expected span.kind '{SpanKind.CLIENT}', got '{span.get_tag(SPAN_KIND)}'"
    )
    # Verify out.host is set (peer service precursor tag)
    assert span.get_tag(net.TARGET_HOST) is not None, "Expected out.host tag to be set for peer service"
    assert span.get_tag(net.TARGET_HOST) == "api.anthropic.com", (
        f"Expected out.host to be 'api.anthropic.com', got '{span.get_tag(net.TARGET_HOST)}'"
    )


@pytest.mark.asyncio
async def test_client_query_has_peer_service_tags(claude_agent_sdk, mock_internal_client, test_spans):
    """Test that ClaudeSDKClient workflow sets peer service precursor tags.

    Peer service tags are used by the PeerServiceProcessor to compute peer.service.
    The integration should set:
    - span.service = service name (semantic tag)
    - span.kind = "client" (outbound request)
    - out.host = service identifier for peer service computation
    """
    from unittest.mock import AsyncMock
    from unittest.mock import MagicMock
    from unittest.mock import patch as mock_patch

    from ddtrace.constants import SPAN_KIND
    from ddtrace.ext import SpanKind
    from ddtrace.ext import net
    from tests.contrib.claude_agent_sdk.utils import MOCK_MODEL

    # Mock the transport write method
    mock_write = AsyncMock(return_value=None)

    # Create a mock query object with receive_messages method
    # The SDK expects raw JSON data in the format the message parser expects
    async def mock_receive_messages():
        yield {
            "type": "system",
            "subtype": "init",
            "cwd": "/test/path",
            "session_id": "test-session-id",
            "tools": ["Task", "Bash", "Read"],
            "model": MOCK_MODEL,
        }
        yield {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "4"}],
                "model": MOCK_MODEL,
            },
        }
        yield {
            "type": "result",
            "subtype": "success",
            "duration_ms": 100,
            "duration_api_ms": 90,
            "is_error": False,
            "num_turns": 1,
            "session_id": "test-session-id",
        }

    mock_query = MagicMock()
    mock_query.receive_messages = mock_receive_messages

    # Create a mock client that simulates the connected state
    with mock_patch.object(claude_agent_sdk.ClaudeSDKClient, "connect", new_callable=AsyncMock):
        with mock_patch.object(claude_agent_sdk.ClaudeSDKClient, "disconnect", new_callable=AsyncMock):
            client = claude_agent_sdk.ClaudeSDKClient()
            # Simulate connected state with proper mock query object
            client._query = mock_query
            client._transport = MagicMock()
            client._transport.write = mock_write

            # Call query method - starts span
            await client.query(prompt="Hello from client!")

            # Call receive_messages - finishes span
            messages = []
            async for message in client.receive_messages():
                messages.append(message)

    # Verify exactly 1 span was created with peer service tags
    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected exactly 1 trace, got {len(spans)}"
    assert len(spans[0]) == 1, f"Expected exactly 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "claude_agent_sdk.request", f"Expected span name 'claude_agent_sdk.request', got '{span.name}'"
    # Verify span.service semantic tag is set (required by APM standards)
    assert span.service is not None, "Expected span.service tag to be set"
    # Verify span.kind is CLIENT (required for peer service)
    assert span.get_tag(SPAN_KIND) == SpanKind.CLIENT, (
        f"Expected span.kind '{SpanKind.CLIENT}', got '{span.get_tag(SPAN_KIND)}'"
    )
    # Verify out.host is set (peer service precursor tag)
    assert span.get_tag(net.TARGET_HOST) is not None, "Expected out.host tag to be set for peer service"
    assert span.get_tag(net.TARGET_HOST) == "api.anthropic.com", (
        f"Expected out.host to be 'api.anthropic.com', got '{span.get_tag(net.TARGET_HOST)}'"
    )
