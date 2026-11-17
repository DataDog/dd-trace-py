"""AI Guard connection checks of openai-agents MCP servers, which run before the transport opens.

The tests stay offline: stdio servers run a local echo server, and HTTP servers use a local TCP listener.
"""

import asyncio
from contextlib import asynccontextmanager
from functools import partial
import json
import pathlib
import socket
import sys
import threading
from unittest.mock import patch

from agents.mcp import MCPServerSse
from agents.mcp import MCPServerStdio
from agents.mcp import MCPServerStreamableHttp
import agents.mcp.server as sdk_server
import pytest


try:
    # openai 3.x moved to httpx2, which keeps the httpx transport API.
    import httpx2 as httpx
except ImportError:
    import httpx

from ddtrace import config
from ddtrace.aiguard import AIGuardAbortError
from ddtrace.aiguard import new_ai_guard_client
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.aiguard.integrations._openai_agents import _openai_agents_mcp_connect_before
from ddtrace.contrib.internal.openai_agents import patch as openai_agents_patch
from ddtrace.internal import core
from ddtrace.internal.native import ConnectionFailedError
from ddtrace.internal.settings.aiguard import AIGuardConfig
from tests.aiguard.utils import mock_evaluate_resource_response
from tests.aiguard.utils import mock_evaluate_response
from tests.aiguard.utils import override_ai_guard_config


EXECUTE_REQUEST = "ddtrace.aiguard._api_client.AIGuardClient._execute_request"
CONNECT_EVENT = "openai_agents.mcp.connect.before"
RESOURCE_URL = "https://api.example.com/ai-guard/evaluate/resource"
ECHO_SERVER = str(pathlib.Path(__file__).resolve().parent.parent / "anthropic" / "mcp_echo_server.py")
# Creates the marker file and then serves the echo server, so the marker proves that the subprocess started.
LAUNCHER = (
    "import pathlib, runpy, sys; pathlib.Path(sys.argv[1]).touch(); runpy.run_path(sys.argv[2], run_name='__main__')"
)


@pytest.fixture(autouse=True)
def connection_listener():
    """The session loads AI Guard with the gate at its default value, so this module registers the listener."""
    listener = partial(_openai_agents_mcp_connect_before, new_ai_guard_client())
    with override_ai_guard_config(dict(_ai_guard_mcp_connections_enabled=True)):
        core.on(CONNECT_EVENT, listener)
        try:
            yield listener
        finally:
            core.reset_listeners(CONNECT_EVENT, listener)


class _Listener:
    """Local TCP server that counts the connections it accepts and answers each one with HTTP 503."""

    def __init__(self):
        self._socket = socket.socket()
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen()
        self._socket.settimeout(0.1)
        self._stopped = threading.Event()
        self.url = f"http://127.0.0.1:{self._socket.getsockname()[1]}/mcp"
        self.origin = self.url.rsplit("/", 1)[0]
        self.accepted = 0
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stopped.is_set():
            try:
                connection, _ = self._socket.accept()
            except socket.timeout:
                continue
            self.accepted += 1
            with connection:
                connection.settimeout(5)
                try:
                    connection.recv(65536)
                    connection.sendall(
                        b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                    )
                except OSError:
                    pass

    def close(self):
        self._stopped.set()
        self._thread.join(5)
        self._socket.close()


@pytest.fixture
def listener():
    server = _Listener()
    yield server
    server.close()


@pytest.fixture
def stdio_transports():
    """Records each stdio transport that opens. The transport itself runs unchanged."""
    opened = []
    stdio_client = sdk_server.stdio_client

    @asynccontextmanager
    async def recording_stdio_client(*args, **kwargs):
        opened.append(args)
        async with stdio_client(*args, **kwargs) as streams:
            yield streams

    with patch.object(sdk_server, "stdio_client", recording_stdio_client):
        yield opened


