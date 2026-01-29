import asyncio
import importlib.metadata
from importlib.metadata import version
import json
import os
from textwrap import dedent

import mock

from ddtrace.internal.utils.version import parse_version
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event
from tests.utils import override_config


MCP_VERSION = parse_version(version("mcp"))


def _assert_distributed_trace(test_spans, llmobs_events, expected_tool_name):
    """Assert that client and server spans have the same trace ID and return client/server spans and LLM Obs events."""
    traces = test_spans.pop_traces()
    assert len(traces) >= 1

    all_spans = [span for trace in traces for span in trace]
    client_spans = [span for span in all_spans if span.resource == "client_tool_call"]
    server_spans = [span for span in all_spans if span.resource == "server_tool_call"]
    client_events = [event for event in llmobs_events if "MCP Client Tool Call" in event["name"]]
    server_events = [
        event
        for event in llmobs_events
        if event["name"] == expected_tool_name and "mcp_tool_kind:server" in event.get("tags", [])
    ]

    assert len(client_spans) >= 1 and len(server_spans) >= 1
    assert len(client_events) >= 1 and len(server_events) >= 1
    assert client_spans[0].trace_id == server_spans[0].trace_id
    assert client_events[0]["trace_id"] == server_events[0]["trace_id"]
    assert client_events[0]["_dd"]["apm_trace_id"] == server_events[0]["_dd"]["apm_trace_id"]

    return all_spans, client_events, server_events, client_spans, server_spans


def test_llmobs_mcp_client_calls_server(mcp_setup, test_spans, llmobs_events, mcp_call_tool):
    """Test that LLMObs records are emitted for both client and server MCP operations."""
    asyncio.run(mcp_call_tool("calculator", {"operation": "add", "a": 20, "b": 22}))

    llmobs_events.sort(key=lambda event: event["start_ns"])
    all_spans, client_events, server_events, client_spans, server_spans = _assert_distributed_trace(
        test_spans, llmobs_events, "calculator"
    )

    # Sort all_spans by start_ns to match llmobs_events order
    all_spans.sort(key=lambda span: span.start_ns)

    # session, client initialize, server initialize, client list tools, client tool call, server tool call
    assert len(all_spans) == 6
    client_span = client_spans[0]
    server_span = server_spans[0]

    assert client_events[0]["name"] == "MCP Client Tool Call: calculator"
    assert server_events[0]["name"] == "calculator"

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
        tags={
            "service": "mcptest",
            "ml_app": "<ml-app-name>",
            "mcp_server_name": "TestServer",
            "mcp_tool_kind": "client",
        },
    )

    expected_params = {
        **({"task": None} if MCP_VERSION >= (1, 26, 0) else {}),
        "meta": {"progressToken": None},
        "name": "calculator",
        "arguments": {"operation": "add", "a": 20, "b": 22},
    }

    assert server_events[0] == _expected_llmobs_non_llm_span_event(
        server_span,
        span_kind="tool",
        input_value=json.dumps(
            {
                "method": "tools/call",
                "params": expected_params,
                "jsonrpc": "2.0",
                "id": 1,
            }
        ),
        output_value=json.dumps(
            {
                "meta": None,
                "content": [{"type": "text", "text": '{\n  "result": 42\n}', "annotations": None, "meta": None}],
                "structuredContent": None,
                "isError": False,
            }
        ),
        tags={
            "service": "mcptest",
            "ml_app": "<ml-app-name>",
            "mcp_method": "tools/call",
            "mcp_tool": "calculator",
            "mcp_tool_kind": "server",
        },
    )

    # asserting the remaining spans
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        all_spans[0],
        span_kind="workflow",
        input_value=mock.ANY,
        tags={
            "service": "mcptest",
            "ml_app": "<ml-app-name>",
            "mcp_server_name": "TestServer",
            "mcp_server_version": importlib.metadata.version("mcp"),
            "mcp_server_title": None,
        },
        metadata=mock.ANY,
    )

    assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
        all_spans[1], span_kind="task", output_value=mock.ANY, tags={"service": "mcptest", "ml_app": "<ml-app-name>"}
    )

    # server initialize
    assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
        all_spans[2],
        span_kind="task",
        input_value=mock.ANY,
        output_value=mock.ANY,
        tags={
            "service": "mcptest",
            "ml_app": "<ml-app-name>",
            "mcp_method": "initialize",
            "client_name": "mcp",
            "client_version": "mcp_0.1.0",
        },
    )

    # tools/list call
    assert llmobs_events[5] == _expected_llmobs_non_llm_span_event(
        all_spans[5],
        span_kind="task",
        input_value=mock.ANY,
        output_value=mock.ANY,
        tags={"service": "mcptest", "ml_app": "<ml-app-name>"},
    )


