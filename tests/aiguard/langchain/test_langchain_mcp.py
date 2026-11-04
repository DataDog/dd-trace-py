"""AI Guard evaluation of client-managed MCP tools converted by langchain-mcp-adapters."""

import contextlib
import importlib
import json
import sys
from typing import Any
from unittest.mock import patch

import pytest


pytest.importorskip("langchain_mcp_adapters")

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration  # noqa: E402
from langchain_core.outputs import ChatResult  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from mcp.types import CallToolResult  # noqa: E402
from mcp.types import ListToolsResult  # noqa: E402
from mcp.types import TextContent  # noqa: E402
from mcp.types import Tool as MCPTool  # noqa: E402

from ddtrace.aiguard import AIGuardAbortError  # noqa: E402
from ddtrace.aiguard.integrations._mcp import mcp_server_id  # noqa: E402
from tests.aiguard.utils import mock_evaluate_response  # noqa: E402
from tests.aiguard.utils import override_ai_guard_config  # noqa: E402


SEARCH = MCPTool(
    name="search",
    description="Search the docs.",
    inputSchema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
)
REMOTE_URL = "https://url-user:url-pass@MCP.example.com:8443/mcp/path?token=qs-secret#frag-secret"
REMOTE_ORIGIN = "https://mcp.example.com:8443"
SECRETS = ("url-user", "url-pass", "qs-secret", "frag-secret", "/mcp/path", "hdr-secret", "env-secret", "arg-secret")


class FakeSession:
    """Stands in for mcp.ClientSession: lists the given tools and records each call_tool."""

    def __init__(self, tools=(SEARCH,)):
        self.tools = list(tools)
        self.calls: list = []

    async def initialize(self):
        return None

    async def list_tools(self, cursor=None):
        return ListToolsResult(tools=self.tools)

    async def call_tool(self, name, arguments=None, *args, **kwargs):
        self.calls.append((name, arguments))
        return CallToolResult(content=[TextContent(type="text", text="found")])


class ScriptedChatModel(BaseChatModel):
    """Chat model that returns the scripted replies in order and accepts any tools."""

    replies: list[AIMessage]

    @property
    def _llm_type(self) -> str:
        return "fake-scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop, run_manager, **kwargs)


