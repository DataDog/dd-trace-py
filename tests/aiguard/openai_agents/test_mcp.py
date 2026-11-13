"""AI Guard evaluation of MCP tool calls run by openai-agents MCP servers."""

import asyncio
import json
import subprocess
import sys
import threading
import types
from unittest.mock import patch

from agents.mcp import MCPServerSse
from agents.mcp import MCPServerStdio
from agents.mcp import MCPServerStreamableHttp
from agents.mcp.util import MCPUtil
from mcp.types import CallToolResult
from mcp.types import ListToolsResult
from mcp.types import TextContent
from mcp.types import Tool
import pytest


try:
    # openai 3.x moved to httpx2, which keeps the httpx transport API.
    import httpx2 as httpx
except ImportError:
    import httpx

from ddtrace.aiguard import AIGuardAbortError
from ddtrace.aiguard import new_ai_guard_client
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.aiguard.integrations._mcp import mcp_server_id
from ddtrace.aiguard.integrations._openai_agents import _openai_agents_mcp_call_tool_before
from ddtrace.contrib.internal.openai_agents import patch as openai_agents_patch
from ddtrace.internal.module import ModuleWatchdog
from tests.aiguard.utils import mock_evaluate_response
from tests.aiguard.utils import override_ai_guard_config


EXECUTE_REQUEST = "ddtrace.aiguard._api_client.AIGuardClient._execute_request"
ATLASSIAN_ID = mcp_server_id(url="https://mcp.example.com")
ATLASSIAN_SERVER = {
    "id": ATLASSIAN_ID,
    "managed_by": "client",
    "transport": "streamable_http",
    "tools": {"atlassian_search_issues": "search_issues"},
    "name": "atlassian",
    "url": "https://mcp.example.com",
}


class FakeSession:
    """Stands in for the MCP client session so no server is needed; records the tools/call requests."""

    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments, *args, **kwargs):
        self.calls.append((name, arguments))
        return CallToolResult(content=[TextContent(type="text", text="ok")])


def _connected(server):
    server.session = FakeSession()
    return server


def _atlassian_server(url="https://user:secret@MCP.Example.com:443/mcp?token=abc#frag", name="atlassian"):
    return _connected(
        MCPServerStreamableHttp(params={"url": url, "headers": {"Authorization": "Bearer secret"}}, name=name)
    )


def _tool(name="search_issues"):
    return Tool(name=name, inputSchema={"type": "object", "properties": {}})


def _tool_context(tool_call_id="call_1", tool_name="atlassian_search_issues", arguments='{"query": "APPSEC"}'):
    try:
        from agents.tool_context import ToolContext

        return ToolContext(context=None, tool_name=tool_name, tool_call_id=tool_call_id, tool_arguments=arguments)
    except (ImportError, TypeError):
        # Older SDKs pass a context without the model call details.
        return types.SimpleNamespace(tool_call_id=tool_call_id, tool_name=tool_name)


def _invoke(server, tool=None, context=None, input_json='{"query": "APPSEC"}'):
    return asyncio.run(MCPUtil.invoke_mcp_tool(server, tool or _tool(), context or _tool_context(), input_json))


def _attributes(mock_execute_request, index=-1):
    return mock_execute_request.call_args_list[index].args[1]["data"]["attributes"]


def _messages(mock_execute_request, index=-1):
    return _attributes(mock_execute_request, index)["messages"]


def _mcp_servers(mock_execute_request, index=-1):
    return _attributes(mock_execute_request, index).get("mcp_servers")


def _single_tool_call(messages):
    assert len(messages) == 1
    assert messages[0]["role"] == "assistant"
    (tool_call,) = messages[0]["tool_calls"]
    return tool_call


