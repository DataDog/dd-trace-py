import asyncio
import importlib.metadata
from importlib.metadata import version
import json
import os
from textwrap import dedent

import mock

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.llmobs._utils import assert_llmobs_span_data
from tests.utils import override_config


MCP_VERSION = parse_version(version("mcp"))


def _assert_distributed_trace(test_spans, expected_tool_name):
    """Assert that client and server spans have the same trace ID; return spans and the
    client/server tool-call subsets identified by resource name.
    """
    traces = test_spans.pop_traces()
    assert len(traces) >= 1

    all_spans = [span for trace in traces for span in trace]
    client_spans = [span for span in all_spans if span.resource == "client_tool_call"]
    server_spans = [span for span in all_spans if span.resource == "server_tool_call"]

    assert len(client_spans) >= 1 and len(server_spans) >= 1
    assert client_spans[0].trace_id == server_spans[0].trace_id

    return all_spans, client_spans, server_spans


def test_llmobs_mcp_client_calls_server(mcp_setup, mcp_llmobs, test_spans, mcp_call_tool):
    """Test that LLMObs records are emitted for both client and server MCP operations."""
    asyncio.run(mcp_call_tool("calculator", {"operation": "add", "a": 20, "b": 22}))

    all_spans, client_spans, server_spans = _assert_distributed_trace(test_spans, "calculator")

    # Sort all_spans by start_ns
    all_spans.sort(key=lambda span: span.start_ns)

    # session, client initialize, server initialize, client list tools, client tool call, server tool call
    assert len(all_spans) == 6
    client_span = client_spans[0]
    server_span = server_spans[0]

    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(client_span),
        span_kind="tool",
        input_value=json.dumps({"operation": "add", "a": 20, "b": 22}, sort_keys=True),
        output_value=json.dumps(
            {
                "content": [{"type": "text", "annotations": {}, "meta": {}, "text": '{\n  "result": 42\n}'}],
                "isError": False,
            },
            sort_keys=True,
        ),
        tags={
            "service": "mcptest",
            "ml_app": "<ml-app-name>",
            "integration": "mcp",
            "mcp_tool_kind": "client",
            "mcp_server_name": "TestServer",
        },
        name="MCP Client Tool Call: calculator",
    )

    expected_params = {
        **({"task": None} if MCP_VERSION >= (1, 26, 0) else {}),
        "meta": {"progressToken": None},
        "name": "calculator",
        "arguments": {"operation": "add", "a": 20, "b": 22},
    }

    # Server tool span is parented to client tool span via apm parent_id
    assert server_span.parent_id == client_span.span_id
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(server_span),
        span_kind="tool",
        input_value=json.dumps(
            {
                "method": "tools/call",
                "params": expected_params,
                "jsonrpc": "2.0",
                "id": 1,
            },
            sort_keys=True,
        ),
        output_value=json.dumps(
            {
                "meta": None,
                "content": [{"type": "text", "text": '{\n  "result": 42\n}', "annotations": None, "meta": None}],
                "structuredContent": None,
                "isError": False,
            },
            sort_keys=True,
        ),
        tags={
            "service": "mcptest",
            "ml_app": "<ml-app-name>",
            "integration": "mcp",
            "mcp_method": "tools/call",
            "mcp_tool": "calculator",
            "mcp_tool_kind": "server",
        },
        name="calculator",
        parent_id=mock.ANY,
    )

    # asserting the remaining spans (sorted by start_ns)
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(all_spans[0]),
        span_kind="workflow",
        input_value=mock.ANY,
        tags={
            "service": "mcptest",
            "ml_app": "<ml-app-name>",
            "integration": "mcp",
            "mcp_server_name": "TestServer",
            "mcp_server_version": importlib.metadata.version("mcp"),
            "mcp_server_title": None,
        },
        name="MCP Client Session",
    )

    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(all_spans[1]),
        span_kind="task",
        output_value=mock.ANY,
        tags={"service": "mcptest", "ml_app": "<ml-app-name>", "integration": "mcp"},
        name="MCP Client Initialize",
    )

    # server initialize
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(all_spans[2]),
        span_kind="task",
        input_value=mock.ANY,
        output_value=mock.ANY,
        tags={
            "service": "mcptest",
            "ml_app": "<ml-app-name>",
            "integration": "mcp",
            "mcp_method": "initialize",
            "client_name": "mcp",
            "client_version": "mcp_0.1.0",
        },
        name="mcp.initialize",
    )

    # tools/list call
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(all_spans[5]),
        span_kind="task",
        input_value=mock.ANY,
        output_value=mock.ANY,
        tags={"service": "mcptest", "ml_app": "<ml-app-name>", "integration": "mcp"},
        name="MCP Client list Tools",
    )