def test_llmobs_client_server_tool_error(mcp_setup, test_spans, llmobs_events, mcp_call_tool):
    """Test error handling in both client and server MCP operations."""
    asyncio.run(mcp_call_tool("failing_tool", {"param": "value"}))

    all_spans, client_events, server_events, client_spans, server_spans = _assert_distributed_trace(
        test_spans, llmobs_events, "failing_tool"
    )

    # session, client initialize, server initialize, client tool call, server tool call (no list tools)
    assert len(all_spans) == 5
    client_span = client_spans[0]
    server_span = server_spans[0]

    assert client_events[0]["name"] == "MCP Client Tool Call: failing_tool"
    assert server_events[0]["name"] == "failing_tool"

    assert client_span.error
    assert server_span.error

    # assert the error client span manually
    assert client_events[0]["meta"]["input"]["value"] == json.dumps({"param": "value"})
    assert client_events[0]["meta"]["output"]["value"] == json.dumps(
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
    )
    assert client_events[0]["meta"]["error"]["message"] == "Error executing tool failing_tool: Tool execution failed"
    assert client_events[0]["status"] == "error"
    assert "error:1" in client_events[0]["tags"]

    expected_params = {
        **({"task": None} if MCP_VERSION >= (1, 26, 0) else {}),
        "meta": {"progressToken": None},
        "name": "failing_tool",
        "arguments": {"param": "value"},
    }

    assert server_events[0] == _expected_llmobs_non_llm_span_event(
        server_span,
        span_kind="tool",
        input_value=json.dumps(
            {
                "method": "tools/call",
                "params": expected_params,
                "jsonrpc": "2.0",
                "id": 1,
            }
        ),
        output_value=json.dumps(
            {
                "meta": None,
                "content": [
                    {
                        "type": "text",
                        "text": "Error executing tool failing_tool: Tool execution failed",
                        "annotations": None,
                        "meta": None,
                    }
                ],
                "structuredContent": None,
                "isError": True,
            }
        ),
        tags={
            "service": "mcptest",
            "ml_app": "<ml-app-name>",
            "mcp_method": "tools/call",
            "mcp_tool": "failing_tool",
            "mcp_tool_kind": "server",
        },
        error="ToolError",
        error_message="tool resulted in an error",
        error_stack="",
    )


def test_server_initialization_span_created(mcp_setup, test_spans, llmobs_events, mcp_server_initialized):
    """Test that server initialization creates a span and LLMObs event with custom client info."""
    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]
    llmobs_events.sort(key=lambda event: event["start_ns"])

    initialize_spans = [span for span in all_spans if span.name == "mcp.initialize"]
    assert len(initialize_spans) == 1
    initialize_span = initialize_spans[0]

    initialize_events = [event for event in llmobs_events if event["name"] == "mcp.initialize"]
    assert len(initialize_events) == 1, f"Expected 1 LLMObs event, got {len(initialize_events)}"
    initialize_event = initialize_events[0]

    assert initialize_event == _expected_llmobs_non_llm_span_event(
        initialize_span,
        span_kind="task",
        input_value=mock.ANY,
        output_value=mock.ANY,
        tags={
            "service": "mcptest",
            "ml_app": "<ml-app-name>",
            "mcp_method": "initialize",
            "client_name": "test-client",
            "client_version": "test-client_1.2.3",
        },
    )

    input_data = json.loads(initialize_event["meta"]["input"]["value"])
    assert input_data["method"] == "initialize"
    assert input_data["params"]["clientInfo"]["name"] == "test-client"
    assert input_data["params"]["clientInfo"]["version"] == "1.2.3"

    output_data = json.loads(initialize_event["meta"]["output"]["value"])
    assert "protocolVersion" in output_data
    assert "capabilities" in output_data
    assert "serverInfo" in output_data


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
    # session, client initialize, server initialize, client tool call, server tool call, list tools
    assert len(traces) == 6

    client_trace = next((t for t in traces if "Client Tool Call" in t["spans"][0]["name"]), None)
    server_trace = next(
        (
            t
            for t in traces
            if t["spans"][0]["name"] == "get_weather" and "mcp_tool_kind:server" in t["spans"][0]["tags"]
        ),
        None,
    )

    assert client_trace is not None
    assert server_trace is not None
    assert client_trace["spans"][0]["trace_id"] != server_trace["spans"][0]["trace_id"]
    assert client_trace["spans"][0]["_dd"]["apm_trace_id"] != server_trace["spans"][0]["_dd"]["apm_trace_id"]