def _tool_call_reply(name: str, args: dict[str, Any], call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _requests(mock_execute_request) -> list[dict[str, Any]]:
    return [call[0][1]["data"]["attributes"] for call in mock_execute_request.call_args_list]


def _mcp_requests(mock_execute_request) -> list[dict[str, Any]]:
    return [request for request in _requests(mock_execute_request) if request.get("mcp_servers")]


async def _load(session=None, **kwargs) -> Any:
    from langchain_mcp_adapters import tools as adapter_tools

    return await adapter_tools.load_mcp_tools(session, **kwargs)


def _remote_connection(transport: str = "streamable_http") -> dict[str, Any]:
    return {"transport": transport, "url": REMOTE_URL, "headers": {"Authorization": "Bearer hdr-secret"}}


@contextlib.contextmanager
def _sessions_created_on_demand(session: FakeSession):
    """Make the adapter's create_session yield the fake session instead of connecting."""

    @contextlib.asynccontextmanager
    async def create_session(connection, **kwargs):
        yield session

    with patch("langchain_mcp_adapters.tools.create_session", create_session):
        yield


def _assert_no_secrets(request: dict[str, Any]) -> None:
    payload = json.dumps(request)
    for secret in SECRETS:
        assert secret not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transport, expected",
    [("streamable_http", "streamable_http"), ("http", "streamable_http"), ("sse", "sse"), ("websocket", "unknown")],
)
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_direct_call_remote_server(mock_execute_request, langchain, transport, expected):
    """A call outside ToolNode is evaluated alone, with the server's origin and none of its credentials."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    session = FakeSession()
    (search,) = await _load(session, connection=_remote_connection(transport), server_name="docs")

    assert "found" in str(await search.ainvoke({"query": "aiguard"}))

    assert session.calls == [("search", {"query": "aiguard"})]
    assert mock_execute_request.call_count == 1
    request = _requests(mock_execute_request)[0]
    server_id = mcp_server_id(url=REMOTE_ORIGIN)
    assert request["mcp_servers"] == [
        {
            "id": server_id,
            "managed_by": "client",
            "transport": expected,
            "tools": {"search": "search"},
            "name": "docs",
            "url": REMOTE_ORIGIN,
        }
    ]
    (message,) = request["messages"]
    assert message["role"] == "assistant"
    (tool_call,) = message["tool_calls"]
    assert tool_call["id"]
    assert tool_call["function"] == {"name": "search", "arguments": '{"query": "aiguard"}'}
    assert tool_call["mcp"] == {"server_id": server_id}
    _assert_no_secrets(request)


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_direct_call_stdio_server(mock_execute_request, langchain):
    """A stdio launcher reports its command and package only; other args and env are never sent."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    connection = {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@scope/pkg", "/home/u/arg-secret", "--token=tok-secret"],
        "env": {"TOKEN": "env-secret"},
    }
    (search,) = await _load(FakeSession(), connection=connection, server_name="local")

    await search.ainvoke({"query": "aiguard"})

    request = _requests(mock_execute_request)[0]
    assert request["mcp_servers"] == [
        {
            "id": mcp_server_id(command="npx", args=["@scope/pkg"]),
            "managed_by": "client",
            "transport": "stdio",
            "tools": {"search": "search"},
            "name": "local",
            "command": "npx",
            "args": ["@scope/pkg"],
        }
    ]
    _assert_no_secrets(request)
    assert "tok-secret" not in json.dumps(request)


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_npx_servers_running_different_packages_get_different_ids(mock_execute_request, langchain):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    for package in ("@modelcontextprotocol/server-filesystem", "@modelcontextprotocol/server-git"):
        connection = {"transport": "stdio", "command": "npx", "args": ["-y", package]}
        (search,) = await _load(FakeSession(), connection=connection, server_name="local")
        await search.ainvoke({"query": "a"})

    servers = [request["mcp_servers"][0] for request in _requests(mock_execute_request)]
    assert [server["args"] for server in servers] == [
        ["@modelcontextprotocol/server-filesystem"],
        ["@modelcontextprotocol/server-git"],
    ]
    assert [server["id"] for server in servers] == [
        mcp_server_id(command="npx", args=server["args"]) for server in servers
    ]
    assert servers[0]["id"] != servers[1]["id"]


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_stdio_server_without_a_known_launcher_reports_no_args(mock_execute_request, langchain):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    connection = {"transport": "stdio", "command": "node", "args": ["server.js"]}
    (search,) = await _load(FakeSession(), connection=connection, server_name="local")

    await search.ainvoke({"query": "a"})

    (server,) = _requests(mock_execute_request)[0]["mcp_servers"]
    assert server["id"] == mcp_server_id(command="node")
    assert server["command"] == "node"
    assert "args" not in server


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_direct_call_block_prevents_the_mcp_request(mock_execute_request, langchain, decision):
    """A block raises before any MCP session is created, despite the adapter's tool error handling."""
    mock_execute_request.return_value = mock_evaluate_response(decision)
    session = FakeSession()
    with _sessions_created_on_demand(session):
        (search,) = await _load(connection=_remote_connection(), server_name="docs")
    session_created = []

    @contextlib.asynccontextmanager
    async def create_session(connection, **kwargs):
        session_created.append(connection)
        yield session

    with patch("langchain_mcp_adapters.tools.create_session", create_session):
        with pytest.raises(AIGuardAbortError):
            await search.ainvoke({"query": "drop tables"})

    assert session_created == []
    assert session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_direct_call_decision_without_blocking_executes(mock_execute_request, langchain, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision, block=False)
    session = FakeSession()
    (search,) = await _load(session, connection=_remote_connection(), server_name="docs")

    assert "found" in str(await search.ainvoke({"query": "aiguard"}))

    assert mock_execute_request.call_count == 1
    assert session.calls == [("search", {"query": "aiguard"})]


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_multi_server_client_keeps_servers_apart(mock_execute_request, langchain):
    """Two servers exposing the same tool keep their own server_id and original name through get_tools."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    session = FakeSession()
    client = MultiServerMCPClient(
        {
            "alpha": {"transport": "streamable_http", "url": "https://alpha.example.com/mcp"},
            "beta": {"transport": "sse", "url": "https://beta.example.com/sse"},
        },
        tool_name_prefix=True,
    )
    with _sessions_created_on_demand(session):
        tools = {t.name: t for t in await client.get_tools()}
        await tools["alpha_search"].ainvoke({"query": "a"})
        await tools["beta_search"].ainvoke({"query": "b"})

    assert session.calls == [("search", {"query": "a"}), ("search", {"query": "b"})]
    alpha, beta = (request["mcp_servers"][0] for request in _requests(mock_execute_request))
    assert alpha["id"] == mcp_server_id(url="https://alpha.example.com")
    assert beta["id"] == mcp_server_id(url="https://beta.example.com")
    assert alpha["tools"] == {"alpha_search": "search"}
    assert beta["tools"] == {"beta_search": "search"}
    calls = [request["messages"][-1]["tool_calls"][0] for request in _requests(mock_execute_request)]
    assert [call["mcp"]["server_id"] for call in calls] == [alpha["id"], beta["id"]]
    assert [call["function"]["name"] for call in calls] == ["alpha_search", "beta_search"]


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_session_only_server_is_identified_by_name(mock_execute_request, langchain):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    (search,) = await _load(FakeSession(), server_name="docs")

    await search.ainvoke({"query": "aiguard"})

    assert _requests(mock_execute_request)[0]["mcp_servers"] == [
        {
            "id": mcp_server_id(name="docs"),
            "managed_by": "client",
            "transport": "unknown",
            "tools": {"search": "search"},
            "name": "docs",
        }
    ]


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_session_without_server_metadata_is_an_ordinary_tool(mock_execute_request, langchain):
    """With nothing to identify the server, the tool is left untouched and not evaluated on its own."""
    session = FakeSession()
    (search,) = await _load(session)

    assert "found" in str(await search.ainvoke({"query": "aiguard"}))

    assert mock_execute_request.call_count == 0
    assert session.calls == [("search", {"query": "aiguard"})]


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_mcp_disabled_leaves_tools_untouched(mock_execute_request, langchain):
    session = FakeSession()
    with override_ai_guard_config(dict(_ai_guard_mcp_enabled=False)):
        (search,) = await _load(session, connection=_remote_connection(), server_name="docs")
        assert "found" in str(await search.ainvoke({"query": "aiguard"}))

    assert mock_execute_request.call_count == 0
    assert session.calls == [("search", {"query": "aiguard"})]


@pytest.mark.asyncio
async def test_metadata_stays_off_the_tool_fields(langchain):
    """The server rides on the coroutine only, so tool metadata and serialization are unchanged."""
    (search,) = await _load(FakeSession(), connection=_remote_connection(), server_name="docs")

    assert search.metadata is None
    assert "mcp.example.com" not in json.dumps(search.model_dump(exclude={"coroutine", "func"}), default=str)
    assert search.coroutine._dd_ai_guard_mcp[1] == "search"


requires_create_agent = pytest.mark.skipif(
    not hasattr(importlib.import_module("langchain.agents"), "create_agent"),
    reason="create_agent API introduced in langchain 1.0",
)


def _agent(tools: list[Any], replies: list[AIMessage]) -> Any:
    from langchain.agents import create_agent

    return create_agent(model=ScriptedChatModel(replies=replies), tools=tools)


@tool
def add(a: int, b: int) -> int:
    """Adds a and b."""
    return a + b


@requires_create_agent
@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True], ids=["ainvoke", "astream"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_create_agent_mcp_call_gets_one_mcp_aware_check(mock_execute_request, langchain, stream):
    """ToolNode evaluates the MCP call with the conversation and its server; the tool adds no second check."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    session = FakeSession()
    (search,) = await _load(session, connection=_remote_connection(), server_name="docs")
    agent = _agent([search, add], [_tool_call_reply("search", {"query": "aiguard"}), AIMessage(content="done")])
    inputs = {"messages": [HumanMessage(content="look it up")]}

    if stream:
        async for _ in agent.astream(inputs):
            pass
    else:
        await agent.ainvoke(inputs)

    assert session.calls == [("search", {"query": "aiguard"})]
    # prompt, model response (no MCP metadata), ToolNode MCP check, tool result, final answer
    assert mock_execute_request.call_count == 5
    (request,) = _mcp_requests(mock_execute_request)
    assert request["messages"][0] == {"role": "user", "content": "look it up"}
    tool_call = request["messages"][-1]["tool_calls"][0]
    assert tool_call["id"] == "call_1"
    assert tool_call["mcp"] == {"server_id": mcp_server_id(url=REMOTE_ORIGIN)}
    assert request["mcp_servers"][0]["tools"] == {"search": "search"}
    _assert_no_secrets(request)


