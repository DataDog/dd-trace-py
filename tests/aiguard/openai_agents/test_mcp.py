"""AI Guard evaluation of MCP tool calls run by openai-agents MCP servers."""

import asyncio
import json
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
from ddtrace.aiguard._listener import _openai_listen
from ddtrace.aiguard.integrations._mcp import _model_tool_calls
from ddtrace.aiguard.integrations._openai_responses import _openai_response_create_after
from tests.aiguard.utils import mock_evaluate_response
from tests.aiguard.utils import override_ai_guard_config


EXECUTE_REQUEST = "ddtrace.aiguard._api_client.AIGuardClient._execute_request"
ATLASSIAN_MCP = {
    "transport": "streamable_http",
    "tool_name": "search_issues",
    "name": "atlassian",
    "url": "https://mcp.example.com/mcp",
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


def _messages(mock_execute_request, index=-1):
    return mock_execute_request.call_args_list[index].args[1]["data"]["attributes"]["messages"]


def _single_tool_call(messages):
    assert len(messages) == 1
    assert messages[0]["role"] == "assistant"
    (tool_call,) = messages[0]["tool_calls"]
    return tool_call


@patch(EXECUTE_REQUEST)
def test_agent_driven_call_is_evaluated_once_with_mcp_identity(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    server = _atlassian_server()

    _invoke(server)

    assert server.session.calls == [("search_issues", {"query": "APPSEC"})]
    assert mock_execute_request.call_count == 1
    assert _single_tool_call(_messages(mock_execute_request)) == {
        "id": "call_1",
        "function": {"name": "atlassian_search_issues", "arguments": '{"query": "APPSEC"}'},
        "mcp": ATLASSIAN_MCP,
    }


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
    assert ai_guard_span.get_tag(AI_GUARD.MCP_SERVER_NAME_TAG) == "atlassian"
    assert ai_guard_span.get_tag(AI_GUARD.MCP_TOOL_NAME_TAG) == "search_issues"
    assert ai_guard_span.get_tag(AI_GUARD.MCP_TRANSPORT_TAG) == "streamable_http"
    assert ai_guard_span.get_tag(AI_GUARD.MCP_SERVER_URL_TAG) == "https://mcp.example.com/mcp"


@patch(EXECUTE_REQUEST)
def test_monitor_mode_and_evaluation_errors_preserve_execution(mock_execute_request):
    server = _atlassian_server()

    mock_execute_request.return_value = mock_evaluate_response("DENY", block=False)
    _invoke(server)
    mock_execute_request.side_effect = ConnectionError("unreachable")
    _invoke(server)

    assert len(server.session.calls) == 2


@patch(EXECUTE_REQUEST)
def test_direct_server_call_uses_a_local_id(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    server = _atlassian_server()

    asyncio.run(server.call_tool("search_issues", {"query": "APPSEC"}))

    tool_call = _single_tool_call(_messages(mock_execute_request))
    assert tool_call["id"].startswith("dd_mcp_")
    assert tool_call["function"] == {"name": "search_issues", "arguments": '{"query": "APPSEC"}'}
    assert tool_call["mcp"] == ATLASSIAN_MCP


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
                    "mcp": ATLASSIAN_MCP,
                }
            ],
        },
    ]


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

    first, second = (_single_tool_call(_messages(mock_execute_request, i))["mcp"] for i in range(2))
    assert (first["name"], first["url"]) == ("jira", "https://mcp.example.com/mcp")
    assert (second["name"], second["url"]) == ("confluence", "https://wiki.example.com:8443/mcp")


@patch(EXECUTE_REQUEST)
def test_stdio_server_reports_no_command_arguments_or_environment(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    server = _connected(
        MCPServerStdio(params={"command": "uvx", "args": ["mcp-server", "--token", "secret-arg"], "env": {"K": "v"}})
    )

    _invoke(server)

    payload = json.dumps(mock_execute_request.call_args_list[-1].args[1])
    assert _single_tool_call(_messages(mock_execute_request))["mcp"] == {
        "transport": "stdio",
        "tool_name": "search_issues",
    }
    for secret in ("uvx", "secret-arg", '"K"'):
        assert secret not in payload


@patch(EXECUTE_REQUEST)
def test_generated_server_name_is_not_reported(mock_execute_request):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    server = _connected(MCPServerSse(params={"url": "https://user:secret@sse.example.com/sse?key=abc"}))

    _invoke(server)

    payload = json.dumps(mock_execute_request.call_args_list[-1].args[1])
    assert _single_tool_call(_messages(mock_execute_request))["mcp"] == {
        "transport": "sse",
        "tool_name": "search_issues",
        "url": "https://sse.example.com/sse",
    }
    for secret in ("secret", "key=abc", "Bearer"):
        assert secret not in payload


@pytest.mark.parametrize("enabled", [True, False])
def test_listeners_are_gated(enabled):
    with override_ai_guard_config(dict(_ai_guard_collect_mcp_enabled=enabled)):
        with patch("ddtrace.aiguard._listener.core.on") as core_on:
            _openai_listen(new_ai_guard_client())

    events = {c.args[0] for c in core_on.call_args_list}
    mcp_events = {"openai_agents.mcp.invoke_tool.before", "openai_agents.mcp.call_tool.before"}
    assert events & mcp_events == (mcp_events if enabled else set())


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
    assert mcp_tool_call["mcp"] == {
        "transport": "streamable_http",
        "tool_name": "search_issues",
        "name": "atlassian",
        "url": "https://mcp.example.com/mcp",
    }