@patch(EXECUTE_REQUEST)
def test_agent_driven_call_is_evaluated_once_with_its_server(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    server = _atlassian_server()

    _invoke(server)

    assert server.session.calls == [("search_issues", {"query": "APPSEC"})]
    assert mock_execute_request.call_count == 1
    assert _single_tool_call(_messages(mock_execute_request)) == {
        "id": "call_1",
        "function": {"name": "atlassian_search_issues", "arguments": '{"query": "APPSEC"}'},
        "mcp": {"server_id": ATLASSIAN_ID},
    }
    assert _mcp_servers(mock_execute_request) == [ATLASSIAN_SERVER]


@patch(EXECUTE_REQUEST)
def test_server_id_is_stable_across_connections(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    _invoke(_atlassian_server())
    _invoke(_atlassian_server(url="https://mcp.example.com/other-path?session=2"))

    assert [_mcp_servers(mock_execute_request, i)[0]["id"] for i in range(2)] == [ATLASSIAN_ID, ATLASSIAN_ID]


@pytest.mark.parametrize("decision", ["DENY", "ABORT"])
@patch(EXECUTE_REQUEST)
def test_blocking_verdict_prevents_the_tools_call(mock_execute_request, decision, test_spans):
    mock_execute_request.return_value = mock_evaluate_response(decision, block=True)
    server = _atlassian_server()

    with pytest.raises(AIGuardAbortError):
        _invoke(server)

    assert server.session.calls == []
    (ai_guard_span,) = [span for span in test_spans.spans if span.name == AI_GUARD.RESOURCE_TYPE]
    assert ai_guard_span.get_tag(AI_GUARD.BLOCKED_TAG) == "true"
    assert ai_guard_span.get_tag(AI_GUARD.TOOL_NAME_TAG) == "atlassian_search_issues"
    assert ai_guard_span.get_tag(AI_GUARD.INTEGRATION_TAG) == AI_GUARD.INTEGRATION_OPENAI_AGENTS


@patch(EXECUTE_REQUEST)
def test_monitor_mode_and_evaluation_errors_preserve_execution(mock_execute_request):
    server = _atlassian_server()

    mock_execute_request.return_value = mock_evaluate_response("DENY", block=False)
    _invoke(server)
    mock_execute_request.side_effect = ConnectionError("unreachable")
    _invoke(server)

    assert len(server.session.calls) == 2


@patch(EXECUTE_REQUEST)
def test_evaluation_runs_off_the_event_loop(mock_execute_request):
    evaluation_threads = []

    def _evaluate(url, payload):
        evaluation_threads.append(threading.get_ident())
        return mock_evaluate_response("ALLOW")

    mock_execute_request.side_effect = _evaluate
    server = _atlassian_server()

    async def _run():
        await MCPUtil.invoke_mcp_tool(server, _tool(), _tool_context(), '{"query": "APPSEC"}')
        await server.call_tool("search_issues", {"query": "APPSEC"})
        return threading.get_ident()

    loop_thread = asyncio.run(_run())

    assert len(evaluation_threads) == 2
    assert loop_thread not in evaluation_threads


def test_no_listener_dispatches_on_the_event_loop():
    from ddtrace.contrib.internal.openai_agents import patch as openai_agents_patch

    server = _atlassian_server()
    with (
        patch.object(openai_agents_patch.core, "has_listeners", return_value=False),
        patch.object(openai_agents_patch.asyncio, "to_thread") as to_thread,
    ):
        _invoke(server)

    to_thread.assert_not_called()
    assert server.session.calls == [("search_issues", {"query": "APPSEC"})]


@patch(EXECUTE_REQUEST)
def test_direct_server_call_uses_a_local_id(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    server = _atlassian_server()

    asyncio.run(server.call_tool("search_issues", {"query": "APPSEC"}))

    tool_call = _single_tool_call(_messages(mock_execute_request))
    assert tool_call["id"].startswith("dd_mcp_")
    assert tool_call["function"] == {"name": "search_issues", "arguments": '{"query": "APPSEC"}'}
    assert tool_call["mcp"] == {"server_id": ATLASSIAN_ID}
    assert _mcp_servers(mock_execute_request) == [dict(ATLASSIAN_SERVER, tools={"search_issues": "search_issues"})]


@patch(EXECUTE_REQUEST)
def test_direct_call_after_an_agent_call_is_evaluated(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    server = _atlassian_server()

    async def _run():
        await MCPUtil.invoke_mcp_tool(server, _tool(), _tool_context(), '{"query": "APPSEC"}')
        await server.call_tool("search_issues", {"query": "APPSEC"})

    asyncio.run(_run())

    assert mock_execute_request.call_count == 2
    assert _single_tool_call(_messages(mock_execute_request))["id"].startswith("dd_mcp_")


@patch(EXECUTE_REQUEST)
def test_blocked_agent_call_does_not_excuse_a_direct_call(mock_execute_request):
    server = _atlassian_server()
    mock_execute_request.return_value = mock_evaluate_response("DENY", block=True)

    async def _run():
        with pytest.raises(AIGuardAbortError):
            await MCPUtil.invoke_mcp_tool(server, _tool(), _tool_context(), '{"query": "APPSEC"}')
        await server.call_tool("search_issues", {"query": "APPSEC"})

    with pytest.raises(AIGuardAbortError):
        asyncio.run(_run())

    assert mock_execute_request.call_count == 2
    assert server.session.calls == []


@patch(EXECUTE_REQUEST)
def test_same_tool_on_two_servers_keeps_each_identity(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    _invoke(_atlassian_server(name="jira"))
    _invoke(_atlassian_server(url="https://wiki.example.com:8443/mcp", name="confluence"))

    first, second = (_mcp_servers(mock_execute_request, i)[0] for i in range(2))
    assert (first["name"], first["url"]) == ("jira", "https://mcp.example.com")
    assert (second["name"], second["url"]) == ("confluence", "https://wiki.example.com:8443")
    assert first["id"] != second["id"]


@patch(EXECUTE_REQUEST)
def test_stdio_server_reports_only_the_launched_package(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    server = _connected(
        MCPServerStdio(params={"command": "uvx", "args": ["mcp-server", "--token", "secret-arg"], "env": {"K": "v"}})
    )

    _invoke(server)

    payload = json.dumps(mock_execute_request.call_args_list[-1].args[1])
    assert _mcp_servers(mock_execute_request) == [
        {
            "id": mcp_server_id(command="uvx", args=["mcp-server"]),
            "managed_by": "client",
            "transport": "stdio",
            "tools": {"atlassian_search_issues": "search_issues"},
            "command": "uvx",
            "args": ["mcp-server"],
        }
    ]
    for secret in ("--token", "secret-arg", '"K"'):
        assert secret not in payload


@patch(EXECUTE_REQUEST)
def test_generated_server_name_is_not_reported(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    server = _connected(MCPServerSse(params={"url": "https://user:secret@sse.example.com/sse?key=abc"}))

    _invoke(server)

    payload = json.dumps(mock_execute_request.call_args_list[-1].args[1])
    (server_entry,) = _mcp_servers(mock_execute_request)
    assert "name" not in server_entry
    assert (server_entry["transport"], server_entry["url"]) == ("sse", "https://sse.example.com")
    for secret in ("secret", "key=abc", "Bearer", "sse.example.com/sse"):
        assert secret not in payload


@patch(EXECUTE_REQUEST)
def test_unidentifiable_server_is_evaluated_as_an_ordinary_tool(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    _openai_agents_mcp_call_tool_before(new_ai_guard_client(), object(), "search_issues", {"query": "APPSEC"})

    assert "mcp" not in _single_tool_call(_messages(mock_execute_request))
    assert _mcp_servers(mock_execute_request) is None


def test_openai_kill_switch_keeps_the_mcp_listeners():
    from ddtrace.aiguard import _listener

    with override_ai_guard_config(dict(_ai_guard_openai_enabled=False, _ai_guard_mcp_enabled=True)):
        with patch.object(_listener.core, "on") as core_on:
            _listener.ai_guard_listen()

    events = {c.args[0] for c in core_on.call_args_list}
    assert {"openai_agents.mcp.invoke_tool.before", "openai_agents.mcp.call_tool.before"} <= events
    assert not any(event.startswith("openai.") for event in events)


@patch(EXECUTE_REQUEST)
def test_openai_kill_switch_still_guards_mcp_calls(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("DENY", block=True)
    server = _atlassian_server()

    # The listeners read the configuration per call: the OpenAI switch must not stop them.
    with override_ai_guard_config(dict(_ai_guard_openai_enabled=False, _ai_guard_mcp_enabled=True)):
        with pytest.raises(AIGuardAbortError):
            asyncio.run(server.call_tool("search_issues", {"query": "APPSEC"}))

    assert server.session.calls == []


def test_mcp_disabled_registers_no_listener():
    """Without listeners the contrib dispatches nothing, so MCP calls take no thread hop."""
    from ddtrace.aiguard import _listener

    with override_ai_guard_config(dict(_ai_guard_mcp_enabled=False)):
        with patch.object(_listener.core, "on") as core_on:
            _listener.ai_guard_listen()

    assert not any(c.args[0].startswith("openai_agents.") for c in core_on.call_args_list)


@patch(EXECUTE_REQUEST)
def test_listener_errors_fail_open(mock_execute_request):
    """Only AIGuardAbortError leaves the listeners: a failure building the evaluation lets the call run."""
    server = _atlassian_server()
    circular = {}
    circular["self"] = circular

    with patch("ddtrace.aiguard.integrations._openai_agents.evaluate_auto", side_effect=RecursionError):
        _invoke(server)
    asyncio.run(server.call_tool("search_issues", circular))

    mock_execute_request.assert_not_called()
    assert [name for name, _ in server.session.calls] == ["search_issues", "search_issues"]


@patch(EXECUTE_REQUEST)
def test_call_forwarded_to_another_server_is_evaluated(mock_execute_request):
    """The re-entrancy guard is per server: a call one server forwards to another is still evaluated."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    backend = _atlassian_server(url="https://backend.example.com/mcp", name="backend")
    router = _atlassian_server(name="router")

    class ForwardingSession(FakeSession):
        async def call_tool(self, name, arguments, *args, **kwargs):
            return await backend.call_tool(name, arguments)

    router.session = ForwardingSession()
    asyncio.run(router.call_tool("search_issues", {"query": "APPSEC"}))

    assert [_mcp_servers(mock_execute_request, i)[0]["name"] for i in range(2)] == ["router", "backend"]
    assert backend.session.calls == [("search_issues", {"query": "APPSEC"})]


@pytest.mark.parametrize("arguments,evaluated", [({"query": "APPSEC"}, False), ({"query": "OTHER"}, True)])
@patch(EXECUTE_REQUEST)
def test_agent_call_skip_needs_the_same_arguments(mock_execute_request, arguments, evaluated):
    """Only the call the adapter announced skips the direct check, not another call to the same tool."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    server = _atlassian_server()

    async def call_in_adapter_context():
        announced = openai_agents_patch._AgentMCPCall(server, "search_issues", {"query": "APPSEC"})
        token = openai_agents_patch._agent_mcp_call.set(announced)
        try:
            await server.call_tool("search_issues", arguments)
        finally:
            openai_agents_patch._agent_mcp_call.reset(token)

    asyncio.run(call_in_adapter_context())

    assert mock_execute_request.called is evaluated


def test_unpatch_restores_the_invoke_mcp_tool_classmethod():
    openai_agents_patch.unpatch()
    try:
        assert isinstance(MCPUtil.__dict__["invoke_mcp_tool"], classmethod)
        assert not any(owner is MCPUtil for owner, _, _ in openai_agents_patch._mcp_wrapped)
    finally:
        openai_agents_patch.patch()


def test_mcp_is_wrapped_when_imported_not_on_patch():
    """The SDK imports agents.mcp lazily (it needs the mcp package): patching registers import hooks instead."""
    hook_map = ModuleWatchdog._instance._hook_map
    for module_name in openai_agents_patch._MCP_MODULES:
        assert openai_agents_patch._wrap_mcp_module in hook_map[module_name]

    code = (
        "import sys, agents\n"
        "before = 'agents.mcp' in sys.modules\n"
        "from ddtrace.contrib.internal.openai_agents.patch import patch\n"
        "patch()\n"
        "assert ('agents.mcp' in sys.modules) == before, 'patch imported agents.mcp'\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@patch(EXECUTE_REQUEST)
def test_mcp_disabled_skips_the_mcp_evaluations(mock_execute_request):
    server = _atlassian_server()

    with override_ai_guard_config(dict(_ai_guard_mcp_enabled=False)):
        _invoke(server)
        asyncio.run(server.call_tool("search_issues", {"query": "APPSEC"}))

    mock_execute_request.assert_not_called()
    assert len(server.session.calls) == 2


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


class _ModelTransport(httpx.AsyncBaseTransport):
    """First turn: the model calls the MCP tool. Second turn: it answers."""

    def __init__(self):
        self.turns = 0

    async def handle_async_request(self, request):
        self.turns += 1
        if self.turns == 1:
            output = [
                {
                    "id": "fc_1",
                    "type": "function_call",
                    "call_id": "call_run",
                    "name": "search_issues",
                    "arguments": '{"query": "APPSEC"}',
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


class FakeListingSession(FakeSession):
    async def list_tools(self, *args, **kwargs):
        return ListToolsResult(tools=[_tool()])


@pytest.mark.parametrize("decision,blocked", [("ALLOW", False), ("DENY", True)])
@patch(EXECUTE_REQUEST)
def test_runner_evaluates_mcp_tool_before_it_runs(mock_execute_request, decision, blocked):
    from agents import Agent
    from agents import OpenAIResponsesModel
    from agents import Runner
    import openai

    server = MCPServerStreamableHttp(params={"url": "https://mcp.example.com/mcp"}, name="atlassian")
    server.session = FakeListingSession()
    client = openai.AsyncOpenAI(api_key="<not-a-real-key>", http_client=httpx.AsyncClient(transport=_ModelTransport()))
    agent = Agent(
        name="assistant",
        model=OpenAIResponsesModel(model="gpt-4o-mini", openai_client=client),
        mcp_servers=[server],
    )

    def _evaluate(url, payload):
        tool_calls = payload["data"]["attributes"]["messages"][-1].get("tool_calls") or []
        is_mcp = any("mcp" in tool_call for tool_call in tool_calls)
        return mock_evaluate_response(decision if is_mcp else "ALLOW", block=True)

    mock_execute_request.side_effect = _evaluate

    if blocked:
        with pytest.raises(AIGuardAbortError):
            asyncio.run(Runner.run(agent, "Find APPSEC issues"))
        assert server.session.calls == []
    else:
        result = asyncio.run(Runner.run(agent, "Find APPSEC issues"))
        assert result.final_output == "done"
        assert server.session.calls == [("search_issues", {"query": "APPSEC"})]

    mcp_evaluations = [
        c.args[1]["data"]["attributes"]["messages"]
        for c in mock_execute_request.call_args_list
        if any(
            "mcp" in tool_call for tool_call in c.args[1]["data"]["attributes"]["messages"][-1].get("tool_calls") or []
        )
    ]
    assert len(mcp_evaluations) == 1
    (mcp_tool_call,) = mcp_evaluations[0][-1]["tool_calls"]
    assert mcp_tool_call["id"] == "call_run"
    assert mcp_tool_call["mcp"] == {"server_id": ATLASSIAN_ID}