def test_llmobs_client_server_tool_error(mcp_setup, mcp_llmobs, test_spans, mcp_call_tool):
    """Test error handling in both client and server MCP operations."""
    asyncio.run(mcp_call_tool("failing_tool", {"param": "value"}))

    all_spans, client_spans, server_spans = _assert_distributed_trace(test_spans, "failing_tool")

    # session, client initialize, server initialize, client tool call, server tool call (no list tools)
    assert len(all_spans) == 5
    client_span = client_spans[0]
    server_span = server_spans[0]

    assert client_span.error
    assert server_span.error

    # assert the error client span manually via meta_struct
    client_metastruct = _get_llmobs_data_metastruct(client_span)
    client_meta = client_metastruct.get("meta", {})
    assert client_meta.get("input", {}).get("value") == json.dumps({"param": "value"}, sort_keys=True)
    assert client_meta.get("output", {}).get("value") == json.dumps(
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
        },
        sort_keys=True,
    )
    assert client_meta.get("error", {}).get("message") == "Error executing tool failing_tool: Tool execution failed"

    expected_params = {
        **({"task": None} if MCP_VERSION >= (1, 26, 0) else {}),
        "meta": {"progressToken": None},
        "name": "failing_tool",
        "arguments": {"param": "value"},
    }

    assert server_span.parent_id == client_span.span_id
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(server_span),
        span_kind="tool",
        input_value=json.dumps(
            {
                "method": "tools/call",
                "params": expected_params,
                "jsonrpc": "2.0",
                "id": 1,
            },
            sort_keys=True,
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
            },
            sort_keys=True,
        ),
        tags={
            "service": "mcptest",
            "ml_app": "<ml-app-name>",
            "integration": "mcp",
            "mcp_method": "tools/call",
            "mcp_tool": "failing_tool",
            "mcp_tool_kind": "server",
        },
        error={"type": "ToolError", "message": "tool resulted in an error", "stack": ""},
        name="failing_tool",
        parent_id=mock.ANY,
    )


def test_server_initialization_span_created(mcp_setup, mcp_llmobs, test_spans, mcp_server_initialized):
    """Test that server initialization creates a span and LLMObs event with custom client info."""
    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    initialize_spans = [span for span in all_spans if span.name == "mcp.initialize"]
    assert len(initialize_spans) == 1
    initialize_span = initialize_spans[0]

    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(initialize_span),
        span_kind="task",
        input_value=mock.ANY,
        output_value=mock.ANY,
        tags={
            "service": "mcptest",
            "ml_app": "<ml-app-name>",
            "integration": "mcp",
            "mcp_method": "initialize",
            "client_name": "test-client",
            "client_version": "test-client_1.2.3",
        },
    )

    init_metastruct = _get_llmobs_data_metastruct(initialize_span)
    init_meta = init_metastruct.get("meta", {})
    input_data = json.loads(init_meta.get("input", {}).get("value"))
    assert input_data["method"] == "initialize"
    assert input_data["params"]["clientInfo"]["name"] == "test-client"
    assert input_data["params"]["clientInfo"]["version"] == "1.2.3"

    output_data = json.loads(init_meta.get("output", {}).get("value"))
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


def test_intent_capture_tool_schema_injection(mcp_setup, mcp_llmobs, test_spans, mcp_server):
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


def test_intent_capture_records_intent_on_span_meta(mcp_setup, mcp_llmobs, test_spans, mcp_server):
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

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    server_tool_span = next(
        (
            s
            for s in all_spans
            if _get_llmobs_data_metastruct(s).get("name") == "calculator"
            and _get_llmobs_data_metastruct(s).get("tags", {}).get("mcp_tool_kind") == "server"
        ),
        None,
    )
    assert server_tool_span is not None

    server_tool_metastruct = _get_llmobs_data_metastruct(server_tool_span)
    server_tool_meta = server_tool_metastruct.get("meta", {})

    # Verify intent is recorded in meta
    assert "intent" in server_tool_meta, f"intent not in meta: {server_tool_meta}"
    assert server_tool_meta["intent"] == "Testing intent capture for adding numbers"

    # Verify telemetry argument is NOT in the input value
    input_value = json.loads(server_tool_meta.get("input", {}).get("value"))
    arguments = input_value.get("params", {}).get("arguments", {})
    assert "telemetry" not in arguments, f"telemetry should not be in arguments: {arguments}"
    assert "operation" in arguments
    assert "a" in arguments
    assert "b" in arguments


def test_intent_capture_disabled_by_default(mcp_setup, mcp_llmobs, test_spans, mcp_server):
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


def test_llmobs_set_tags_runs_after_respond_not_before(mcp_setup):
    """Regression: llmobs_set_tags must run AFTER await func(), not before.

    Before the fix, llmobs_set_tags ran synchronously before ``await func(*args, **kwargs)``.
    This widened the race window in which the MCP session's ``_receive_loop`` could exit and
    close ``_write_stream``, causing ``anyio.ClosedResourceError`` instead of a clean
    cancellation (see TASK_MCP_CLOSED_RESOURCE_ERROR.md).

    The fix wraps ``await func(...)`` in a try/finally so that llmobs_set_tags is called
    AFTER the response is sent.  This test verifies:
    1. ``func`` (i.e. ``respond``) is called before ``llmobs_set_tags``.
    2. ``llmobs_set_tags`` still runs even when ``func`` raises ``ClosedResourceError``.
    """
    import anyio

    from ddtrace.contrib.internal.mcp.patch import traced_request_responder_respond

    call_order = []

    async def mock_func_raises_closed_resource(*args, **kwargs):
        call_order.append("func")
        raise anyio.ClosedResourceError()

    mock_instance = mock.MagicMock()
    mock_instance._dd_span = mock.MagicMock()

    original_llmobs_set_tags = mcp_setup._datadog_integration.llmobs_set_tags

    def tracking_llmobs_set_tags(*args, **kwargs):
        call_order.append("llmobs_set_tags")

    mcp_setup._datadog_integration.llmobs_set_tags = tracking_llmobs_set_tags

    async def run():
        try:
            await traced_request_responder_respond(
                mock_func_raises_closed_resource, mock_instance, (mock.MagicMock(),), {}
            )
        except anyio.ClosedResourceError:
            pass

    asyncio.run(run())
    mcp_setup._datadog_integration.llmobs_set_tags = original_llmobs_set_tags

    assert call_order == ["func", "llmobs_set_tags"], (
        f"llmobs_set_tags must run after func (in finally block), not before it. Actual call order: {call_order}"
    )
