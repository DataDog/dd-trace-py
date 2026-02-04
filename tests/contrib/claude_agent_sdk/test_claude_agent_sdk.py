import pytest


@pytest.mark.asyncio
async def test_query(claude_agent_sdk, mock_internal_client, test_spans):
    messages = []
    async for message in claude_agent_sdk.query(prompt="Hello, world!"):
        messages.append(message)
    
    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected exactly 1 trace, got {len(spans)}"
    assert len(spans[0]) == 1, f"Expected exactly 1 span, got {len(spans[0])}"
    
    span = spans[0][0]
    assert span.name == "claude_agent_sdk.query", f"Expected span name 'claude_agent_sdk.query', got '{span.name}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"

    assert span.service is not None, "Expected service tag to be set"


@pytest.mark.asyncio
async def test_query_error(claude_agent_sdk, mock_internal_client_error, test_spans):
    with pytest.raises(ValueError) as exc_info:
        async for _ in claude_agent_sdk.query(prompt="This will fail"):
            pass

    assert "Connection failed" in str(exc_info.value)

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected exactly 1 trace, got {len(spans)}"
    assert len(spans[0]) == 1, f"Expected exactly 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "claude_agent_sdk.query", f"Expected span name 'claude_agent_sdk.query', got '{span.name}'"
    assert span.error == 1, f"Expected error=1, got error={span.error}"
    assert span.get_tag("error.type") == "builtins.ValueError", "Expected error.type tag to be set"
    assert span.get_tag("error.message") == "Connection failed", "Expected error.message tag to be set"


@pytest.mark.asyncio
async def test_client_query(claude_agent_sdk, mock_client, test_spans):
    await mock_client.query(prompt="Hello, world!")
    async for _ in mock_client.receive_messages():
        pass

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected exactly 1 trace, got {len(spans)}"
    assert len(spans[0]) == 1, f"Expected exactly 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "claude_agent_sdk.request", f"Expected span name 'claude_agent_sdk.request', got '{span.name}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"

    assert span.service is not None, "Expected service tag to be set"


@pytest.mark.asyncio
async def test_client_query_error(claude_agent_sdk, mock_client_error, test_spans):
    with pytest.raises(ValueError) as exc_info:
        await mock_client_error.query(prompt="This will fail")

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected exactly 1 trace, got {len(spans)}"
    assert len(spans[0]) == 1, f"Expected exactly 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "claude_agent_sdk.request", f"Expected span name 'claude_agent_sdk.request', got '{span.name}'"
    assert span.error == 1, f"Expected error=1, got error={span.error}"
    assert span.get_tag("error.type") == "builtins.ValueError", "Expected error.type tag to be set"
    assert span.get_tag("error.message") == "Mocked transport error for testing", "Expected error.message tag to be set"
