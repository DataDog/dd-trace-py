import asyncio
import json

import pytest

from ddtrace.contrib.internal.mcp.patch import patch
from ddtrace.contrib.internal.mcp.patch import unpatch
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_input_value
from ddtrace.llmobs._utils import get_llmobs_session_id
from ddtrace.llmobs._utils import get_llmobs_span_kind
from ddtrace.llmobs._utils import get_llmobs_span_name
from ddtrace.llmobs._utils import get_llmobs_tags
from tests.utils import override_config


# "legacy" uses the initialize handshake, "auto" negotiates the stateless 2026-07-28 protocol
CLIENT_MODES = ["legacy", "auto"]


@pytest.fixture
def mcp_server():
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(name="TestServer")

    @server.tool(description="A simple calculator tool")
    def calculator(operation: str, a: int, b: int) -> dict:
        return {"result": a + b}

    @server.tool(description="A tool that always fails")
    def failing_tool(param: str) -> str:
        raise ValueError("Tool execution failed")

    return server


@pytest.fixture
def run_client(mcp_server):
    from mcp import Client
    from mcp.types import Implementation

    def _run(mode, fn):
        async def run():
            client_info = Implementation(name="test-client", title="Test Client", version="1.2.3")
            async with Client(mcp_server, mode=mode, client_info=client_info) as client:
                return await fn(client)

        return asyncio.run(run())

    return _run


def _pop_spans(test_spans):
    return [span for trace in test_spans.pop_traces() for span in trace]


def _only(spans, resource):
    matches = [span for span in spans if span.resource == resource]
    assert len(matches) == 1, [span.resource for span in spans]
    return matches[0]


@pytest.mark.parametrize("mode", CLIENT_MODES)
def test_tool_call(mcp_llmobs, test_spans, run_client, mode):
    run_client(mode, lambda client: client.call_tool("calculator", {"operation": "add", "a": 1, "b": 2}))

    spans = _pop_spans(test_spans)
    client_span = _only(spans, "client_tool_call")
    server_span = _only(spans, "server_tool_call")

    assert server_span.name == "mcp.tools/call"
    assert server_span.trace_id == client_span.trace_id
    assert server_span.parent_id == client_span.span_id
    assert _get_llmobs_data_metastruct(server_span)["parent_id"] == str(client_span.span_id)

    assert get_llmobs_span_kind(server_span) == "tool"
    assert get_llmobs_span_name(server_span) == "calculator"
    server_tags = get_llmobs_tags(server_span)
    assert server_tags["mcp_method"] == "tools/call"
    assert server_tags["mcp_tool"] == "calculator"
    assert server_tags["mcp_tool_kind"] == "server"
    assert server_tags["mcp_client_name"] == "test-client"
    assert server_tags["mcp_client_title"] == "Test Client"
    assert server_tags["mcp_client_version"] == "1.2.3"

    server_input = json.loads(get_llmobs_input_value(server_span))
    assert server_input["method"] == "tools/call"
    assert server_input["params"]["arguments"] == {"operation": "add", "a": 1, "b": 2}
    assert "_dd_trace_context" not in server_input["params"].get("_meta", {})
    assert not [key for key in server_input["params"].get("_meta", {}) if key.startswith("io.modelcontextprotocol/")]

    assert get_llmobs_span_kind(client_span) == "tool"
    assert get_llmobs_span_name(client_span) == "MCP Client Tool Call: calculator"
    assert get_llmobs_tags(client_span)["mcp_server_name"] == "TestServer"

    session_span = _only(spans, "ClientSession")
    assert get_llmobs_tags(session_span)["mcp_server_name"] == "TestServer"

    server_initialize_spans = [span for span in spans if span.name == "mcp.initialize"]
    if mode == "legacy":
        assert len(server_initialize_spans) == 1
        initialize_tags = get_llmobs_tags(server_initialize_spans[0])
        assert initialize_tags["mcp_client_name"] == "test-client"
        assert initialize_tags["client_version"] == "test-client_1.2.3"
        assert _only(spans, "ClientSession.initialize")
    else:
        assert server_initialize_spans == []
        assert not [span for span in spans if span.resource == "ClientSession.initialize"]