@asynccontextmanager
async def _no_stdio_transport(*args, **kwargs):
    raise AssertionError("the stdio transport opened")
    yield


def _answer(action="ALLOW", block=True, **kwargs):
    """Answer the connection checks with action, and every other evaluation with ALLOW."""

    def execute_request(url, payload):
        if url == RESOURCE_URL:
            return mock_evaluate_resource_response(action, block=block, **kwargs)
        return mock_evaluate_response("ALLOW")

    return execute_request


def _answer_status(status):
    def execute_request(url, payload):
        response = mock_evaluate_resource_response("DENY")
        response.status = status
        return response

    return execute_request


def _targets(mock_execute_request):
    """The target of each connection check, in the order of the requests."""
    targets = []
    for call in mock_execute_request.call_args_list:
        if call.args[0] == RESOURCE_URL:
            (target,) = call.args[1]["data"]["attributes"]["targets"]
            targets.append(target)
    return targets


def _echo_server(marker, name="echo"):
    return MCPServerStdio(
        params={"command": sys.executable, "args": ["-c", LAUNCHER, str(marker), ECHO_SERVER]},
        name=name,
        client_session_timeout_seconds=30,
    )


def _echo_target(name="echo"):
    return {
        "type": "mcp_server",
        "mcp_server": {"operation": "connect", "transport": "stdio", "name": name, "command": sys.executable},
    }


async def _use(server):
    """Connect, list the tools and call the echo tool, then clean up."""
    await server.connect()
    try:
        tools = await server.list_tools()
        result = await server.call_tool("echo", {"text": "hi"})
        return [tool.name for tool in tools], result.content[0].text
    finally:
        await server.cleanup()


async def _connect_and_clean_up(server):
    """The local TCP listener answers HTTP 503, so the connection fails: only a block leaves this function.

    openai-agents 0.0.x can report that failure as a cancellation.
    """
    try:
        await server.connect()
    except (Exception, asyncio.CancelledError):
        pass
    finally:
        await server.cleanup()


@patch(EXECUTE_REQUEST)
def test_allowed_connection_is_checked_once_before_it_opens(mock_execute_request, tmp_path, stdio_transports):
    mock_execute_request.side_effect = _answer("ALLOW")
    marker = tmp_path / "started"

    assert asyncio.run(_use(_echo_server(marker))) == (["echo"], "hi")

    assert marker.exists() and len(stdio_transports) == 1
    # The initialize, tools/list and tools/call requests of the connected session need no other check.
    assert _targets(mock_execute_request) == [_echo_target()]
    assert mock_execute_request.call_args_list[0].args == (
        RESOURCE_URL,
        {
            "data": {
                "attributes": {
                    "profile": "default",
                    "targets": [_echo_target()],
                    "meta": {"service": config.service, "env": config.env},
                }
            }
        },
    )
    # The tool call still gets its Delivery 1 evaluation, on the conversation endpoint.
    assert [call.args[0].rsplit("/", 1)[1] for call in mock_execute_request.call_args_list] == ["resource", "evaluate"]


def _responses_body(output):
    return {
        "id": "resp-test",
        "object": "response",
        "created_at": 0,
        "model": "gpt-4o-mini",
        "status": "completed",
        "output": output,
        "usage": {
            "input_tokens": 1,
            "output_tokens": 1,
            "total_tokens": 2,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
        "metadata": {},
        "parallel_tool_calls": True,
        "temperature": 1.0,
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1.0,
    }


class _EchoModel(httpx.AsyncBaseTransport):
    """First turn: the model calls the echo tool. Second turn: it answers."""

    def __init__(self):
        self.turns = 0

    async def handle_async_request(self, request):
        self.turns += 1
        if self.turns == 1:
            output = [
                {
                    "id": "fc_1",
                    "type": "function_call",
                    "call_id": "call_echo",
                    "name": "echo",
                    "arguments": '{"text": "hi"}',
                    "status": "completed",
                }
            ]
        else:
            output = [
                {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "done", "annotations": []}],
                }
            ]
        return httpx.Response(200, json=_responses_body(output))


