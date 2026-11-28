"""AI Guard evaluation of the client-managed MCP tools of the Anthropic SDK (anthropic.lib.tools.mcp)."""

from contextlib import asynccontextmanager
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
from ddtrace.aiguard.integrations._mcp import mcp_server  # noqa: E402
from ddtrace.aiguard.integrations._mcp import safe_stdio_args  # noqa: E402
from ddtrace.aiguard.integrations._mcp import stdio_program  # noqa: E402
from tests.aiguard.anthropic.conftest import async_client  # noqa: E402
from tests.aiguard.anthropic.conftest import payloads as _payloads  # noqa: E402
from tests.aiguard.anthropic.conftest import sync_client  # noqa: E402
from tests.aiguard.utils import mock_evaluate_response  # noqa: E402
from tests.aiguard.utils import override_ai_guard_config  # noqa: E402


MODEL = "claude-sonnet-4-5"
SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
REMOTE_URL = "https://user:s3cr3t@MCP.Example.com:443/mcp/v1?token=t0k3n#frag"
# The reported URL: WHATWG host and path, without userinfo, query or fragment.
REMOTE_SENT_URL = "https://mcp.example.com/mcp/v1"
ECHO_SERVER = os.path.join(os.path.dirname(__file__), "mcp_echo_server.py")
# mcp>=2 transports use httpx2, mcp<2 httpx.
HTTP = getattr(mcp_httpx_utils, "httpx2", httpx)

EXECUTE_REQUEST = "ddtrace.aiguard._api_client.AIGuardClient._execute_request"


def _mcp_tool_def(name="echo"):
    return mcp_types.Tool.model_validate({"name": name, "description": "Echo the text", "inputSchema": SCHEMA})


def _call_result(text="echoed"):
    return mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text=text)])


def _tool_call_payloads(mock_execute_request):
    """Evaluations whose target is an assistant tool call: the model responses and the call checks."""
    return [p for p in _payloads(mock_execute_request) if p["messages"][-1].get("tool_calls")]


def _remote_server(url=REMOTE_SENT_URL, transport="streamable_http", tools=None):
    return {
        "id": "mcp_1",
        "managed_by": "client",
        "transport": transport,
        "url": url,
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
    """A call check: the lone assistant tool call, with its MCP server, or ordinary without server."""
    message = payload["messages"][0]
    assert len(payload["messages"]) == 1
    assert message["role"] == "assistant"
    (tool_call,) = message["tool_calls"]
    assert tool_call["id"].startswith("call_")
    assert tool_call["function"] == {"name": "echo", "arguments": '{"text": "hi"}'}
    if server is None:
        assert "mcp" not in tool_call
        assert "mcp_servers" not in payload
    else:
        assert tool_call["mcp"] == {"server_id": server["id"]}
        assert payload["mcp_servers"] == [server]


def _assert_ordinary_response(payload):
    """A model response evaluation: its tool_use is an ordinary tool call, without MCP metadata."""
    tool_call = payload["messages"][-1]["tool_calls"][0]
    assert tool_call["id"] == "toolu_1"
    assert "mcp" not in tool_call
    assert "mcp_servers" not in payload


# ---------------------------------------------------------------------------
# Server entries (pure unit)
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
        "id": "mcp_1",
        "managed_by": "client",
        "transport": "stdio",
        "command": "npx",
        "args": ["@modelcontextprotocol/server-github"],
        "tools": {},
    }
    assert "args" not in mcp_server("client", "stdio", command="node")