def test_intent_capture_tool_schema_injection(mcp_setup, llmobs_events, mcp_server):
    """Test that intent capture adds telemetry property to tool input schemas."""
    from mcp.shared.memory import create_connected_server_and_client_session

    async def run_test():
        async with create_connected_server_and_client_session(mcp_server._mcp_server) as client:
            result = await client.list_tools()
            return result

    with override_config("mcp", dict(capture_intent=True)):
        result = asyncio.run(run_test())
    tool = next(t for t in result.tools if t.name == "calculator")
    schema = tool.inputSchema

    # Verify telemetry property is injected
    assert "telemetry" in schema["properties"], f"telemetry not in properties: {schema}"
    telemetry_schema = schema["properties"]["telemetry"]
    assert telemetry_schema["type"] == "object"
    assert "intent" in telemetry_schema["properties"]
    assert telemetry_schema["properties"]["intent"]["type"] == "string"
    assert "required" in telemetry_schema
    assert "intent" in telemetry_schema["required"]

    # Verify original tool arguments are unchanged
    assert "operation" in schema["properties"]
    assert "a" in schema["properties"]
    assert "b" in schema["properties"]
    assert schema["properties"]["operation"]["type"] == "string"
    assert schema["properties"]["a"]["type"] == "integer"
    assert schema["properties"]["b"]["type"] == "integer"

    # Verify intent is required at top level, and original required args still present
    assert "intent" in schema["required"]
    assert "operation" in schema["required"]
    assert "a" in schema["required"]
    assert "b" in schema["required"]


def test_intent_capture_records_intent_on_span_meta(mcp_setup, test_spans, llmobs_events, mcp_server):
    """Test that intent is recorded on the span meta and telemetry argument is excluded from input."""
    from mcp.shared.memory import create_connected_server_and_client_session

    async def run_test():
        async with create_connected_server_and_client_session(mcp_server._mcp_server) as client:
            await client.call_tool(
                "calculator",
                {
                    "operation": "add",
                    "a": 1,
                    "b": 2,
                    "telemetry": {"intent": "Testing intent capture for adding numbers"},
                },
            )

    with override_config("mcp", dict(capture_intent=True)):
        asyncio.run(run_test())

    llmobs_events.sort(key=lambda event: event["start_ns"])
    server_tool_event = next(
        (e for e in llmobs_events if e["name"] == "calculator" and "mcp_tool_kind:server" in e["tags"]),
        None,
    )
    assert server_tool_event is not None

    # Verify intent is recorded in meta
    assert "intent" in server_tool_event["meta"], f"intent not in meta: {server_tool_event['meta']}"
    assert server_tool_event["meta"]["intent"] == "Testing intent capture for adding numbers"

    # Verify telemetry argument is NOT in the input value
    input_value = json.loads(server_tool_event["meta"]["input"]["value"])
    arguments = input_value.get("params", {}).get("arguments", {})
    assert "telemetry" not in arguments, f"telemetry should not be in arguments: {arguments}"
    assert "operation" in arguments
    assert "a" in arguments
    assert "b" in arguments


def test_intent_capture_disabled_by_default(mcp_setup, test_spans, llmobs_events, mcp_server):
    """Test that intent capture is disabled by default and telemetry property is not injected."""
    from mcp.shared.memory import create_connected_server_and_client_session

    async def run_test():
        async with create_connected_server_and_client_session(mcp_server._mcp_server) as client:
            result = await client.list_tools()
            return result

    with override_config("mcp", dict(capture_intent=False)):
        result = asyncio.run(run_test())
    tool = next(t for t in result.tools if t.name == "calculator")
    schema = tool.inputSchema

    # Verify telemetry property is NOT injected when intent capture is disabled
    assert "telemetry" not in schema.get("properties", {}), f"telemetry should not be in properties: {schema}"
