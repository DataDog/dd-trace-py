import asyncio
import json
import os
from textwrap import dedent

import mock

from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


def _assert_distributed_trace(mock_tracer, llmobs_events, expected_tool_name):
    """Assert that client and server spans have the same trace ID and return client/server spans and LLM Obs events."""
    traces = mock_tracer.pop_traces()
    assert len(traces) >= 1

    all_spans = [span for trace in traces for span in trace]
    client_spans = [span for span in all_spans if span.resource == "client_tool_call"]
    server_spans = [span for span in all_spans if span.resource == "server_tool_call"]
    client_events = [event for event in llmobs_events if "MCP Client Tool Call" in event["name"]]
    server_events = [event for event in llmobs_events if "MCP Server Tool Execute" in event["name"]]

    assert len(client_spans) >= 1 and len(server_spans) >= 1
    assert len(client_events) >= 1 and len(server_events) >= 1
    assert client_spans[0].trace_id == server_spans[0].trace_id
    assert client_events[0]["trace_id"] == server_events[0]["trace_id"]
    assert client_events[0]["_dd"]["apm_trace_id"] == server_events[0]["_dd"]["apm_trace_id"]

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

    all_spans, client_events, server_events, client_spans, server_spans = _assert_distributed_trace(
        mock_tracer, llmobs_events, "failing_tool"
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


def test_mcp_distributed_tracing_disabled_env(ddtrace_run_python_code_in_subprocess, llmobs_backend):
    """Test that distributed tracing is disabled when DD_MCP_DISTRIBUTED_TRACING=false."""
    env = os.environ.copy()
    env["DD_LLMOBS_ML_APP"] = "test-ml-app"
    env["DD_API_KEY"] = "test-api-key"
    env["DD_LLMOBS_ENABLED"] = "1"
    env["DD_LLMOBS_AGENTLESS_ENABLED"] = "0"
    env["DD_TRACE_AGENT_URL"] = llmobs_backend.url()
    env["DD_MCP_DISTRIBUTED_TRACING"] = "false"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        dedent(
            """
        import asyncio
        import logging
        import warnings

        logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)
        warnings.filterwarnings("ignore", message="OpenTelemetry configuration.*not supported by Datadog")

        from ddtrace.llmobs import LLMObs
        LLMObs.enable()

        from mcp.server.fastmcp import FastMCP
        from mcp.shared.memory import create_connected_server_and_client_session

        mcp = FastMCP(name="TestServer")

        @mcp.tool(description="Get weather for a location")
        def get_weather(location: str) -> str:
            return f"Weather in {location} is 72°F"

        async def test():
            async with create_connected_server_and_client_session(mcp._mcp_server) as client:
                await client.initialize()
                await client.call_tool("get_weather", {"location": "San Francisco"})

        asyncio.run(test())
        """
        ),
        env=env,
    )
    assert out == b""
    assert status == 0, err
    events = llmobs_backend.wait_for_num_events(num=1)
    traces = events[0]
    assert len(traces) == 2

    client_trace = next((t for t in traces if "Client Tool Call" in t["spans"][0]["name"]), None)
    server_trace = next((t for t in traces if "Server Tool Execute" in t["spans"][0]["name"]), None)

    assert client_trace is not None
    assert server_trace is not None
    assert client_trace["spans"][0]["trace_id"] != server_trace["spans"][0]["trace_id"]
    assert client_trace["spans"][0]["_dd"]["apm_trace_id"] != server_trace["spans"][0]["_dd"]["apm_trace_id"]
