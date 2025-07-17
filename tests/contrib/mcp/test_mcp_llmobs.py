import asyncio
import json

import mock

from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


def _get_client_and_server_spans_and_events(mock_tracer, llmobs_events):
    """Get client and server spans and events for testing."""
    traces = mock_tracer.pop_traces()
    assert len(traces) >= 1

    all_spans = [span for trace in traces for span in trace]
    client_spans = [span for span in all_spans if span.resource == "client_tool_call"]
    server_spans = [span for span in all_spans if span.resource == "server_tool_call"]
    client_events = [event for event in llmobs_events if "MCP Client Tool Call" in event["name"]]
    server_events = [event for event in llmobs_events if "MCP Server Tool Execute" in event["name"]]

    assert len(client_spans) >= 1 and len(server_spans) >= 1
    assert len(client_events) >= 1 and len(server_events) >= 1

    return all_spans, client_events, server_events, client_spans, server_spans


def test_llmobs_mcp_client_calls_server(mcp_setup, mock_tracer, llmobs_events, mcp_call_tool):
    """Test that LLMObs records are emitted for both client and server MCP operations."""
    asyncio.run(mcp_call_tool("calculator", {"operation": "add", "a": 20, "b": 22}))

    all_spans, client_events, server_events, client_spans, server_spans = _get_client_and_server_spans_and_events(
        mock_tracer, llmobs_events
    )

    assert len(all_spans) == 2
    client_span = client_spans[0]
    server_span = server_spans[0]

    assert client_events[0]["name"] == "MCP Client Tool Call: calculator"
    assert server_events[0]["name"] == "MCP Server Tool Execute: calculator"

    assert client_events[0] == _expected_llmobs_non_llm_span_event(
        client_span,
        span_kind="tool",
        input_value=json.dumps({"operation": "add", "a": 20, "b": 22}),
        output_value=json.dumps(
            {
                "content": [{"type": "text", "annotations": {}, "meta": {}, "text": '{\n  "result": 42\n}'}],
                "isError": False,
            }
        ),
        tags={"service": "mcptest", "ml_app": "<ml-app-name>"},
    )
    assert server_events[0] == _expected_llmobs_non_llm_span_event(
        server_span,
        span_kind="tool",
        input_value=json.dumps({"operation": "add", "a": 20, "b": 22}),
        output_value=json.dumps([{"type": "text", "annotations": {}, "meta": {}, "text": '{\n  "result": 42\n}'}]),
        tags={"service": "mcptest", "ml_app": "<ml-app-name>"},
    )


def test_llmobs_client_server_tool_error(mcp_setup, mock_tracer, llmobs_events, mcp_call_tool):
    """Test error handling in both client and server MCP operations."""
    asyncio.run(mcp_call_tool("failing_tool", {"param": "value"}))

    all_spans, client_events, server_events, client_spans, server_spans = _get_client_and_server_spans_and_events(
        mock_tracer, llmobs_events
    )

    assert len(all_spans) == 2
    client_span = client_spans[0]
    server_span = server_spans[0]

    assert client_events[0]["name"] == "MCP Client Tool Call: failing_tool"
    assert server_events[0]["name"] == "MCP Server Tool Execute: failing_tool"

    assert not client_span.error
    assert server_span.error

    assert client_events[0] == _expected_llmobs_non_llm_span_event(
        client_span,
        span_kind="tool",
        input_value=json.dumps({"param": "value"}),
        output_value=json.dumps(
            {
                "content": [
                    {
                        "type": "text",
                        "annotations": {},
                        "meta": {},
                        "text": "Error executing tool failing_tool: Tool execution failed",
                    }
                ],
                "isError": True,
            }
        ),
        tags={"service": "mcptest", "ml_app": "<ml-app-name>"},
    )
    assert server_events[0] == _expected_llmobs_non_llm_span_event(
        server_span,
        span_kind="tool",
        input_value=json.dumps({"param": "value"}),
        tags={"service": "mcptest", "ml_app": "<ml-app-name>"},
        error="mcp.server.fastmcp.exceptions.ToolError",
        error_message="Error executing tool failing_tool: Tool execution failed",
        error_stack=mock.ANY,
    )
