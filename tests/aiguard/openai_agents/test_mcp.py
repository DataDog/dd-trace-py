"""AI Guard evaluation of MCP tool calls run by openai-agents MCP servers."""

import asyncio
import json
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
from ddtrace.aiguard.integrations._mcp import _model_tool_calls
from ddtrace.aiguard.integrations._mcp import mcp_reference
from ddtrace.aiguard.integrations._mcp import mcp_server
from ddtrace.aiguard.integrations._mcp import mcp_server_id
from ddtrace.aiguard.integrations._mcp import record_model_tool_calls
from ddtrace.aiguard.integrations._mcp import tool_call_conversation
from ddtrace.aiguard.integrations._openai_agents import _openai_agents_mcp_call_tool_before
from ddtrace.aiguard.integrations._openai_responses import _openai_response_create_after
from ddtrace.aiguard.integrations._openai_responses import _openai_response_create_before
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


@pytest.fixture(autouse=True)
def _reset_model_tool_calls():
    token = _model_tool_calls.set(None)
    yield
    _model_tool_calls.reset(token)


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
def test_model_call_reuses_the_conversation(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    response = {
        "output": [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "atlassian_search_issues",
                "arguments": '{"query": "APPSEC"}',
            },
            {"type": "function_call", "call_id": "call_2", "name": "other_tool", "arguments": "{}"},
        ]
    }
    _openai_response_create_after(new_ai_guard_client(), {"input": "Find APPSEC issues"}, response)

    _invoke(_atlassian_server())

    assert _messages(mock_execute_request) == [
        {"role": "user", "content": "Find APPSEC issues"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "atlassian_search_issues", "arguments": '{"query": "APPSEC"}'},
                    "mcp": {"server_id": ATLASSIAN_ID},
                }
            ],
        },
    ]
    assert _mcp_servers(mock_execute_request) == [ATLASSIAN_SERVER]


@patch(EXECUTE_REQUEST)
def test_history_keeps_the_hosted_servers_it_references(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    kwargs = {
        "input": "Find APPSEC issues",
        "tools": [{"type": "mcp", "server_label": "deepwiki", "server_url": "https://mcp.deepwiki.com/mcp"}],
    }
    response = {
        "output": [
            {
                "type": "mcp_call",
                "id": "mcp_1",
                "server_label": "deepwiki",
                "name": "ask_question",
                "arguments": "{}",
                "output": "answer",
            },
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "atlassian_search_issues",
                "arguments": '{"query": "APPSEC"}',
            },
        ]
    }
    _openai_response_create_after(new_ai_guard_client(), kwargs, response)

    _invoke(_atlassian_server())

    servers = _mcp_servers(mock_execute_request)
    assert [server["id"] for server in servers] == [mcp_server_id(url="https://mcp.deepwiki.com"), ATLASSIAN_ID]
    assert servers[0]["managed_by"] == "provider"
    referenced = {
        tool_call["mcp"]["server_id"]
        for message in _messages(mock_execute_request)
        for tool_call in message.get("tool_calls") or []
    }
    assert referenced == {server["id"] for server in servers}


@patch(EXECUTE_REQUEST)
def test_history_server_sharing_the_id_never_describes_the_evaluated_call(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    kwargs = {
        "input": "Find APPSEC issues",
        "tools": [{"type": "mcp", "server_label": "atlassian_hosted", "server_url": "https://mcp.example.com/sse"}],
    }
    response = {
        "output": [
            {
                "type": "mcp_call",
                "id": "mcp_1",
                "server_label": "atlassian_hosted",
                "name": "get_issue",
                "arguments": "{}",
                "output": "issue",
            },
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "atlassian_search_issues",
                "arguments": '{"query": "APPSEC"}',
            },
        ]
    }
    _openai_response_create_after(new_ai_guard_client(), kwargs, response)

    _invoke(_atlassian_server())

    # Same endpoint, same ID: the evaluated client call keeps its own entry, and the hosted call it
    # cannot share an entry with is sent as an ordinary tool call.
    assert _mcp_servers(mock_execute_request) == [ATLASSIAN_SERVER]
    hosted_call = _messages(mock_execute_request)[1]["tool_calls"][0]
    assert hosted_call == {"id": "mcp_1", "function": {"name": "get_issue", "arguments": "{}"}}


def _history_call(call_id, name, server, original_name):
    return {
        "id": call_id,
        "function": {"name": name, "arguments": "{}"},
        "mcp": mcp_reference(server, name, original_name),
    }


def _current_call(server, name="search", original_name="search"):
    tool_call = {"id": "call_2", "function": {"name": name, "arguments": "{}"}}
    tool_call["mcp"] = mcp_reference(server, name, original_name)
    return tool_call


