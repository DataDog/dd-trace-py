"""AI Guard evaluation of the client-managed MCP tools of the Anthropic SDK (anthropic.lib.tools.mcp)."""

import asyncio
import contextlib
from contextlib import asynccontextmanager
from contextlib import contextmanager
import contextvars
import hashlib
import inspect
import json
import os
import sys
import threading
from unittest.mock import AsyncMock
from unittest.mock import patch

import anyio
import httpx
import pytest


pytest.importorskip("mcp")
# Imported before the anthropic patch on purpose: the helpers are wrapped in place, so these names are covered.
mcp_helpers = pytest.importorskip("anthropic.lib.tools.mcp")

from anthropic.lib.tools.mcp import async_mcp_tool  # noqa: E402
from anthropic.lib.tools.mcp import mcp_tool  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp import StdioServerParameters  # noqa: E402
from mcp import types as mcp_types  # noqa: E402
from mcp.client.sse import sse_client  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
import mcp.shared._httpx_utils as mcp_httpx_utils  # noqa: E402

from ddtrace.aiguard import AIGuardAbortError  # noqa: E402
from ddtrace.aiguard import middleware  # noqa: E402
from ddtrace.aiguard.integrations._mcp import mcp_server  # noqa: E402
from ddtrace.aiguard.integrations._mcp import mcp_server_id  # noqa: E402
from ddtrace.aiguard.integrations._mcp import safe_stdio_args  # noqa: E402
from ddtrace.internal import core  # noqa: E402
from ddtrace.internal.span_bus import store_span_on_context  # noqa: E402
from ddtrace.trace import tracer  # noqa: E402
from tests.aiguard.utils import mock_evaluate_response  # noqa: E402
from tests.aiguard.utils import override_ai_guard_config  # noqa: E402


MODEL = "claude-sonnet-4-5"
SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
REMOTE_URL = "https://user:s3cr3t@MCP.Example.com:443/mcp/v1?token=t0k3n#frag"
REMOTE_ORIGIN = "https://mcp.example.com"
REMOTE_ID = mcp_server_id(url=REMOTE_ORIGIN)
ECHO_SERVER = os.path.join(os.path.dirname(__file__), "mcp_echo_server.py")
# mcp>=2 transports use httpx2, mcp<2 httpx.
HTTP = getattr(mcp_httpx_utils, "httpx2", httpx)

EXECUTE_REQUEST = "ddtrace.aiguard._api_client.AIGuardClient._execute_request"


def _mcp_tool_def(name="echo"):
    return mcp_types.Tool.model_validate({"name": name, "description": "Echo the text", "inputSchema": SCHEMA})


def _call_result(text="echoed"):
    return mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text=text)])


def _payloads(mock_execute_request):
    return [call.args[1]["data"]["attributes"] for call in mock_execute_request.call_args_list]


def _tool_call_payloads(mock_execute_request):
    """Evaluations whose target is an assistant tool call: the permission checks of a tool operation."""
    return [p for p in _payloads(mock_execute_request) if p["messages"][-1].get("tool_calls")]


def _remote_server(url_origin=REMOTE_ORIGIN, transport="streamable_http", tools=None):
    return {
        "id": mcp_server_id(url=url_origin),
        "managed_by": "client",
        "transport": transport,
        "url": url_origin,
        "tools": tools if tools is not None else {"echo": "echo"},
    }


@asynccontextmanager
async def _remote_session(url=REMOTE_URL):
    """ClientSession on streams from the real streamable HTTP transport, with call_tool mocked."""
    async with streamable_http_client(url) as streams:
        session = ClientSession(streams[0], streams[1])
        session.call_tool = AsyncMock(return_value=_call_result())
        yield session


def _assert_direct_call(payload, server):
    message = payload["messages"][0]
    assert len(payload["messages"]) == 1
    assert message["role"] == "assistant"
    (tool_call,) = message["tool_calls"]
    assert tool_call["id"].startswith("call_")
    assert tool_call["function"] == {"name": "echo", "arguments": '{"text": "hi"}'}
    assert tool_call["mcp"] == {"server_id": server["id"]}
    assert payload["mcp_servers"] == [server]