@patch(EXECUTE_REQUEST)
def test_agent_runs_on_a_connected_server_need_no_connection_check(mock_execute_request, tmp_path):
    from agents import Agent
    from agents import OpenAIResponsesModel
    from agents import Runner
    import openai

    mock_execute_request.side_effect = _answer("ALLOW")
    server = _echo_server(tmp_path / "started")

    def agent():
        client = openai.AsyncOpenAI(api_key="<not-a-real-key>", http_client=httpx.AsyncClient(transport=_EchoModel()))
        model = OpenAIResponsesModel(model="gpt-4o-mini", openai_client=client)
        return Agent(name="assistant", model=model, mcp_servers=[server])

    async def run():
        await server.connect()
        try:
            return [(await Runner.run(agent(), "Echo hi")).final_output for _ in range(2)]
        finally:
            await server.cleanup()

    assert asyncio.run(run()) == ["done", "done"]

    assert _targets(mock_execute_request) == [_echo_target()]
    mcp_tool_evaluations = [
        call
        for call in mock_execute_request.call_args_list
        if call.args[0] != RESOURCE_URL
        and any("mcp" in tool_call for tool_call in call.args[1]["data"]["attributes"]["messages"][-1]["tool_calls"])
    ]
    assert len(mcp_tool_evaluations) == 2


@patch(EXECUTE_REQUEST)
def test_reconnection_is_checked_again(mock_execute_request, tmp_path):
    mock_execute_request.side_effect = _answer("ALLOW")
    server = _echo_server(tmp_path / "started")

    async def run():
        for _ in range(2):
            await server.connect()
            await server.list_tools()
            await server.cleanup()

    asyncio.run(run())

    assert _targets(mock_execute_request) == [_echo_target(), _echo_target()]


@pytest.mark.parametrize("action", ["DENY", "ABORT"])
@patch(EXECUTE_REQUEST)
def test_blocked_stdio_connection_starts_no_subprocess(mock_execute_request, tmp_path, stdio_transports, action):
    mock_execute_request.side_effect = _answer(action, reason="MCP server denied by policy")
    marker = tmp_path / "started"
    server = _echo_server(marker)

    with pytest.raises(AIGuardAbortError) as exc_info:
        asyncio.run(_use(server))

    # The bare error, which the SDK does not convert into an error that it handles.
    assert type(exc_info.value) is AIGuardAbortError
    assert (exc_info.value.action, exc_info.value.reason) == (action, "MCP server denied by policy")
    assert stdio_transports == []
    assert not marker.exists()
    assert server.session is None
    assert _targets(mock_execute_request) == [_echo_target()]


@pytest.mark.parametrize("server_class", [MCPServerStreamableHttp, MCPServerSse])
@pytest.mark.parametrize("action,blocked", [("ALLOW", False), ("DENY", True)])
@patch(EXECUTE_REQUEST)
def test_blocked_http_connection_opens_no_network_connection(
    mock_execute_request, listener, server_class, action, blocked
):
    mock_execute_request.side_effect = _answer(action)
    server = server_class(
        params={"url": listener.url, "headers": {"Authorization": "Bearer secret"}}, client_session_timeout_seconds=5
    )

    if blocked:
        with pytest.raises(AIGuardAbortError):
            asyncio.run(_connect_and_clean_up(server))
        assert listener.accepted == 0
        assert server.session is None
    else:
        # The control of the blocked case: the same server reaches the listener when the check allows it.
        asyncio.run(_connect_and_clean_up(server))
        assert listener.accepted >= 1
    (target,) = _targets(mock_execute_request)
    assert target["mcp_server"]["url"] == listener.origin