@pytest.mark.parametrize("mode", CLIENT_MODES)
def test_tool_error(mcp_llmobs, test_spans, run_client, mode):
    run_client(mode, lambda client: client.call_tool("failing_tool", {"param": "value"}))

    spans = _pop_spans(test_spans)
    client_span = _only(spans, "client_tool_call")
    server_span = _only(spans, "server_tool_call")

    assert client_span.error
    assert server_span.error
    assert server_span.get_tag("error.type") == "ToolError"
    # mcp 2 reports a generic message rather than the raised exception's text
    assert server_span.get_tag("error.message").startswith("Error executing tool failing_tool")
    assert client_span.get_tag("error.message").startswith("Error executing tool failing_tool")


@pytest.mark.parametrize("mode", CLIENT_MODES)
def test_tool_error_without_llmobs(mcp_setup, test_spans, run_client, mode):
    assert not LLMObs.enabled
    result = run_client(mode, lambda client: client.call_tool("failing_tool", {"param": "value"}))

    assert result.is_error
    server_span = _only(_pop_spans(test_spans), "server_tool_call")
    assert server_span.error == 1
    assert server_span.get_tag("error.type") == "ToolError"
    assert server_span.get_tag("error.message").startswith("Error executing tool failing_tool")


@pytest.mark.parametrize("mode", CLIENT_MODES)
def test_intent_capture(mcp_llmobs, test_spans, run_client, mode):
    async def list_then_call(client):
        tools = await client.list_tools()
        await client.call_tool(
            "calculator",
            {"operation": "add", "a": 1, "b": 2, "telemetry": {"intent": "Testing intent capture"}},
        )
        return tools

    with override_config("mcp", dict(capture_intent=True)):
        tools = run_client(mode, list_then_call)

    schema = next(tool for tool in tools.tools if tool.name == "calculator").input_schema
    assert schema["properties"]["telemetry"]["properties"]["intent"]["type"] == "string"
    assert "intent" in schema["required"]

    server_span = _only(_pop_spans(test_spans), "server_tool_call")
    assert _get_llmobs_data_metastruct(server_span)["meta"]["intent"] == "Testing intent capture"
    assert "telemetry" not in json.loads(get_llmobs_input_value(server_span))["params"]["arguments"]


def test_intent_capture_disabled_by_default(mcp_llmobs, run_client):
    tools = run_client("auto", lambda client: client.list_tools())

    schema = next(tool for tool in tools.tools if tool.name == "calculator").input_schema
    assert "telemetry" not in schema["properties"]


def _run_over_streams(server):
    """Client(server) runs the server inside the client's contextvars, which parents server spans even
    without _meta propagation. Separate streams and a server task started up front isolate the two sides.
    """
    import anyio
    from mcp.client.session import ClientSession
    from mcp.shared.message import SessionMessage

    async def run():
        client_write, server_read = anyio.create_memory_object_stream[SessionMessage | Exception](10)
        server_write, client_read = anyio.create_memory_object_stream[SessionMessage | Exception](10)
        lowlevel_server = server._lowlevel_server
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                lowlevel_server.run, server_read, server_write, lowlevel_server.create_initialization_options()
            )
            async with ClientSession(client_read, client_write) as session:
                await session.initialize()
                await session.call_tool("calculator", {"operation": "add", "a": 1, "b": 2})
            tg.cancel_scope.cancel()

    asyncio.run(run())


def test_distributed_tracing_over_streams(mcp_llmobs, test_spans, mcp_server):
    _run_over_streams(mcp_server)

    spans = _pop_spans(test_spans)
    client_span = _only(spans, "client_tool_call")
    server_span = _only(spans, "server_tool_call")
    assert server_span.trace_id == client_span.trace_id
    assert server_span.parent_id == client_span.span_id


def test_distributed_tracing_disabled(mcp_llmobs, test_spans, mcp_server):
    with override_config("mcp", dict(distributed_tracing=False)):
        _run_over_streams(mcp_server)

    spans = _pop_spans(test_spans)
    client_span = _only(spans, "client_tool_call")
    server_span = _only(spans, "server_tool_call")
    assert server_span.trace_id != client_span.trace_id
    assert server_span.parent_id is None


def test_server_constructed_before_patch(mcp_llmobs, test_spans):
    unpatch()
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(name="TestServer")

    @server.tool(description="A simple calculator tool")
    def calculator(operation: str, a: int, b: int) -> dict:
        return {"result": a + b}

    patch()
    _run_over_streams(server)
    _run_over_streams(server)

    spans = _pop_spans(test_spans)
    assert len([span for span in spans if span.resource == "server_tool_call"]) == 2