# ---------------------------------------------------------------------------
# Server entries and IDs (pure unit)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command,args,expected",
    [
        (
            "npx",
            ["-y", "@modelcontextprotocol/server-filesystem", "/home/me"],
            ["@modelcontextprotocol/server-filesystem"],
        ),
        ("/usr/local/bin/uvx", ["mcp-server-fetch"], ["mcp-server-fetch"]),
        ("npx.cmd", ["@scope/pkg@1.2.3"], ["@scope/pkg@1.2.3"]),
        ("python3.12", ["-m", "my_server.main", "--token", "s3cr3t"], ["my_server.main"]),
        ("npx", ["--package", "pkg", "cmd"], []),
        ("npx", ["./local/server.js"], []),
        ("node", ["server.js", "--api-key", "s3cr3t"], []),
        ("python", ["/path/server.py"], []),
        ("docker", ["run", "-e", "TOKEN=s3cr3t", "image"], []),
    ],
)
def test_safe_stdio_args(command, args, expected):
    assert safe_stdio_args(command, args) == expected


def test_stdio_server_entry():
    server = mcp_server("client", "stdio", command="npx", args=["@modelcontextprotocol/server-github"])
    assert server == {
        "id": mcp_server_id(command="npx", args=["@modelcontextprotocol/server-github"]),
        "managed_by": "client",
        "transport": "stdio",
        "command": "npx",
        "args": ["@modelcontextprotocol/server-github"],
        "tools": {},
    }
    # The transport is not part of the key, and args are joined after the command.
    key = b"stdio|npx|@modelcontextprotocol/server-github"
    assert server["id"] == "mcp_" + hashlib.sha256(key).hexdigest()[:16]
    assert "args" not in mcp_server("client", "stdio", command="node")


def test_remote_server_entry_is_origin_only():
    server = mcp_server("client", "sse", url=REMOTE_URL)
    assert server == _remote_server(transport="sse", tools={})
    assert server["id"] == mcp_server_id(url="https://mcp.example.com/other")


# ---------------------------------------------------------------------------
# Direct calls: the converted tool checks before tools/call is sent
# ---------------------------------------------------------------------------