@pytest.mark.parametrize(
    "config,execute_request,action",
    [
        pytest.param({}, _answer("DENY", block=False), "DENY", id="remote monitor mode"),
        pytest.param({"_ai_guard_block": False}, _answer("ABORT", block=True), "ABORT", id="local monitor mode"),
        pytest.param({}, ConnectionFailedError("refused"), None, id="transport error"),
        pytest.param({}, _answer_status(503), None, id="error status"),
        pytest.param({}, lambda url, payload: mock_evaluate_response("ALLOW"), None, id="no results"),
        pytest.param({}, _answer("GO_TO_SLEEP"), None, id="unknown action"),
    ],
)
@patch(EXECUTE_REQUEST)
def test_monitor_mode_and_failed_checks_let_the_connection_continue(
    mock_execute_request, tmp_path, test_spans, config, execute_request, action
):
    mock_execute_request.side_effect = execute_request
    marker = tmp_path / "started"

    with override_ai_guard_config(config):
        assert asyncio.run(_use(_echo_server(marker))) == (["echo"], "hi")

    assert marker.exists()
    (span,) = [span for span in test_spans.spans if span.get_tag(AI_GUARD.TARGET_TAG) == "mcp_server"]
    # A failed check is never reported as ALLOW: its span has the error and no action.
    assert span.error == (1 if action is None else 0)
    assert span.get_tag(AI_GUARD.ACTION_TAG) == action
    assert span.get_tag(AI_GUARD.BLOCKED_TAG) is None


@patch(EXECUTE_REQUEST)
def test_connection_check_runs_off_the_event_loop_before_the_transport(
    mock_execute_request, tmp_path, stdio_transports
):
    pending = threading.Event()
    release = threading.Event()
    check_threads = []

    def execute_request(url, payload):
        check_threads.append(threading.get_ident())
        pending.set()
        assert release.wait(30)
        return mock_evaluate_resource_response("ALLOW")

    mock_execute_request.side_effect = execute_request
    server = _echo_server(tmp_path / "started")

    async def run():
        connection = asyncio.ensure_future(server.connect())
        try:
            for _ in range(3000):
                if pending.is_set() or connection.done():
                    break
                await asyncio.sleep(0.01)
            assert pending.is_set()
            # The policy request is pending: this task still runs on the loop, and the transport stays closed.
            for _ in range(10):
                await asyncio.sleep(0.01)
            assert not connection.done()
            assert stdio_transports == []
        finally:
            release.set()
        await connection
        try:
            await server.list_tools()
        finally:
            await server.cleanup()
        return threading.get_ident()

    loop_thread = asyncio.run(run())

    assert len(stdio_transports) == 1
    assert len(check_threads) == 1
    assert check_threads[0] != loop_thread


@patch(EXECUTE_REQUEST)
def test_each_server_gets_its_own_check_with_only_its_identity(mock_execute_request, listener):
    mock_execute_request.side_effect = _answer("DENY")
    secret_url = listener.url.replace("http://", "http://user:secret@") + "?token=abc#frag"
    servers = [
        MCPServerStreamableHttp(
            params={"url": secret_url, "headers": {"Authorization": "Bearer secret"}}, name="atlassian"
        ),
        MCPServerSse(params={"url": secret_url, "headers": {"Cookie": "session=secret"}}),
        MCPServerStdio(
            params={
                "command": "uvx",
                "args": ["mcp-example", "--token", "secret-arg"],
                "env": {"API_KEY": "secret-env"},
                "cwd": "/srv/secret-dir",
            },
            name="local-docs",
        ),
    ]

    # A regression must not launch uvx: the stdio transport fails the test before it starts a process.
    with patch.object(sdk_server, "stdio_client", _no_stdio_transport):
        for server in servers:
            with pytest.raises(AIGuardAbortError):
                asyncio.run(_use(server))

    assert _targets(mock_execute_request) == [
        {
            "type": "mcp_server",
            "mcp_server": {
                "operation": "connect",
                "transport": "streamable_http",
                "name": "atlassian",
                "url": listener.origin,
            },
        },
        # The SDK generates the name "sse: <url>" when none is configured, so this target has no name.
        {"type": "mcp_server", "mcp_server": {"operation": "connect", "transport": "sse", "url": listener.origin}},
        {
            "type": "mcp_server",
            "mcp_server": {
                "operation": "connect",
                "transport": "stdio",
                "name": "local-docs",
                "command": "uvx",
                "args": ["mcp-example"],
            },
        },
    ]
    payloads = json.dumps([call.args[1] for call in mock_execute_request.call_args_list])
    for secret in ("secret", "token", "abc", "frag", "/mcp", "Bearer", "Cookie", "API_KEY", "/srv", "tools", '"id"'):
        assert secret not in payloads
    assert listener.accepted == 0