@pytest.mark.parametrize("mode", CLIENT_MODES)
def test_conversation_id_capture(mcp_llmobs, test_spans, run_client, mode):
    async def run(client):
        tools = await client.list_tools()
        first = await client.call_tool("calculator", {"operation": "add", "a": 1, "b": 2})
        conversation_id = first.content[-1].text.split('"')[1]
        second = await client.call_tool(
            "calculator",
            {
                "operation": "add",
                "a": 1,
                "b": 2,
                "telemetry": {"intent": "Testing", "conversation_id": conversation_id},
            },
        )
        return tools, first, second, conversation_id

    with override_config("mcp", dict(capture_intent=True, capture_session=True)):
        tools, first, second, conversation_id = run_client(mode, run)

    telemetry_schema = next(t for t in tools.tools if t.name == "calculator").input_schema["properties"]["telemetry"]
    assert set(telemetry_schema["properties"]) == {"intent", "conversation_id"}
    assert telemetry_schema["required"] == ["intent"]

    assert len(conversation_id) == 32
    assert len(first.content) == 2
    assert len(second.content) == 1

    first_span, second_span = [span for span in _pop_spans(test_spans) if span.resource == "server_tool_call"]
    assert get_llmobs_session_id(first_span) == conversation_id
    assert get_llmobs_session_id(second_span) == conversation_id
    assert _get_llmobs_data_metastruct(second_span)["meta"]["intent"] == "Testing"
    assert "telemetry" not in json.loads(get_llmobs_input_value(second_span))["params"]["arguments"]


def test_conversation_id_capture_disabled_by_default(mcp_llmobs, test_spans, run_client):
    result = run_client("auto", lambda client: client.call_tool("calculator", {"operation": "add", "a": 1, "b": 2}))

    assert len(result.content) == 1
    assert get_llmobs_session_id(_only(_pop_spans(test_spans), "server_tool_call")) is None


def test_conversation_id_capture_without_arguments(mcp_llmobs, test_spans, run_client):
    with override_config("mcp", dict(capture_session=True)):
        result = run_client("auto", lambda client: client.call_tool("calculator"))

    conversation_id = get_llmobs_session_id(_only(_pop_spans(test_spans), "server_tool_call"))
    assert conversation_id
    assert conversation_id in result.content[-1].text


@pytest.mark.parametrize("transport_session_id", [None, "transport-session"])
def test_conversation_id_capture_skipped_with_transport_session(mcp_llmobs, transport_session_id):
    from ddtrace.llmobs._contrib._subscribers import get_integration

    integration = get_integration("mcp")
    request = {"params": {"arguments": {}}}
    with override_config("mcp", dict(capture_session=True)), LLMObs.tool(name="calculator") as span:
        integration.process_telemetry_argument(span, request, transport_session_id)
        session_id = get_llmobs_session_id(span)

    if transport_session_id:
        assert session_id is None
    else:
        assert len(session_id) == 32


def test_conversation_id_reused_for_parallel_first_calls(mcp_llmobs):
    from ddtrace.llmobs._contrib._subscribers import get_integration

    class Connection:
        pass

    integration = get_integration("mcp")
    first, other = Connection(), Connection()
    session_ids = []
    with override_config("mcp", dict(capture_session=True)):
        for connection in (first, first, other):
            with LLMObs.tool(name="calculator") as span:
                integration.process_telemetry_argument(span, {"params": {"arguments": {}}}, None, connection)
                session_ids.append(get_llmobs_session_id(span))

    assert session_ids[0] == session_ids[1]
    assert session_ids[2] != session_ids[0]


def test_conversation_id_capture_strips_empty_telemetry(mcp_llmobs, test_spans, run_client):
    arguments = {"operation": "add", "a": 1, "b": 2, "telemetry": {}}
    with override_config("mcp", dict(capture_session=True)):
        result = run_client("auto", lambda client: client.call_tool("calculator", arguments))

    assert not result.is_error
    server_span = _only(_pop_spans(test_spans), "server_tool_call")
    assert "telemetry" not in json.loads(get_llmobs_input_value(server_span))["params"]["arguments"]
    assert get_llmobs_session_id(server_span)


def test_pending_conversation_id_keeps_creation_time(mcp_llmobs):
    from ddtrace.llmobs._contrib._subscribers import get_integration

    class Connection:
        pass

    integration = get_integration("mcp")
    connection = Connection()
    with override_config("mcp", dict(capture_session=True)):
        first = integration._new_conversation_id(connection)
        conversation_id, created = integration._pending_conversation_ids[connection]
        integration._pending_conversation_ids[connection] = (conversation_id, created - 1.5)
        assert integration._new_conversation_id(connection) == first
        assert integration._pending_conversation_ids[connection][1] == created - 1.5
        integration._pending_conversation_ids[connection] = (conversation_id, created - 2.5)
        assert integration._new_conversation_id(connection) != first