@requires_create_agent
@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_create_agent_block_prevents_the_mcp_request(mock_execute_request, langchain, decision):
    def evaluate(*args: Any, **kwargs: Any):
        return mock_evaluate_response(decision if args[1]["data"]["attributes"].get("mcp_servers") else "ALLOW")

    mock_execute_request.side_effect = evaluate
    session = FakeSession()
    (search,) = await _load(session, connection=_remote_connection(), server_name="docs")
    agent = _agent([search], [_tool_call_reply("search", {"query": "drop"}), AIMessage(content="done")])

    with pytest.raises(AIGuardAbortError):
        await agent.ainvoke({"messages": [HumanMessage(content="look it up")]})

    assert session.calls == []
    assert len(_mcp_requests(mock_execute_request)) == 1


@requires_create_agent
@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_create_agent_ordinary_tool_has_no_mcp(mock_execute_request, langchain):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    session = FakeSession()
    (search,) = await _load(session, connection=_remote_connection(), server_name="docs")
    agent = _agent([search, add], [_tool_call_reply("add", {"a": 1, "b": 2}), AIMessage(content="3")])

    await agent.ainvoke({"messages": [HumanMessage(content="add")]})

    assert session.calls == []
    assert _mcp_requests(mock_execute_request) == []
    for request in _requests(mock_execute_request):
        for message in request["messages"]:
            assert all("mcp" not in call for call in message.get("tool_calls") or ())