@patch(EXECUTE_REQUEST)
def test_delayed_connection_is_checked_when_it_opens(mock_execute_request, listener, stdio_transports, tmp_path):
    """When the test creates a server or its transport, no check and no I/O occur.

    The check runs when the transport opens.
    """
    mock_execute_request.side_effect = _answer("DENY")
    server = MCPServerStreamableHttp(params={"url": listener.url})
    _echo_server(tmp_path / "started")
    MCPServerSse(params={"url": listener.url})

    streams = server.create_streams()
    assert mock_execute_request.call_count == 0

    async def open_later():
        await asyncio.sleep(0)
        async with streams:
            pass

    with pytest.raises(AIGuardAbortError):
        asyncio.run(open_later())

    assert _targets(mock_execute_request) == [
        {
            "type": "mcp_server",
            "mcp_server": {"operation": "connect", "transport": "streamable_http", "url": listener.origin},
        }
    ]
    assert listener.accepted == 0
    assert stdio_transports == []
    assert not (tmp_path / "started").exists()


@pytest.mark.skipif(
    not hasattr(MCPServerStreamableHttp, "_isolated_client_session"), reason="the SDK has no isolated sessions"
)
@patch(EXECUTE_REQUEST)
def test_isolated_session_is_a_new_connection_attempt(mock_execute_request, listener):
    """Newer SDKs can retry a tool call on an isolated session, which opens a transport of its own."""
    mock_execute_request.side_effect = _answer("DENY")
    server = MCPServerStreamableHttp(params={"url": listener.url}, name="atlassian")

    async def open_isolated_session():
        async with server._isolated_client_session():
            pass

    with pytest.raises(AIGuardAbortError):
        asyncio.run(open_isolated_session())

    assert len(_targets(mock_execute_request)) == 1
    assert listener.accepted == 0


@patch(EXECUTE_REQUEST)
def test_connection_span_is_distinguishable_from_other_evaluations(mock_execute_request, tracer, test_spans, tmp_path):
    mock_execute_request.side_effect = _answer(
        "DENY", reason="MCP server denied by policy", policy={"id": "policy_123", "version": 42}
    )

    with tracer.trace("agent") as root_span:
        with pytest.raises(AIGuardAbortError):
            asyncio.run(_use(_echo_server(tmp_path / "started")))

    (span,) = [span for span in test_spans.spans if span.name == AI_GUARD.RESOURCE_TYPE]
    assert span.parent_id == root_span.span_id
    # No direction, tool_name or redacted tag: they belong to prompt and tool evaluations.
    assert {key: value for key, value in span.get_tags().items() if key.startswith("ai_guard.")} == {
        AI_GUARD.TARGET_TAG: "mcp_server",
        AI_GUARD.SOURCE_TAG: "auto",
        AI_GUARD.INTEGRATION_TAG: "openai_agents",
        AI_GUARD.ACTION_TAG: "DENY",
        AI_GUARD.REASON_TAG: "MCP server denied by policy",
        AI_GUARD.BLOCKED_TAG: "true",
        AI_GUARD.POLICY_ID_TAG: "policy_123",
        AI_GUARD.POLICY_VERSION_TAG: "42",
    }
    assert span._get_struct_tag(AI_GUARD.STRUCT) == {"targets": [_echo_target()]}
    assert root_span.get_tag(AI_GUARD.EVENT_TAG) == "true"


