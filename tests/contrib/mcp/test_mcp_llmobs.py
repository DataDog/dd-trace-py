import asyncio

import mock

from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


def _assert_distributed_trace(mock_tracer, llmobs_events, expected_tool_name):
    """Assert that client and server spans have the same trace ID (distributed tracing)."""
    traces = mock_tracer.pop_traces()
    assert len(traces) >= 1

    all_spans = [span for trace in traces for span in trace]
    client_spans = [span for span in all_spans if span.resource == "call_tool"]
    server_spans = [span for span in all_spans if span.resource == "execute_tool"]
    client_events = [event for event in llmobs_events if event["meta"]["metadata"].get("mcp.operation") == "call_tool"]
    server_events = [
        event for event in llmobs_events if event["meta"]["metadata"].get("mcp.operation") == "execute_tool"
    ]

    assert len(client_spans) >= 1 and len(server_spans) >= 1
    assert len(client_events) >= 1 and len(server_events) >= 1
    assert client_spans[0].trace_id == server_spans[0].trace_id
    assert client_events[0]["trace_id"] == server_events[0]["trace_id"]

    return all_spans, client_events, server_events, client_spans, server_spans


def test_llmobs_mcp_client_calls_server(mcp_setup, mock_tracer, llmobs_events, mcp_call_tool):
    """Test that LLMObs records are emitted for both client and server MCP operations."""
    asyncio.run(mcp_call_tool("calculator", {"operation": "add", "a": 20, "b": 22}))

    all_spans, client_events, server_events, client_spans, server_spans = _assert_distributed_trace(
        mock_tracer, llmobs_events, "calculator"
    )

    assert len(all_spans) == 2
    client_span = client_spans[0]
    server_span = server_spans[0]

    assert client_events[0] == _expected_llmobs_non_llm_span_event(
        client_span,
        span_kind="tool",
        input_value='{"operation": "add", "a": 20, "b": 22}',
        output_value=mock.ANY,
        metadata={"mcp.operation": "call_tool"},
        tags={"service": "mcptest", "ml_app": "<ml-app-name>"},
    )

    assert server_events[0] == _expected_llmobs_non_llm_span_event(
        server_span,
        span_kind="tool",
        input_value='{"operation": "add", "a": 20, "b": 22}',
        output_value=mock.ANY,
        metadata={"mcp.operation": "execute_tool"},
        tags={"service": "mcptest", "ml_app": "<ml-app-name>"},
    )


def test_llmobs_client_tool_call_error(mcp_setup, mock_tracer, llmobs_events, mcp_call_tool):
    """Test that client tool calls get error response but client span is not marked as error."""
    asyncio.run(mcp_call_tool("failing_tool", {"param": "value"}))

    all_spans, client_events, server_events, client_spans, server_spans = _assert_distributed_trace(
        mock_tracer, llmobs_events, "failing_tool"
    )

    assert len(all_spans) == 2
    client_span = client_spans[0]
    server_span = server_spans[0]
    assert not client_span.error
    assert server_span.error

    assert client_events[0] == _expected_llmobs_non_llm_span_event(
        client_span,
        span_kind="tool",
        input_value='{"param": "value"}',
        output_value=mock.ANY,
        metadata={"mcp.operation": "call_tool"},
        tags={"service": "mcptest", "ml_app": "<ml-app-name>"},
    )


def test_llmobs_server_tool_execute_error(mcp_setup, mock_tracer, llmobs_events, mcp_call_tool):
    """Test error handling in server tool execution."""
    asyncio.run(mcp_call_tool("failing_tool", {"param": "value"}))

    all_spans, client_events, server_events, client_spans, server_spans = _assert_distributed_trace(
        mock_tracer, llmobs_events, "failing_tool"
    )

    assert len(all_spans) == 2
    client_span = client_spans[0]
    server_span = server_spans[0]
    assert not client_span.error
    assert server_span.error

    server_error_events = [
        event
        for event in llmobs_events
        if event.get("status") == "error" and event["meta"]["metadata"].get("mcp.operation") == "execute_tool"
    ]
    assert len(server_error_events) == 1
    server_error_event = server_error_events[0]

    assert server_error_event == _expected_llmobs_non_llm_span_event(
        server_span,
        span_kind="tool",
        input_value='{"param": "value"}',
        metadata={"mcp.operation": "execute_tool"},
        tags={"service": "mcptest", "ml_app": "<ml-app-name>"},
        error="mcp.server.fastmcp.exceptions.ToolError",
        error_message="Error executing tool failing_tool: Tool execution failed",
        error_stack=mock.ANY,
    )