def _record(history_call, history_server):
    messages = [
        {"role": "user", "content": "Find issues"},
        {"role": "assistant", "tool_calls": [history_call]},
        {"role": "tool", "tool_call_id": history_call["id"], "content": "done"},
        {"role": "assistant", "tool_calls": [{"id": "call_2", "function": {"name": "search", "arguments": "{}"}}]},
    ]
    record_model_tool_calls(messages, 3, [history_server])
    return messages


def test_conflicting_tool_mapping_on_a_shared_id_is_not_sent():
    """Same description, but the history maps the evaluated call's name to another original tool."""
    history_server = mcp_server("client", "streamable_http", name="atlassian", url="https://mcp.example.com")
    server = mcp_server("client", "streamable_http", name="atlassian", url="https://mcp.example.com")
    messages = _record(_history_call("call_1", "search", history_server, "search_v1"), history_server)

    conversation, servers = tool_call_conversation(_current_call(server), server)

    assert servers == [dict(server, tools={"search": "search"})]
    assert "mcp" not in conversation[1]["tool_calls"][0]
    assert "mcp" in messages[1]["tool_calls"][0], "the recorded history is never mutated"


def test_matching_history_on_a_shared_id_keeps_its_reference():
    history_server = mcp_server("client", "streamable_http", name="atlassian", url="https://mcp.example.com")
    server = mcp_server("client", "streamable_http", name="atlassian", url="https://mcp.example.com")
    _record(_history_call("call_1", "get_issue", history_server, "get_issue"), history_server)

    conversation, servers = tool_call_conversation(_current_call(server), server)

    assert conversation[1]["tool_calls"][0]["mcp"] == {"server_id": server["id"]}
    assert servers == [dict(server, tools={"get_issue": "get_issue", "search": "search"})]


def _record_atlassian_response(name="atlassian_search_issues", arguments='{"query": "APPSEC"}'):
    response = {"output": [{"type": "function_call", "call_id": "call_1", "name": name, "arguments": arguments}]}
    _openai_response_create_after(new_ai_guard_client(), {"input": "Earlier operation"}, response)


@pytest.mark.parametrize(
    "name,arguments",
    [("other_tool", '{"query": "APPSEC"}'), ("atlassian_search_issues", '{"query": "OTHER"}')],
    ids=["other_tool", "other_arguments"],
)
@patch(EXECUTE_REQUEST)
def test_reused_call_id_of_another_call_sends_only_the_tool_call(mock_execute_request, name, arguments):
    """A call ID alone does not tie an MCP call to a recorded conversation: an earlier operation may reuse it."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    _record_atlassian_response(name, arguments)

    _invoke(_atlassian_server())

    assert _single_tool_call(_messages(mock_execute_request))["id"] == "call_1"


@patch(EXECUTE_REQUEST)
def test_equivalent_json_arguments_still_match(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    _record_atlassian_response(arguments='{ "query" : "APPSEC" }')

    _invoke(_atlassian_server())

    assert _messages(mock_execute_request)[0] == {"role": "user", "content": "Earlier operation"}


@patch(EXECUTE_REQUEST)
def test_new_model_request_forgets_the_previous_operation(mock_execute_request):
    """A request whose response is never recorded (e.g. an unevaluated stream) leaves no stale calls behind."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    _record_atlassian_response()

    _openai_response_create_before(new_ai_guard_client(), {"input": "Next operation", "stream": True})

    assert _model_tool_calls.get() is None


@patch(EXECUTE_REQUEST)
def test_agent_turn_forgets_the_previous_operation(mock_execute_request):
    """A turn run by a custom model provider never reaches the OpenAI listeners, so the turn itself clears."""
    from ddtrace.contrib.internal.openai_agents.patch import _patched_run_single_turn

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    _record_atlassian_response()
    seen_by_the_turn = []

    async def run_turn():
        seen_by_the_turn.append(_model_tool_calls.get())
        return "turn result"

    assert asyncio.run(_patched_run_single_turn(run_turn, None, (), {})) == "turn result"
    assert seen_by_the_turn == [None]


@patch(EXECUTE_REQUEST)
def test_unknown_call_id_sends_only_the_tool_call(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    _openai_response_create_after(
        new_ai_guard_client(),
        {"input": "Find APPSEC issues"},
        {"output": [{"type": "function_call", "call_id": "call_9", "name": "x", "arguments": "{}"}]},
    )

    _invoke(_atlassian_server())

    assert _single_tool_call(_messages(mock_execute_request))["id"] == "call_1"


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
    assert {
        "openai_agents.mcp.invoke_tool.before",
        "openai_agents.mcp.call_tool.before",
        "openai_agents.turn.started",
    } <= events
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