@patch(EXECUTE_REQUEST)
def test_server_without_a_safe_identifier_is_not_checked(mock_execute_request):
    mock_execute_request.side_effect = _answer("DENY")
    client = new_ai_guard_client()

    # An empty command gives no identifier, and the generated name "stdio: " is never reported.
    _openai_agents_mcp_connect_before(client, MCPServerStdio(params={"command": ""}))
    _openai_agents_mcp_connect_before(client, object())

    mock_execute_request.assert_not_called()


@patch(EXECUTE_REQUEST)
def test_listener_errors_fail_open(mock_execute_request, tmp_path):
    with patch("ddtrace.aiguard.integrations._openai_agents.evaluate_resource_auto", side_effect=RecursionError):
        assert asyncio.run(_use(_echo_server(tmp_path / "started"))) == (["echo"], "hi")

    assert _targets(mock_execute_request) == []


@pytest.mark.parametrize(
    "config", [{"_ai_guard_mcp_enabled": False}, {"_ai_guard_mcp_connections_enabled": False}], ids=["mcp", "gate"]
)
@patch(EXECUTE_REQUEST)
def test_listener_reads_both_switches_at_call_time(mock_execute_request, tmp_path, config):
    mock_execute_request.side_effect = _answer("DENY")

    with override_ai_guard_config(config):
        assert asyncio.run(_use(_echo_server(tmp_path / "started"))) == (["echo"], "hi")

    assert _targets(mock_execute_request) == []


@pytest.mark.parametrize(
    "mcp_enabled,connections_enabled,registered",
    [(True, AIGuardConfig()._ai_guard_mcp_connections_enabled, False), (True, True, True), (False, True, False)],
    ids=["default", "gate on", "mcp off"],
)
def test_connection_listener_needs_mcp_and_the_gate(mcp_enabled, connections_enabled, registered):
    from ddtrace.aiguard import _listener

    config = dict(_ai_guard_mcp_enabled=mcp_enabled, _ai_guard_mcp_connections_enabled=connections_enabled)
    with override_ai_guard_config(config):
        with patch.object(_listener.core, "on") as core_on:
            _listener.ai_guard_listen()

    assert (CONNECT_EVENT in {call.args[0] for call in core_on.call_args_list}) is registered


@patch(EXECUTE_REQUEST)
def test_without_the_listener_the_transport_is_unchanged(mock_execute_request, connection_listener, tmp_path):
    """The default configuration registers no listener, so the contrib dispatches nothing and makes no thread hop."""
    core.reset_listeners(CONNECT_EVENT, connection_listener)
    server = _echo_server(tmp_path / "started")

    async def connect_and_list():
        await server.connect()
        try:
            return [tool.name for tool in await server.list_tools()]
        finally:
            await server.cleanup()

    assert not isinstance(server.create_streams(), openai_agents_patch._CheckedStreams)
    with patch.object(openai_agents_patch.asyncio, "to_thread", wraps=asyncio.to_thread) as to_thread:
        assert asyncio.run(connect_and_list()) == ["echo"]

    to_thread.assert_not_called()
    mock_execute_request.assert_not_called()


def test_unpatch_restores_create_streams():
    wrapped = {
        owner: original for owner, name, original in openai_agents_patch._mcp_wrapped if name == "create_streams"
    }
    assert set(wrapped) == {MCPServerStdio, MCPServerSse, MCPServerStreamableHttp}

    openai_agents_patch.unpatch()
    try:
        for owner, original in wrapped.items():
            assert owner.__dict__["create_streams"] is original
    finally:
        openai_agents_patch.patch()