@patch(EXECUTE_REQUEST)
async def test_direct_async_call_remote_metadata(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        result = await tool.call({"text": "hi"})

    session.call_tool.assert_awaited_once_with(name="echo", arguments={"text": "hi"})
    assert result == [{"type": "text", "text": "echoed"}]
    (payload,) = _payloads(mock_execute_request)
    _assert_direct_call(payload, _remote_server())
    serialized = json.dumps(payload)
    for secret in ("s3cr3t", "t0k3n", "frag", "/mcp/v1", "user"):
        assert secret not in serialized


@pytest.mark.parametrize("decision", ["DENY", "ABORT"])
@patch(EXECUTE_REQUEST)
async def test_direct_call_block_prevents_tools_call(mock_execute_request, anthropic_sdk, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        with pytest.raises(AIGuardAbortError):
            await tool.call({"text": "hi"})

    session.call_tool.assert_not_called()
    mock_execute_request.assert_called_once()


@patch(EXECUTE_REQUEST)
async def test_direct_call_non_blocking_deny_executes(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("DENY", block=False)
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        await tool.call({"text": "hi"})

    session.call_tool.assert_awaited_once()
    mock_execute_request.assert_called_once()


@patch(EXECUTE_REQUEST)
async def test_direct_sync_call(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = mcp_tool(_mcp_tool_def(), session)
        # The sync helper reaches the session through anyio.from_thread, so call it from a worker thread.
        result = await anyio.to_thread.run_sync(tool.call, {"text": "hi"})

        mock_execute_request.return_value = mock_evaluate_response("DENY")
        with pytest.raises(AIGuardAbortError):
            await anyio.to_thread.run_sync(tool.call, {"text": "hi"})

    assert result == [{"type": "text", "text": "echoed"}]
    session.call_tool.assert_awaited_once()
    payloads = _payloads(mock_execute_request)
    assert len(payloads) == 2
    _assert_direct_call(payloads[0], _remote_server())


@pytest.mark.parametrize(
    "convert",
    [
        lambda tool_def, session: async_mcp_tool(tool=tool_def, client=session),
        lambda tool_def, session: async_mcp_tool(tool_def, client=session),
        lambda tool_def, session: async_mcp_tool(client=session, tool=tool_def, strict=True),
    ],
    ids=["keywords", "mixed", "keywords_reordered"],
)
@patch(EXECUTE_REQUEST)
async def test_helper_called_with_keywords(mock_execute_request, anthropic_sdk, convert):
    mock_execute_request.return_value = mock_evaluate_response("DENY")
    async with _remote_session() as session:
        tool = convert(_mcp_tool_def(), session)
        with pytest.raises(AIGuardAbortError):
            await tool.call({"text": "hi"})

    session.call_tool.assert_not_called()
    (payload,) = _payloads(mock_execute_request)
    _assert_direct_call(payload, _remote_server())


@patch(EXECUTE_REQUEST)
async def test_sync_helper_called_with_keywords(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("DENY")
    async with _remote_session() as session:
        tool = mcp_tool(tool=_mcp_tool_def(), client=session)
        with pytest.raises(AIGuardAbortError):
            await anyio.to_thread.run_sync(tool.call, {"text": "hi"})

    session.call_tool.assert_not_called()
    _assert_direct_call(_payloads(mock_execute_request)[0], _remote_server())


@patch(EXECUTE_REQUEST)
async def test_two_sessions_with_the_same_tool_name(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as first, _remote_session("http://localhost:8000/mcp") as second:
        await async_mcp_tool(_mcp_tool_def(), first).call({"text": "hi"})
        await async_mcp_tool(_mcp_tool_def(), second).call({"text": "hi"})

    first_payload, second_payload = _payloads(mock_execute_request)
    _assert_direct_call(first_payload, _remote_server())
    _assert_direct_call(second_payload, _remote_server("http://localhost:8000"))
    assert REMOTE_ID != mcp_server_id(url="http://localhost:8000")


@patch(EXECUTE_REQUEST)
async def test_sse_metadata(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    url = "https://user:s3cr3t@sse.example.com:8443/sse?key=k3y"

    def handler(request):
        return HTTP.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"event: endpoint\ndata: /messages/?session_id=abc\n\n",
        )

    def client_factory(headers=None, timeout=None, auth=None):
        return HTTP.AsyncClient(transport=HTTP.MockTransport(handler), headers=headers, timeout=timeout, auth=auth)

    async with sse_client(url, headers={"Authorization": "Bearer h34d3r"}, httpx_client_factory=client_factory) as (
        read_stream,
        write_stream,
    ):
        session = ClientSession(read_stream, write_stream)
        session.call_tool = AsyncMock(return_value=_call_result())
        await async_mcp_tool(_mcp_tool_def(), session).call({"text": "hi"})

    (payload,) = _payloads(mock_execute_request)
    _assert_direct_call(payload, _remote_server("https://sse.example.com:8443", transport="sse"))
    serialized = json.dumps(payload)
    for secret in ("s3cr3t", "k3y", "h34d3r", "Authorization"):
        assert secret not in serialized


@patch(EXECUTE_REQUEST)
async def test_stdio_metadata_and_real_call(mock_execute_request, anthropic_sdk):
    """A real stdio server: command only (a script path is not a safe identifier), no env, real tools/call."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    params = StdioServerParameters(command=sys.executable, args=[ECHO_SERVER], env={"MCP_TOKEN": "s3cr3t"})
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            (tool_def,) = (await session.list_tools()).tools
            result = await async_mcp_tool(tool_def, session).call({"text": "hi"})

    assert result == [{"type": "text", "text": "hi"}]
    (payload,) = _payloads(mock_execute_request)
    server = {
        "id": mcp_server_id(command=sys.executable),
        "managed_by": "client",
        "transport": "stdio",
        "command": sys.executable,
        "tools": {"echo": "echo"},
    }
    _assert_direct_call(payload, server)
    serialized = json.dumps(payload)
    assert "s3cr3t" not in serialized
    assert ECHO_SERVER not in serialized


@patch(EXECUTE_REQUEST)
async def test_stdio_blocked_call_never_reaches_the_server(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("DENY")
    params = StdioServerParameters(command=sys.executable, args=[ECHO_SERVER])
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            (tool_def,) = (await session.list_tools()).tools
            with patch.object(session, "call_tool", wraps=session.call_tool) as call_tool:
                with pytest.raises(AIGuardAbortError):
                    await async_mcp_tool(tool_def, session).call({"text": "hi"})
            call_tool.assert_not_called()


@patch(EXECUTE_REQUEST)
async def test_session_without_transport_metadata(mock_execute_request, anthropic_sdk):
    """A session not built from a known transport has no server key: no MCP metadata, an ordinary tool."""
    mock_execute_request.return_value = mock_evaluate_response("DENY")
    send_stream, read_stream = anyio.create_memory_object_stream(0)
    write_stream, receive_stream = anyio.create_memory_object_stream(0)
    session = ClientSession(read_stream, write_stream)
    session.call_tool = AsyncMock(return_value=_call_result())

    tool = async_mcp_tool(_mcp_tool_def(), session)
    await tool.call({"text": "hi"})

    session.call_tool.assert_awaited_once()
    mock_execute_request.assert_not_called()
    for stream in (send_stream, read_stream, write_stream, receive_stream):
        stream.close()


@patch(EXECUTE_REQUEST)
async def test_mcp_disabled(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("DENY")
    with override_ai_guard_config(dict(_ai_guard_mcp_enabled=False)):
        async with _remote_session() as session:
            tool = async_mcp_tool(_mcp_tool_def(), session)
            await tool.call({"text": "hi"})

            mock_execute_request.return_value = mock_evaluate_response("ALLOW")
            client = anthropic_sdk.Anthropic(
                api_key="<not-a-real-key>", http_client=httpx.Client(transport=_Responses([_tool_use_body()]))
            )
            client.beta.messages.create(model=MODEL, max_tokens=64, messages=_USER, tools=[tool.to_dict()])

    session.call_tool.assert_awaited_once()
    for payload in _payloads(mock_execute_request):
        assert "mcp_servers" not in payload
        assert "mcp" not in json.dumps(payload["messages"])


# ---------------------------------------------------------------------------
# Model responses: one evaluation per MCP tool operation, carrying its MCP metadata
# ---------------------------------------------------------------------------


_USER = [{"role": "user", "content": "Echo hi"}]


def _tool_use_body(name="echo", tool_input=None):
    return {
        "id": "msg_tool",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [{"type": "tool_use", "id": "toolu_1", "name": name, "input": tool_input or {"text": "hi"}}],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _text_body():
    return {
        "id": "msg_text",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [{"type": "text", "text": "done"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _stream_body(body):
    """The SSE events that stream a non-streaming message body (text and tool_use blocks)."""
    start = dict(body, content=[], stop_reason=None)
    events = [_sse("message_start", {"type": "message_start", "message": start})]
    for index, block in enumerate(body["content"]):
        if block["type"] == "text":
            empty, delta = {"type": "text", "text": ""}, {"type": "text_delta", "text": block["text"]}
        else:
            empty = dict(block, input={})
            delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
        events.append(
            _sse("content_block_start", {"type": "content_block_start", "index": index, "content_block": empty})
        )
        events.append(_sse("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta}))
        events.append(_sse("content_block_stop", {"type": "content_block_stop", "index": index}))
    message_delta = {"stop_reason": body["stop_reason"], "stop_sequence": None}
    events.append(
        _sse("message_delta", {"type": "message_delta", "delta": message_delta, "usage": {"output_tokens": 1}})
    )
    events.append(_sse("message_stop", {"type": "message_stop"}))
    return b"".join(events)


class _Responses(httpx.BaseTransport, httpx.AsyncBaseTransport):
    """Anthropic API mock returning the given message bodies in order, streamed when the request asks."""

    def __init__(self, bodies):
        self._bodies = list(bodies)

    def _response(self, request):
        body = self._bodies.pop(0)
        if json.loads(request.content).get("stream"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_stream_body(body))
        return httpx.Response(200, headers={"content-type": "application/json"}, content=json.dumps(body).encode())

    def handle_request(self, request):
        return self._response(request)

    async def handle_async_request(self, request):
        return self._response(request)


def _sync_client(sdk, bodies):
    return sdk.Anthropic(api_key="<not-a-real-key>", http_client=httpx.Client(transport=_Responses(bodies)))


def _async_client(sdk, bodies):
    return sdk.AsyncAnthropic(api_key="<not-a-real-key>", http_client=httpx.AsyncClient(transport=_Responses(bodies)))


def _assert_single_mcp_check(mock_execute_request):
    (payload,) = _tool_call_payloads(mock_execute_request)
    tool_call = payload["messages"][-1]["tool_calls"][0]
    assert tool_call["mcp"] == {"server_id": REMOTE_ID}
    assert payload["mcp_servers"] == [_remote_server()]
    return payload


@patch(EXECUTE_REQUEST)
async def test_create_then_call_is_evaluated_once(mock_execute_request, anthropic_sdk):
    """The response evaluation carries the MCP metadata, so the call of that tool_use is not evaluated again."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        ordinary = {"name": "get_time", "description": "Time", "input_schema": {"type": "object"}}
        client = _sync_client(anthropic_sdk, [_tool_use_body()])
        response = client.beta.messages.create(
            model=MODEL, max_tokens=64, messages=_USER, tools=[ordinary, tool.to_dict()]
        )
        await tool.call(response.content[0].input)
        assert mock_execute_request.call_count == 2
        # Another call with the same input is a new operation and is evaluated.
        await tool.call(response.content[0].input)

    assert session.call_tool.await_count == 2
    before, after, direct = _payloads(mock_execute_request)
    assert "mcp_servers" not in before
    assert after["messages"][-1]["tool_calls"][0]["id"] == "toolu_1"
    assert after["mcp_servers"] == [_remote_server()]
    assert after["messages"][-1]["tool_calls"][0]["mcp"] == {"server_id": REMOTE_ID}
    _assert_direct_call(direct, _remote_server())


@patch(EXECUTE_REQUEST)
async def test_unconsumed_evaluation_does_not_spare_another_context(mock_execute_request, anthropic_sdk):
    """A response tool call that never runs leaves no pass for the same call made from another context."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = _sync_client(anthropic_sdk, [_tool_use_body()])
        response = client.beta.messages.create(model=MODEL, max_tokens=64, messages=_USER, tools=[tool.to_dict()])
        assert mock_execute_request.call_count == 2

        # A task created in an empty context, like another request or thread, does not see the mark.
        task = contextvars.Context().run(asyncio.ensure_future, tool.call(response.content[0].input))
        await task
        assert mock_execute_request.call_count == 3
        _assert_direct_call(_payloads(mock_execute_request)[2], _remote_server())

        # In the context that evaluated it, the call consumes the mark once.
        await tool.call(response.content[0].input)
        assert mock_execute_request.call_count == 3


@patch(EXECUTE_REQUEST)
async def test_next_response_replaces_unconsumed_evaluations(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = _sync_client(anthropic_sdk, [_tool_use_body(), _text_body()])
        create = dict(model=MODEL, max_tokens=64, messages=_USER, tools=[tool.to_dict()])
        response = client.beta.messages.create(**create)
        client.beta.messages.create(**create)
        await tool.call(response.content[0].input)

    assert len(_tool_call_payloads(mock_execute_request)) == 2
    _assert_direct_call(_payloads(mock_execute_request)[-1], _remote_server())


@pytest.mark.parametrize("same_root", [True, False], ids=["same_root_span", "other_root_span"])
@patch(EXECUTE_REQUEST)
async def test_evaluations_are_scoped_to_the_root_span(mock_execute_request, anthropic_sdk, same_root):
    """A mark left by one request (root span) never spares the same call made under another request."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = _sync_client(anthropic_sdk, [_tool_use_body()])
        with tracer.trace("request.one"):
            response = client.beta.messages.create(model=MODEL, max_tokens=64, messages=_USER, tools=[tool.to_dict()])
            if same_root:
                await tool.call(response.content[0].input)
        if not same_root:
            with tracer.trace("request.two"):
                await tool.call(response.content[0].input)

    session.call_tool.assert_awaited_once()
    assert len(_tool_call_payloads(mock_execute_request)) == (1 if same_root else 2)


@patch(EXECUTE_REQUEST)
async def test_request_without_mcp_tools_keeps_the_marks(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = _sync_client(anthropic_sdk, [_tool_use_body(), _text_body()])
        response = client.beta.messages.create(model=MODEL, max_tokens=64, messages=_USER, tools=[tool.to_dict()])
        client.beta.messages.create(model=MODEL, max_tokens=64, messages=_USER)
        await tool.call(response.content[0].input)

    # Before and after hooks of both requests, and no check of the call.
    assert mock_execute_request.call_count == 4
    assert len(_tool_call_payloads(mock_execute_request)) == 1


class _SkipConversations(middleware.AIGuardMiddleware):
    """Skips every evaluation of more than one message, such as a model response with its request."""

    def should_evaluate(self, context):
        return len(context.request.messages) <= 1


@patch(EXECUTE_REQUEST)
async def test_middleware_skipped_response_does_not_spare_the_call(mock_execute_request, anthropic_sdk):
    # The request is allowed, the response evaluation is skipped, and the call check denies.
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response("DENY")]
    middleware.register_middleware(_SkipConversations())
    try:
        async with _remote_session() as session:
            tool = async_mcp_tool(_mcp_tool_def(), session)
            client = _sync_client(anthropic_sdk, [_tool_use_body()])
            response = client.beta.messages.create(
                model=MODEL, max_tokens=64, messages=[{"role": "user", "content": "x"}], tools=[tool.to_dict()]
            )
            mock_execute_request.reset_mock()
            with pytest.raises(AIGuardAbortError):
                await tool.call(response.content[0].input)
    finally:
        middleware._MIDDLEWARES.clear()

    session.call_tool.assert_not_called()
    _assert_direct_call(_payloads(mock_execute_request)[0], _remote_server())


@patch(EXECUTE_REQUEST)
async def test_history_tool_calls_carry_mcp_metadata(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        history = _USER + [
            {"role": "assistant", "content": _tool_use_body()["content"]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "hi"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_2", "name": "other", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_2", "content": "x"}]},
        ]
        client = _sync_client(anthropic_sdk, [_text_body()])
        ordinary = {"name": "other", "description": "Other", "input_schema": {"type": "object"}}
        client.beta.messages.create(model=MODEL, max_tokens=64, messages=history, tools=[tool.to_dict(), ordinary])

    before = _payloads(mock_execute_request)[0]
    assert before["messages"][1]["tool_calls"][0]["mcp"] == {"server_id": REMOTE_ID}
    assert "mcp" not in before["messages"][3]["tool_calls"][0]
    assert before["mcp_servers"] == [_remote_server()]


@patch(EXECUTE_REQUEST)
async def test_response_block_prevents_tools_call(mock_execute_request, anthropic_sdk_buffered):
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response("DENY")]
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = _async_client(anthropic_sdk_buffered, [_tool_use_body(), _text_body()])
        runner = client.beta.messages.tool_runner(model=MODEL, max_tokens=64, messages=_USER, tools=[tool], stream=True)
        with pytest.raises(AIGuardAbortError):
            async for _ in runner:
                pass
    session.call_tool.assert_not_called()


@patch(EXECUTE_REQUEST)
async def test_ordinary_tools_unchanged(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    client = _sync_client(anthropic_sdk, [_tool_use_body(name="get_time")])
    ordinary = {"name": "get_time", "description": "Time", "input_schema": {"type": "object"}}
    client.beta.messages.create(model=MODEL, max_tokens=64, messages=_USER, tools=[ordinary])

    for payload in _payloads(mock_execute_request):
        assert "mcp_servers" not in payload
        assert "mcp" not in json.dumps(payload["messages"])


@patch(EXECUTE_REQUEST)
async def test_sync_tool_runner(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = mcp_tool(_mcp_tool_def(), session)
        client = _sync_client(anthropic_sdk, [_tool_use_body(), _text_body()])
        runner = client.beta.messages.tool_runner(model=MODEL, max_tokens=64, messages=_USER, tools=[tool])
        await anyio.to_thread.run_sync(lambda: list(runner))

    session.call_tool.assert_awaited_once()
    _assert_single_mcp_check(mock_execute_request)


@patch(EXECUTE_REQUEST)
async def test_async_tool_runner(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = _async_client(anthropic_sdk, [_tool_use_body(), _text_body()])
        runner = client.beta.messages.tool_runner(model=MODEL, max_tokens=64, messages=_USER, tools=[tool])
        async for _ in runner:
            pass

    session.call_tool.assert_awaited_once()
    _assert_single_mcp_check(mock_execute_request)


@patch(EXECUTE_REQUEST)
async def test_async_tool_runner_block(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("DENY")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = _async_client(anthropic_sdk, [_tool_use_body(), _text_body()])
        runner = client.beta.messages.tool_runner(model=MODEL, max_tokens=64, messages=_USER, tools=[tool])
        with pytest.raises(AIGuardAbortError):
            async for _ in runner:
                pass

    session.call_tool.assert_not_called()


@pytest.mark.parametrize("buffered", [False, True], ids=["call_check", "response_check"])
@patch(EXECUTE_REQUEST)
async def test_async_streaming_tool_runner(mock_execute_request, request, buffered):
    """Without stream analysis the call is checked; with it the buffered response is, and the call is not."""
    sdk = request.getfixturevalue("anthropic_sdk_buffered" if buffered else "anthropic_sdk")
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = _async_client(sdk, [_tool_use_body(), _text_body()])
        runner = client.beta.messages.tool_runner(model=MODEL, max_tokens=64, messages=_USER, tools=[tool], stream=True)
        async for stream in runner:
            await stream.get_final_message()

    session.call_tool.assert_awaited_once()
    payload = _assert_single_mcp_check(mock_execute_request)
    assert payload["messages"][-1]["tool_calls"][0]["id"].startswith("toolu_1" if buffered else "call_")
    # The next request evaluates the MCP tool call in its history with the metadata too.
    last_request = [p for p in _payloads(mock_execute_request) if p["messages"][-1]["role"] == "tool"][-1]
    assert last_request["messages"][1]["tool_calls"][0]["mcp"] == {"server_id": REMOTE_ID}
    assert last_request["mcp_servers"] == [_remote_server()]


@patch(EXECUTE_REQUEST)
async def test_sync_streaming_tool_runner(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = mcp_tool(_mcp_tool_def(), session)
        client = _sync_client(anthropic_sdk, [_tool_use_body(), _text_body()])
        runner = client.beta.messages.tool_runner(model=MODEL, max_tokens=64, messages=_USER, tools=[tool], stream=True)

        def run():
            for stream in runner:
                stream.get_final_message()

        await anyio.to_thread.run_sync(run)

    session.call_tool.assert_awaited_once()
    _assert_single_mcp_check(mock_execute_request)


@contextmanager
def _web_request():
    """A request root span stored on a core context, as web framework integrations do."""
    with tracer.trace("web.request") as span, core.context_with_data("test.web.request") as ctx:
        store_span_on_context(ctx, span)
        yield


@patch(EXECUTE_REQUEST)
async def test_async_buffered_streaming_runner_in_web_request(mock_execute_request, anthropic_sdk_buffered):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = _async_client(anthropic_sdk_buffered, [_tool_use_body(), _text_body()])
        with _web_request():
            runner = client.beta.messages.tool_runner(
                model=MODEL, max_tokens=64, messages=_USER, tools=[tool], stream=True
            )
            async for stream in runner:
                await stream.get_final_message()

    session.call_tool.assert_awaited_once()
    payload = _assert_single_mcp_check(mock_execute_request)
    assert payload["messages"][-1]["tool_calls"][0]["id"] == "toolu_1"


@pytest.mark.parametrize("web_request", [False, True], ids=["no_span", "web_request"])
@patch(EXECUTE_REQUEST)
async def test_sync_buffered_streaming_runner(mock_execute_request, anthropic_sdk_buffered, web_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = mcp_tool(_mcp_tool_def(), session)
        client = _sync_client(anthropic_sdk_buffered, [_tool_use_body(), _text_body()])

        def run():
            with _web_request() if web_request else contextlib.nullcontext():
                runner = client.beta.messages.tool_runner(
                    model=MODEL, max_tokens=64, messages=_USER, tools=[tool], stream=True
                )
                for stream in runner:
                    stream.get_final_message()

        await anyio.to_thread.run_sync(run)

    session.call_tool.assert_awaited_once()
    payload = _assert_single_mcp_check(mock_execute_request)
    assert payload["messages"][-1]["tool_calls"][0]["id"] == "toolu_1"


@patch(EXECUTE_REQUEST)
async def test_create_then_call_in_web_request(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = _sync_client(anthropic_sdk, [_tool_use_body()])
        with _web_request():
            response = client.beta.messages.create(model=MODEL, max_tokens=64, messages=_USER, tools=[tool.to_dict()])
            await tool.call(response.content[0].input)

    session.call_tool.assert_awaited_once()
    payload = _assert_single_mcp_check(mock_execute_request)
    assert payload["messages"][-1]["tool_calls"][0]["id"] == "toolu_1"


@patch(EXECUTE_REQUEST)
async def test_sibling_tasks_do_not_consume_each_others_evaluations(mock_execute_request, anthropic_sdk):
    """Tasks inheriting one context never consume the marks of their parent or of a sibling request."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = _sync_client(anthropic_sdk, [_tool_use_body(), _tool_use_body()])
        create = dict(model=MODEL, max_tokens=64, messages=_USER, tools=[tool.to_dict()])
        response = client.beta.messages.create(**create)
        sibling_evaluated = asyncio.Event()

        async def evaluating_branch():
            client.beta.messages.create(**create)
            sibling_evaluated.set()

        async def calling_branch():
            await sibling_evaluated.wait()
            await tool.call(response.content[0].input)

        await asyncio.gather(evaluating_branch(), calling_branch())
        # Only the calling branch was checked again; the parent still consumes its own mark once.
        assert len(_tool_call_payloads(mock_execute_request)) == 3
        _assert_direct_call(_payloads(mock_execute_request)[-1], _remote_server())
        await tool.call(response.content[0].input)
        assert len(_tool_call_payloads(mock_execute_request)) == 3


@patch(EXECUTE_REQUEST)
async def test_parse_ends_the_previous_operation(mock_execute_request, anthropic_sdk):
    """messages.parse, used by the non-streaming tool runner without hooks, clears earlier evaluations."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = _sync_client(anthropic_sdk, [_tool_use_body(), _text_body()])
        response = client.beta.messages.create(model=MODEL, max_tokens=64, messages=_USER, tools=[tool.to_dict()])
        client.beta.messages.parse(model=MODEL, max_tokens=64, messages=_USER)
        await tool.call(response.content[0].input)

    assert len(_tool_call_payloads(mock_execute_request)) == 2
    _assert_direct_call(_payloads(mock_execute_request)[-1], _remote_server())


@patch(EXECUTE_REQUEST)
async def test_direct_async_call_is_evaluated_off_the_event_loop(mock_execute_request, anthropic_sdk):
    threads = []

    def execute(*args, **kwargs):
        threads.append(threading.get_ident())
        return mock_evaluate_response("ALLOW")

    mock_execute_request.side_effect = execute
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        await tool.call({"text": "hi"})

    session.call_tool.assert_awaited_once()
    assert len(threads) == 1 and threads[0] != threading.get_ident()


@patch(EXECUTE_REQUEST)
async def test_iterable_tools_param(mock_execute_request, anthropic_sdk):
    """An iterable tools param is resolved by both hooks, so the call of its tool_use is not evaluated again."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = _sync_client(anthropic_sdk, [_tool_use_body()])
        response = client.beta.messages.create(model=MODEL, max_tokens=64, messages=_USER, tools=iter([tool.to_dict()]))
        await tool.call(response.content[0].input)

    session.call_tool.assert_awaited_once()
    _assert_single_mcp_check(mock_execute_request)


@pytest.mark.parametrize("invoke", ["call", "dunder_call", "func"])
@patch(EXECUTE_REQUEST)
async def test_every_async_invocation_is_checked_once(mock_execute_request, anthropic_sdk, invoke):
    """tool.call, tool(...) and tool.func(...) all reach the session, and each is checked exactly once."""
    mock_execute_request.return_value = mock_evaluate_response("DENY")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        run = {
            "call": lambda: tool.call({"text": "hi"}),
            "dunder_call": lambda: tool(text="hi"),
            "func": lambda: tool.func(text="hi"),
        }[invoke]
        with pytest.raises(AIGuardAbortError):
            await run()
        session.call_tool.assert_not_called()

        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        await run()

    session.call_tool.assert_awaited_once()
    payloads = _payloads(mock_execute_request)
    assert len(payloads) == 2
    _assert_direct_call(payloads[1], _remote_server())


@pytest.mark.parametrize("invoke", ["dunder_call", "func"])
@patch(EXECUTE_REQUEST)
async def test_every_sync_invocation_is_checked(mock_execute_request, anthropic_sdk, invoke):
    mock_execute_request.return_value = mock_evaluate_response("DENY")
    async with _remote_session() as session:
        tool = mcp_tool(_mcp_tool_def(), session)
        run = {"dunder_call": lambda: tool(text="hi"), "func": lambda: tool.func(text="hi")}[invoke]
        with pytest.raises(AIGuardAbortError):
            await anyio.to_thread.run_sync(run)

    session.call_tool.assert_not_called()
    _assert_direct_call(_payloads(mock_execute_request)[0], _remote_server())


async def test_guarded_func_keeps_the_tool_metadata(anthropic_sdk):
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        assert inspect.iscoroutinefunction(tool.func)
        assert tool.func.__wrapped__.__name__ == "call_mcp"
        assert tool.to_dict()["name"] == "echo"