def test_remote_server_entry_keeps_the_path_without_secrets():
    assert mcp_server("client", "sse", url=REMOTE_URL) == _remote_server(transport="sse", tools={})


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
    for secret in ("s3cr3t", "t0k3n", "frag", "user"):
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
    _assert_direct_call(second_payload, _remote_server("http://localhost:8000/mcp"))


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
    _assert_direct_call(payload, _remote_server("https://sse.example.com:8443/sse", transport="sse"))
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
    # Only the program name of the interpreter path is reported.
    program = stdio_program(sys.executable)
    server = {
        "id": "mcp_1",
        "managed_by": "client",
        "transport": "stdio",
        "command": program,
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
    """A session not built from a known transport has no server metadata: its calls are checked as ordinary."""
    mock_execute_request.return_value = mock_evaluate_response("DENY")
    send_stream, read_stream = anyio.create_memory_object_stream(0)
    write_stream, receive_stream = anyio.create_memory_object_stream(0)
    session = ClientSession(read_stream, write_stream)
    session.call_tool = AsyncMock(return_value=_call_result())

    tool = async_mcp_tool(_mcp_tool_def(), session)
    with pytest.raises(AIGuardAbortError):
        await tool.call({"text": "hi"})

    session.call_tool.assert_not_called()
    (payload,) = _payloads(mock_execute_request)
    _assert_direct_call(payload, None)
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
            client = sync_client(anthropic_sdk, [_tool_use_body()])
            client.beta.messages.create(model=MODEL, max_tokens=64, messages=_USER, tools=[tool.to_dict()])

    session.call_tool.assert_awaited_once()
    for payload in _payloads(mock_execute_request):
        assert "mcp_servers" not in payload
        assert "mcp" not in json.dumps(payload["messages"])


# ---------------------------------------------------------------------------
# Model responses are evaluated as before; each MCP tool call is checked when it is sent
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


def _assert_call_check(mock_execute_request, server):
    """The only tool call evaluation is the call check: neither the parse-based runner nor an unanalyzed stream is."""
    (payload,) = _tool_call_payloads(mock_execute_request)
    _assert_direct_call(payload, server)


@patch(EXECUTE_REQUEST)
async def test_create_then_call_evaluates_the_response_and_the_call(mock_execute_request, anthropic_sdk):
    """The response evaluation has no MCP metadata; the call is checked with its server before tools/call."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        ordinary = {"name": "get_time", "description": "Time", "input_schema": {"type": "object"}}
        client = sync_client(anthropic_sdk, [_tool_use_body()])
        response = client.beta.messages.create(
            model=MODEL, max_tokens=64, messages=_USER, tools=[ordinary, tool.to_dict()]
        )
        await tool.call(response.content[0].input)

    session.call_tool.assert_awaited_once()
    before, after, call = _payloads(mock_execute_request)
    assert "mcp_servers" not in before
    _assert_ordinary_response(after)
    _assert_direct_call(call, _remote_server())


@patch(EXECUTE_REQUEST)
async def test_call_check_blocks_after_an_allowed_response(mock_execute_request, anthropic_sdk):
    """The MCP server only reaches AI Guard with the call, so a server policy blocks there."""
    allow, deny = mock_evaluate_response("ALLOW"), mock_evaluate_response("DENY")
    mock_execute_request.side_effect = [allow, allow, deny]
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = sync_client(anthropic_sdk, [_tool_use_body()])
        response = client.beta.messages.create(model=MODEL, max_tokens=64, messages=_USER, tools=[tool.to_dict()])
        with pytest.raises(AIGuardAbortError):
            await tool.call(response.content[0].input)

    session.call_tool.assert_not_called()
    _assert_direct_call(_payloads(mock_execute_request)[2], _remote_server())


@patch(EXECUTE_REQUEST)
async def test_history_mcp_tool_calls_are_ordinary(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        history = _USER + [
            {"role": "assistant", "content": _tool_use_body()["content"]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "hi"}]},
        ]
        client = sync_client(anthropic_sdk, [_text_body()])
        client.beta.messages.create(model=MODEL, max_tokens=64, messages=history, tools=[tool.to_dict()])

    before, after = _payloads(mock_execute_request)
    for payload in (before, after):
        (tool_call,) = payload["messages"][1]["tool_calls"]
        assert tool_call["id"] == "toolu_1"
        assert "mcp" not in tool_call
        assert "mcp_servers" not in payload


@pytest.mark.parametrize(
    "connector_url",
    ["https://docs.example.com/mcp", "https://mcp.example.com/connector"],
    ids=["other_origin", "same_origin"],
)
@patch(EXECUTE_REQUEST)
async def test_connector_call_named_like_a_client_tool(mock_execute_request, anthropic_sdk, connector_url):
    """An MCP connector call named like a client-managed tool keeps its connector server.

    The client-managed tool is still checked when it runs, whatever the origin of the connector server.
    """
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    connector = {"type": "url", "name": "docs", "url": connector_url}
    call = {"type": "mcp_tool_use", "id": "mcptoolu_1", "name": "echo", "server_name": "docs", "input": {"text": "hi"}}
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = sync_client(anthropic_sdk, [dict(_text_body(), content=[call])])
        client.beta.messages.create(
            model=MODEL,
            max_tokens=64,
            messages=_USER,
            tools=[tool.to_dict()],
            mcp_servers=[connector],
            betas=["mcp-client-2025-11-20"],
        )
        await tool.call({"text": "hi"})

    session.call_tool.assert_awaited_once()
    _, after, direct = _payloads(mock_execute_request)
    assert after["messages"][-1]["tool_calls"][0]["mcp"] == {"server_id": "mcp_1"}
    assert [(server["id"], server["managed_by"], server["url"]) for server in after["mcp_servers"]] == [
        ("mcp_1", "provider", connector_url)
    ]
    _assert_direct_call(direct, _remote_server())


@patch(EXECUTE_REQUEST)
async def test_response_block_prevents_tools_call(mock_execute_request, anthropic_sdk_buffered):
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response("DENY")]
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = async_client(anthropic_sdk_buffered, [_tool_use_body(), _text_body()])
        runner = client.beta.messages.tool_runner(model=MODEL, max_tokens=64, messages=_USER, tools=[tool], stream=True)
        with pytest.raises(AIGuardAbortError):
            async for _ in runner:
                pass
    session.call_tool.assert_not_called()


@patch(EXECUTE_REQUEST)
async def test_ordinary_tools_unchanged(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    client = sync_client(anthropic_sdk, [_tool_use_body(name="get_time")])
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
        client = sync_client(anthropic_sdk, [_tool_use_body(), _text_body()])
        runner = client.beta.messages.tool_runner(model=MODEL, max_tokens=64, messages=_USER, tools=[tool])
        await anyio.to_thread.run_sync(lambda: list(runner))

    session.call_tool.assert_awaited_once()
    _assert_call_check(mock_execute_request, _remote_server())


@patch(EXECUTE_REQUEST)
async def test_async_tool_runner(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = async_client(anthropic_sdk, [_tool_use_body(), _text_body()])
        runner = client.beta.messages.tool_runner(model=MODEL, max_tokens=64, messages=_USER, tools=[tool])
        async for _ in runner:
            pass

    session.call_tool.assert_awaited_once()
    _assert_call_check(mock_execute_request, _remote_server())


@patch(EXECUTE_REQUEST)
async def test_async_tool_runner_block(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("DENY")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = async_client(anthropic_sdk, [_tool_use_body(), _text_body()])
        runner = client.beta.messages.tool_runner(model=MODEL, max_tokens=64, messages=_USER, tools=[tool])
        with pytest.raises(AIGuardAbortError):
            async for _ in runner:
                pass

    session.call_tool.assert_not_called()


@pytest.mark.parametrize("buffered", [False, True], ids=["no_stream_analysis", "stream_analysis"])
@patch(EXECUTE_REQUEST)
async def test_async_streaming_tool_runner(mock_execute_request, request, buffered):
    """With stream analysis the buffered response is evaluated too; the call is always checked with its server."""
    sdk = request.getfixturevalue("anthropic_sdk_buffered" if buffered else "anthropic_sdk")
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = async_client(sdk, [_tool_use_body(), _text_body()])
        runner = client.beta.messages.tool_runner(model=MODEL, max_tokens=64, messages=_USER, tools=[tool], stream=True)
        async for stream in runner:
            await stream.get_final_message()

    session.call_tool.assert_awaited_once()
    payloads = _tool_call_payloads(mock_execute_request)
    if buffered:
        response, call = payloads
        _assert_ordinary_response(response)
    else:
        (call,) = payloads
    _assert_direct_call(call, _remote_server())
    # The next request evaluates the MCP tool call of its history as an ordinary tool call.
    last_request = [p for p in _payloads(mock_execute_request) if p["messages"][-1]["role"] == "tool"][-1]
    assert "mcp" not in last_request["messages"][1]["tool_calls"][0]
    assert "mcp_servers" not in last_request


@patch(EXECUTE_REQUEST)
async def test_sync_streaming_tool_runner(mock_execute_request, anthropic_sdk):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session() as session:
        tool = mcp_tool(_mcp_tool_def(), session)
        client = sync_client(anthropic_sdk, [_tool_use_body(), _text_body()])
        runner = client.beta.messages.tool_runner(model=MODEL, max_tokens=64, messages=_USER, tools=[tool], stream=True)

        def run():
            for stream in runner:
                stream.get_final_message()

        await anyio.to_thread.run_sync(run)

    session.call_tool.assert_awaited_once()
    _assert_call_check(mock_execute_request, _remote_server())


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


# ---------------------------------------------------------------------------
# Servers that nothing tells apart: the calls are still checked, as ordinary tool calls
# ---------------------------------------------------------------------------


# Reachable servers whose URL cannot be reported, so a transport without a name has no mcp_servers entry.
UNIDENTIFIED_URLS = [
    "https://bücher.example/mcp",
    "https://xn--bcher-kva.example/mcp",
    "https://[fe80::1%25eth0]/mcp",
]


@pytest.mark.parametrize("url", UNIDENTIFIED_URLS)
@patch(EXECUTE_REQUEST)
async def test_unidentified_server_direct_call_is_checked(mock_execute_request, anthropic_sdk, url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session(url) as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        await tool.call({"text": "hi"})

        mock_execute_request.return_value = mock_evaluate_response("DENY")
        with pytest.raises(AIGuardAbortError):
            await tool.call({"text": "hi"})

    session.call_tool.assert_awaited_once()
    allowed, denied = _payloads(mock_execute_request)
    _assert_direct_call(allowed, None)
    _assert_direct_call(denied, None)


@pytest.mark.parametrize("url", UNIDENTIFIED_URLS)
@patch(EXECUTE_REQUEST)
async def test_unidentified_server_direct_sync_call_block(mock_execute_request, anthropic_sdk, url):
    mock_execute_request.return_value = mock_evaluate_response("DENY")
    async with _remote_session(url) as session:
        tool = mcp_tool(_mcp_tool_def(), session)
        with pytest.raises(AIGuardAbortError):
            await anyio.to_thread.run_sync(tool.call, {"text": "hi"})

    session.call_tool.assert_not_called()
    (payload,) = _payloads(mock_execute_request)
    _assert_direct_call(payload, None)


@pytest.mark.parametrize("url", UNIDENTIFIED_URLS)
@patch(EXECUTE_REQUEST)
async def test_unidentified_server_tool_runner_block(mock_execute_request, anthropic_sdk, url):
    """messages.parse has no evaluation, so the call check is the only one of the non-streaming runner."""
    mock_execute_request.return_value = mock_evaluate_response("DENY")
    async with _remote_session(url) as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = async_client(anthropic_sdk, [_tool_use_body(), _text_body()])
        runner = client.beta.messages.tool_runner(model=MODEL, max_tokens=64, messages=_USER, tools=[tool])
        with pytest.raises(AIGuardAbortError):
            async for _ in runner:
                pass

    session.call_tool.assert_not_called()
    (payload,) = _tool_call_payloads(mock_execute_request)
    _assert_direct_call(payload, None)


@pytest.mark.parametrize("url", UNIDENTIFIED_URLS)
@patch(EXECUTE_REQUEST)
async def test_unidentified_server_streaming_tool_runner_block(mock_execute_request, anthropic_sdk, url):
    """Without stream analysis, the call check blocks the call before the tool runs."""
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response("DENY")]
    async with _remote_session(url) as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = async_client(anthropic_sdk, [_tool_use_body(), _text_body()])
        runner = client.beta.messages.tool_runner(model=MODEL, max_tokens=64, messages=_USER, tools=[tool], stream=True)
        with pytest.raises(AIGuardAbortError):
            async for stream in runner:
                await stream.get_final_message()

    session.call_tool.assert_not_called()
    (payload,) = _tool_call_payloads(mock_execute_request)
    _assert_direct_call(payload, None)


@patch(EXECUTE_REQUEST)
async def test_unidentified_server_buffered_response_and_call_are_checked(mock_execute_request, anthropic_sdk_buffered):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    async with _remote_session(UNIDENTIFIED_URLS[0]) as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = async_client(anthropic_sdk_buffered, [_tool_use_body(), _text_body()])
        runner = client.beta.messages.tool_runner(model=MODEL, max_tokens=64, messages=_USER, tools=[tool], stream=True)
        async for stream in runner:
            await stream.get_final_message()

    session.call_tool.assert_awaited_once()
    response, call = _tool_call_payloads(mock_execute_request)
    _assert_ordinary_response(response)
    _assert_direct_call(call, None)


@patch(EXECUTE_REQUEST)
async def test_unidentified_server_response_block_prevents_tools_call(mock_execute_request, anthropic_sdk_buffered):
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response("DENY")]
    async with _remote_session(UNIDENTIFIED_URLS[0]) as session:
        tool = async_mcp_tool(_mcp_tool_def(), session)
        client = async_client(anthropic_sdk_buffered, [_tool_use_body(), _text_body()])
        runner = client.beta.messages.tool_runner(model=MODEL, max_tokens=64, messages=_USER, tools=[tool], stream=True)
        with pytest.raises(AIGuardAbortError):
            async for _ in runner:
                pass

    session.call_tool.assert_not_called()