def test_conversation_id_shared_by_parallel_first_calls(mcp_llmobs, test_spans, run_client):
    """Stateless requests have no connection to share an ID on, so only the legacy stream reuses it."""

    async def parallel_calls(client):
        args = {"operation": "add", "a": 1, "b": 2}
        return await asyncio.gather(client.call_tool("calculator", args), client.call_tool("calculator", args))

    with override_config("mcp", dict(capture_session=True)):
        run_client("legacy", parallel_calls)

    spans = [span for span in _pop_spans(test_spans) if span.resource == "server_tool_call"]
    assert len(spans) == 2
    assert get_llmobs_session_id(spans[0]) == get_llmobs_session_id(spans[1])


def test_conversation_id_source_and_invented_ids(mcp_llmobs, test_spans, run_client):
    async def run(client):
        first = await client.call_tool("calculator", {"operation": "add", "a": 1, "b": 2})
        conversation_id = first.content[-1].text.split('"')[1]
        await client.call_tool(
            "calculator", {"operation": "add", "a": 1, "b": 2, "telemetry": {"conversation_id": conversation_id}}
        )
        invented = await client.call_tool(
            "calculator", {"operation": "add", "a": 1, "b": 2, "telemetry": {"conversation_id": "made-up"}}
        )
        return conversation_id, invented

    with override_config("mcp", dict(capture_session=True)):
        conversation_id, invented = run_client("legacy", run)

    first_span, echoed_span, invented_span = [
        span for span in _pop_spans(test_spans) if span.resource == "server_tool_call"
    ]
    assert get_llmobs_tags(first_span)["mcp_session_source"] == "generated"
    assert get_llmobs_tags(echoed_span)["mcp_session_source"] == "echoed"
    assert get_llmobs_session_id(echoed_span) == conversation_id
    assert get_llmobs_tags(invented_span)["mcp_session_source"] == "generated"
    assert get_llmobs_session_id(invented_span) not in ("made-up", None)
    assert get_llmobs_session_id(invented_span) in invented.content[-1].text


@pytest.mark.parametrize("list_first", [True, False])
def test_tool_with_own_telemetry_argument_is_unchanged(mcp_llmobs, test_spans, list_first):
    from mcp import Client
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(name="TelemetryServer")

    @server.tool(description="Records an event")
    def record_event(telemetry: dict) -> dict:
        return telemetry

    async def run():
        async with Client(server, mode="legacy") as client:
            tools = await client.list_tools() if list_first else None
            result = await client.call_tool("record_event", {"telemetry": {"event": "click"}})
            return tools, result

    with override_config("mcp", dict(capture_session=True)):
        tools, result = asyncio.run(run())

    if tools:
        schema = next(t for t in tools.tools if t.name == "record_event").input_schema
        assert "conversation_id" not in json.dumps(schema)
    assert not result.is_error
    assert json.loads(result.content[0].text) == {"event": "click"}
    assert len(result.content) == 1
    server_span = _only(_pop_spans(test_spans), "server_tool_call")
    assert get_llmobs_session_id(server_span) is None


def test_own_telemetry_argument_kept_with_capture_disabled(mcp_llmobs, test_spans):
    from mcp import Client
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(name="TelemetryServer")

    @server.tool(description="Records an event")
    def record_event_disabled(telemetry: dict) -> dict:
        return telemetry

    async def run():
        async with Client(server, mode="legacy") as client:
            return await client.call_tool("record_event_disabled", {"telemetry": {"event": "click"}})

    result = asyncio.run(run())

    assert not result.is_error
    assert json.loads(result.content[0].text) == {"event": "click"}


def test_conversation_id_not_added_to_error_without_text(mcp_llmobs, test_spans):
    from mcp import Client
    from mcp.server.mcpserver import MCPServer
    from mcp.types import CallToolResult

    server = MCPServer(name="EmptyErrorServer")

    @server.tool(description="Fails without a message")
    def fail_quietly() -> CallToolResult:
        return CallToolResult(content=[], is_error=True)

    async def run():
        async with Client(server, mode="legacy") as client:
            return await client.call_tool("fail_quietly", {})

    with override_config("mcp", dict(capture_session=True)):
        result = asyncio.run(run())

    assert result.is_error
    assert result.content == []
    assert _only(_pop_spans(test_spans), "server_tool_call").get_tag("error.message") == "tool resulted in an error"