@requires_create_agent
@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_create_agent_mcp_disabled(mock_execute_request, langchain):
    """With MCP off, ToolNode keeps today's behaviour: the call evaluated with the response is not re-checked."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    session = FakeSession()
    with override_ai_guard_config(dict(_ai_guard_mcp_enabled=False)):
        (search,) = await _load(session, connection=_remote_connection(), server_name="docs")
        agent = _agent([search], [_tool_call_reply("search", {"query": "aiguard"}), AIMessage(content="done")])
        await agent.ainvoke({"messages": [HumanMessage(content="look it up")]})

    assert session.calls == [("search", {"query": "aiguard"})]
    assert _mcp_requests(mock_execute_request) == []
    # prompt, response with the tool call, tool result, final answer
    assert mock_execute_request.call_count == 4


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_direct_call_of_a_renamed_tool_reports_the_new_name(mock_execute_request, langchain):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    (search,) = await _load(FakeSession(), connection=_remote_connection(), server_name="docs")
    search.name = "docs_search"

    await search.ainvoke({"query": "aiguard"})

    request = _requests(mock_execute_request)[0]
    assert request["messages"][0]["tool_calls"][0]["function"]["name"] == "docs_search"
    assert request["mcp_servers"][0]["tools"] == {"docs_search": "search"}


@requires_create_agent
@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_create_agent_copied_mcp_tool_gets_one_mcp_aware_check(mock_execute_request, langchain):
    """A copy shares the guarded coroutine, so ToolNode's check still exempts it from the in-tool check."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    session = FakeSession()
    (search,) = await _load(session, connection=_remote_connection(), server_name="docs")
    agent = _agent([search.model_copy()], [_tool_call_reply("search", {"query": "aiguard"}), AIMessage(content="done")])

    await agent.ainvoke({"messages": [HumanMessage(content="look it up")]})

    assert session.calls == [("search", {"query": "aiguard"})]
    assert mock_execute_request.call_count == 5
    (request,) = _mcp_requests(mock_execute_request)
    assert request["messages"][-1]["tool_calls"][0]["id"] == "call_1"


@requires_create_agent
@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_create_agent_parallel_mcp_calls_each_get_one_check(mock_execute_request, langchain):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    session = FakeSession()
    (search,) = await _load(session, connection=_remote_connection(), server_name="docs")
    reply = AIMessage(
        content="",
        tool_calls=[
            {"name": "search", "args": {"query": "a"}, "id": "call_a"},
            {"name": "search", "args": {"query": "b"}, "id": "call_b"},
        ],
    )
    agent = _agent([search], [reply, AIMessage(content="done")])

    await agent.ainvoke({"messages": [HumanMessage(content="look both up")]})

    assert sorted(session.calls, key=str) == [("search", {"query": "a"}), ("search", {"query": "b"})]
    checked = sorted(
        (call["id"], call["function"]["arguments"])
        for call in (request["messages"][-1]["tool_calls"][0] for request in _mcp_requests(mock_execute_request))
    )
    assert checked == [("call_a", '{"query": "a"}'), ("call_b", '{"query": "b"}')]


@requires_create_agent
@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_wrapper_tool_named_like_the_mcp_tool_keeps_the_inner_check(mock_execute_request, langchain):
    """ToolNode running a non-MCP tool of the same name does not exempt the MCP call it makes."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    session = FakeSession()
    (mcp_search,) = await _load(session, connection=_remote_connection(), server_name="docs")

    @tool("search")
    async def wrapper(query: str) -> str:
        """Search the docs through MCP."""
        return str(await mcp_search.ainvoke({"query": query}))

    agent = _agent([wrapper], [_tool_call_reply("search", {"query": "aiguard"}), AIMessage(content="done")])

    await agent.ainvoke({"messages": [HumanMessage(content="look it up")]})

    assert session.calls == [("search", {"query": "aiguard"})]
    (request,) = _mcp_requests(mock_execute_request)
    (message,) = request["messages"]
    assert message["tool_calls"][0]["mcp"] == {"server_id": mcp_server_id(url=REMOTE_ORIGIN)}


@pytest.mark.asyncio
@patch("langchain_openai.chat_models.ChatOpenAI._agenerate", autospec=True)
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_agent_executor_mcp_call_gets_the_in_tool_check(
    mock_execute_request, mock_agenerate, langchain_openai, openai_url
):
    """Legacy AgentExecutor: the response check, then the in-tool MCP check with no conversation context."""
    from langchain_classic.agents import AgentExecutor
    from langchain_classic.agents import create_openai_functions_agent
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.prompts import MessagesPlaceholder

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    function_call = {"name": "search", "arguments": json.dumps({"query": "aiguard"})}
    mock_agenerate.side_effect = [
        ChatResult(
            generations=[
                ChatGeneration(message=AIMessage(content="", additional_kwargs={"function_call": function_call}))
            ]
        ),
        ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))]),
    ]
    session = FakeSession()
    (search,) = await _load(session, connection=_remote_connection(), server_name="docs")
    prompt = ChatPromptTemplate.from_messages([("human", "{input}"), MessagesPlaceholder("agent_scratchpad")])
    llm = langchain_openai.ChatOpenAI(temperature=0, n=1, base_url=openai_url)
    executor = AgentExecutor(agent=create_openai_functions_agent(llm, [search], prompt), tools=[search])
    executor.agent.stream_runnable = False

    await executor.ainvoke({"input": "look it up"})

    assert session.calls == [("search", {"query": "aiguard"})]
    (request,) = _mcp_requests(mock_execute_request)
    (message,) = request["messages"]
    assert message["tool_calls"][0]["id"] != ""
    assert message["tool_calls"][0]["mcp"] == {"server_id": mcp_server_id(url=REMOTE_ORIGIN)}


def test_unpatch_restores_the_converter(langchain):
    from langchain_mcp_adapters import tools as adapter_tools

    from ddtrace.contrib.internal.langchain.patch import patch as langchain_patch
    from ddtrace.contrib.internal.langchain.patch import unpatch as langchain_unpatch

    assert hasattr(adapter_tools.convert_mcp_tool_to_langchain_tool, "__wrapped__")
    langchain_unpatch()
    try:
        assert not hasattr(adapter_tools.convert_mcp_tool_to_langchain_tool, "__wrapped__")
        converted = adapter_tools.convert_mcp_tool_to_langchain_tool(FakeSession(), SEARCH, server_name="docs")
        assert not hasattr(converted.coroutine, "_dd_ai_guard_mcp")
        # The import hook is gone too: a fresh import of the module is not wrapped.
        package = sys.modules["langchain_mcp_adapters"]
        del sys.modules["langchain_mcp_adapters.tools"]
        try:
            fresh = importlib.import_module("langchain_mcp_adapters.tools")
            assert fresh is not adapter_tools
            assert not hasattr(fresh.convert_mcp_tool_to_langchain_tool, "__wrapped__")
        finally:
            sys.modules["langchain_mcp_adapters.tools"] = adapter_tools
            package.tools = adapter_tools
    finally:
        langchain_patch()
