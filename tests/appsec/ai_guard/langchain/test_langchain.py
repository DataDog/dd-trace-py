import json
from typing import Any
from unittest.mock import patch

import langchain
import langchain_core
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.messages import FunctionMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from langchain_core.messages import ToolCall
from langchain_core.messages import ToolMessage
from langchain_core.outputs.chat_result import ChatGeneration
from langchain_core.outputs.chat_result import ChatResult
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.prompts import MessagesPlaceholder
import pytest

from ddtrace.appsec._ai_guard._langchain import _convert_messages
from ddtrace.appsec.ai_guard import AIGuardAbortError
from ddtrace.internal.utils.version import parse_version
from tests.appsec.ai_guard.utils import mock_evaluate_response
from tests.appsec.ai_guard.utils import override_ai_guard_config


LANGCHAIN_VERSION = parse_version(langchain.__version__)

# langchain 1.0 removed the legacy ``AgentExecutor`` / ``create_openai_functions_agent``
# API in favor of ``create_agent`` (langgraph-based). Tests are split accordingly.
requires_legacy_agents = pytest.mark.skipif(
    LANGCHAIN_VERSION >= (1, 0, 0),
    reason="legacy AgentExecutor / create_openai_functions_agent API removed in langchain 1.0",
)
requires_create_agent = pytest.mark.skipif(
    LANGCHAIN_VERSION < (1, 0, 0),
    reason="create_agent API introduced in langchain 1.0",
)


class ToolTrackingHandler(BaseCallbackHandler):
    def __init__(self):
        self.tool_calls = []

    def on_tool_start(self, serialized, input_str, **kwargs):
        self.tool_calls.append(serialized["name"])


def _mock_openai_tool_response(tool: str, args: Any) -> ChatResult:
    return ChatResult(
        generations=[
            ChatGeneration(
                message=AIMessage(
                    content="", additional_kwargs={"function_call": {"name": tool, "arguments": json.dumps(args)}}
                ),
                generation_info={"finish_reason": "function_call"},
            )
        ]
    )


def _mock_openai_tool_call_response(tool: str, args: Any) -> ChatResult:
    """Tool-call response in the langchain >= 1.0 ``tool_calls`` format.

    ``create_agent`` routes to the tool node based on ``AIMessage.tool_calls``
    (the legacy ``function_call`` additional kwarg is no longer used).
    """
    return ChatResult(
        generations=[
            ChatGeneration(
                message=AIMessage(content="", tool_calls=[ToolCall(id="call_1", name=tool, args=args)]),
                generation_info={"finish_reason": "tool_calls"},
            )
        ]
    )


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_openai_chat_sync_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    chat.invoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_openai_chat_async_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    await chat.ainvoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    mock_execute_request.assert_called_once()


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_openai_chat_sync_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    # The prompt should be blocked for both DENY and ABORT
    with pytest.raises(AIGuardAbortError):
        chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
        chat.invoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_openai_chat_async_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    # The prompt should be blocked for both DENY and ABORT
    with pytest.raises(AIGuardAbortError):
        chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
        await chat.ainvoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    mock_execute_request.assert_called_once()


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_openai_chat_sync_block_config_disabled(mock_execute_request, langchain_openai, openai_url, decision):
    """When _ai_guard_block=False (DD_AI_GUARD_BLOCK=false), DENY/ABORT should NOT raise AIGuardAbortError
    even when the server response has is_blocking_enabled=True.
    """
    mock_execute_request.return_value = mock_evaluate_response(decision, block=True)

    with override_ai_guard_config(dict(_ai_guard_block=False)):
        chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
        # Should NOT raise because local config passes Options(block=False) which overrides server response
        chat.invoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])
        mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_openai_chat_async_block_config_disabled(mock_execute_request, langchain_openai, openai_url, decision):
    """When _ai_guard_block=False (DD_AI_GUARD_BLOCK=false), DENY/ABORT should NOT raise AIGuardAbortError
    even when the server response has is_blocking_enabled=True.
    """
    mock_execute_request.return_value = mock_evaluate_response(decision, block=True)

    with override_ai_guard_config(dict(_ai_guard_block=False)):
        chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
        await chat.ainvoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])
        mock_execute_request.assert_called_once()


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_openai_llm_sync_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    llm = langchain_openai.OpenAI(base_url=openai_url)
    llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_openai_llm_async_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    llm = langchain_openai.OpenAI(base_url=openai_url)
    await llm.ainvoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    mock_execute_request.assert_called_once()


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_openai_llm_sync_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    # The prompt should be blocked for both DENY and ABORT
    with pytest.raises(AIGuardAbortError):
        llm = langchain_openai.OpenAI(base_url=openai_url)
        llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_openai_llm_async_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    # The prompt should be blocked for both DENY and ABORT
    with pytest.raises(AIGuardAbortError):
        llm = langchain_openai.OpenAI(base_url=openai_url)
        await llm.ainvoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    mock_execute_request.assert_called_once()


@requires_legacy_agents
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_agent_action_sync_block(mock_execute_request, langchain_openai, openai_url, decision):
    from langchain.agents import AgentExecutor
    from langchain.agents import create_openai_functions_agent

    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),  # Allow the initial prompt
        mock_evaluate_response(decision),  # Deny/abort the tool call
    ]

    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.

        Args:
            a: first int
            b: second int
        """
        return a + b

    tools = [add]
    agent_prompt = ChatPromptTemplate.from_messages([("human", "{input}"), MessagesPlaceholder("agent_scratchpad")])
    llm = langchain_openai.ChatOpenAI(temperature=0, n=1, base_url=openai_url)
    agent = create_openai_functions_agent(llm, tools, agent_prompt)

    agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False, return_intermediate_steps=False)
    agent_executor.agent.stream_runnable = False

    with pytest.raises(AIGuardAbortError):
        agent_executor.invoke({"input": "1 + 1"})

    assert mock_execute_request.call_count == 2  # One for prompt, one for tool


@requires_legacy_agents
@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_agent_action_async_block(mock_execute_request, langchain_openai, openai_url, decision):
    from langchain.agents import AgentExecutor
    from langchain.agents import create_openai_functions_agent

    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),  # Allow the initial prompt
        mock_evaluate_response(decision),  # Deny/abort the tool call
    ]

    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.

        Args:
            a: first int
            b: second int
        """
        return a + b

    tools = [add]
    agent_prompt = ChatPromptTemplate.from_messages([("human", "{input}"), MessagesPlaceholder("agent_scratchpad")])
    llm = langchain_openai.ChatOpenAI(temperature=0, n=1, base_url=openai_url)
    agent = create_openai_functions_agent(llm, tools, agent_prompt)

    agent_executor = AgentExecutor(agent=agent, tools=tools)
    agent_executor.agent.stream_runnable = False

    with pytest.raises(AIGuardAbortError):
        await agent_executor.ainvoke({"input": "1 + 1"})

    assert mock_execute_request.call_count == 2  # One for prompt, one for tool


@requires_legacy_agents
@pytest.mark.asyncio
@patch("langchain_openai.chat_models.ChatOpenAI._agenerate", autospec=True)
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_agent_action_intermediate_steps(mock_execute_request, mock_openai_request, langchain_openai, openai_url):
    from langchain.agents import AgentExecutor
    from langchain.agents import create_openai_functions_agent

    def ai_guard_mock(*args, **kwargs):
        messages = args[1]["data"]["attributes"]["messages"]
        last_message = messages[-1]

        # Initial prompt: ALLOW
        if last_message.get("role", None) == "user":
            return mock_evaluate_response("ALLOW")

        # First tool call: ALLOW
        tool_call = last_message.get("tool_calls", [])[0]
        if tool_call["function"]["name"] == "random":
            return mock_evaluate_response("ALLOW")

        # Second tool call: DENY
        assert tool_call["function"]["name"] == "square_root"
        return mock_evaluate_response("DENY")

    mock_execute_request.side_effect = ai_guard_mock

    async def open_ai_mock(*args, **kwargs):
        messages = args[1]
        last_message = messages[-1]
        if isinstance(last_message, HumanMessage):
            assert last_message.content == "Generate a random number between 0 and 100, then calculate its square root"
            return _mock_openai_tool_response("random", {"start": 0, "end": 100})

        assert isinstance(last_message, FunctionMessage)
        random_number = last_message.content
        return _mock_openai_tool_response("square_root", {"value": random_number})

    mock_openai_request.side_effect = open_ai_mock

    @langchain_core.tools.tool
    def random(start: int, end: int) -> int:
        """Generate a random number

        Args:
            start: min value
            end: max value
        """
        import random as rand

        return rand.randint(start, end)

    @langchain_core.tools.tool
    def square_root(value: float) -> float:
        """Computes the square root of a given number

        Args:
            value: value to compute the square root
        """
        import math

        return math.sqrt(value)

    tools = [random, square_root]
    agent_prompt = ChatPromptTemplate.from_messages([("human", "{input}"), MessagesPlaceholder("agent_scratchpad")])
    llm = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    agent = create_openai_functions_agent(llm, tools, agent_prompt)

    agent_executor = AgentExecutor(agent=agent, tools=tools)
    agent_executor.agent.stream_runnable = False
    tool_tracker = ToolTrackingHandler()

    with pytest.raises(AIGuardAbortError):
        await agent_executor.ainvoke(
            {"input": "Generate a random number between 0 and 100, then calculate its square root"},
            config={"callbacks": [tool_tracker]},
        )

    assert mock_execute_request.call_count == 3  # One for prompt, one for each tool
    assert tool_tracker.tool_calls == ["random"]  # Only the random tool was called


# ---------------------------------------------------------------------------
# langchain >= 1.0 agents (``create_agent``)
#
# The legacy ``AgentExecutor`` / ``create_openai_functions_agent`` API was
# removed in langchain 1.0. Agents are now built with ``create_agent`` and
# client-side tools are executed by langgraph's ``ToolNode``. These tests pin
# the same tool-call blocking behavior as the legacy agent tests above, going
# through the new ``ToolNode._run_one`` / ``_arun_one`` AI Guard hooks.
# ---------------------------------------------------------------------------


@requires_create_agent
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("langchain_openai.chat_models.ChatOpenAI._generate", autospec=True)
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_create_agent_action_sync_block(
    mock_execute_request, mock_openai_request, langchain_openai, openai_url, decision
):
    from langchain.agents import create_agent

    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),  # Allow the initial prompt
        mock_evaluate_response(decision),  # Deny/abort the tool call
    ]

    def open_ai_mock(self, messages, *args, **kwargs):
        return _mock_openai_tool_call_response("add", {"a": 1, "b": 1})

    mock_openai_request.side_effect = open_ai_mock

    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.

        Args:
            a: first int
            b: second int
        """
        return a + b

    llm = langchain_openai.ChatOpenAI(temperature=0, n=1, base_url=openai_url)
    agent = create_agent(model=llm, tools=[add])

    with pytest.raises(AIGuardAbortError):
        agent.invoke({"messages": [HumanMessage(content="1 + 1")]})

    assert mock_execute_request.call_count == 2  # One for prompt, one for tool


@requires_create_agent
@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("langchain_openai.chat_models.ChatOpenAI._agenerate", autospec=True)
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_create_agent_action_async_block(
    mock_execute_request, mock_openai_request, langchain_openai, openai_url, decision
):
    from langchain.agents import create_agent

    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),  # Allow the initial prompt
        mock_evaluate_response(decision),  # Deny/abort the tool call
    ]

    async def open_ai_mock(self, messages, *args, **kwargs):
        return _mock_openai_tool_call_response("add", {"a": 1, "b": 1})

    mock_openai_request.side_effect = open_ai_mock

    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.

        Args:
            a: first int
            b: second int
        """
        return a + b

    llm = langchain_openai.ChatOpenAI(temperature=0, n=1, base_url=openai_url)
    agent = create_agent(model=llm, tools=[add])

    with pytest.raises(AIGuardAbortError):
        await agent.ainvoke({"messages": [HumanMessage(content="1 + 1")]})

    assert mock_execute_request.call_count == 2  # One for prompt, one for tool


@requires_create_agent
@patch("langchain_openai.chat_models.ChatOpenAI._generate", autospec=True)
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_create_agent_action_sync_allow(mock_execute_request, mock_openai_request, langchain_openai, openai_url):
    """An allowed tool call executes and the agent completes normally."""
    from langchain.agents import create_agent

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    responses = [
        _mock_openai_tool_call_response("add", {"a": 1, "b": 1}),
        ChatResult(generations=[ChatGeneration(message=AIMessage(content="The answer is 2"))]),
    ]
    call_index = {"i": 0}

    def open_ai_mock(self, messages, *args, **kwargs):
        i = min(call_index["i"], len(responses) - 1)
        call_index["i"] += 1
        return responses[i]

    mock_openai_request.side_effect = open_ai_mock

    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.

        Args:
            a: first int
            b: second int
        """
        return a + b

    llm = langchain_openai.ChatOpenAI(temperature=0, n=1, base_url=openai_url)
    agent = create_agent(model=llm, tools=[add])

    result = agent.invoke({"messages": [HumanMessage(content="1 + 1")]})

    # Three evaluations across the agent loop:
    #   1. before model (user prompt)
    #   2. before tool (the ``add`` tool call)
    #   3. before the second model turn, whose trailing message is the tool
    #      result -> AI Guard evaluates the tool output in context.
    assert mock_execute_request.call_count == 3
    assert any(getattr(m, "content", None) == "The answer is 2" for m in result["messages"])

    # The third evaluation must carry the tool result (role="tool") so AI Guard
    # sees the tool output, not just the original prompt.
    tool_result_eval_messages = mock_execute_request.call_args_list[2][0][1]["data"]["attributes"]["messages"]
    assert any(m.get("role") == "tool" and m.get("content") == "2" for m in tool_result_eval_messages)


@requires_create_agent
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("langchain_openai.chat_models.ChatOpenAI._generate", autospec=True)
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_create_agent_blocks_on_tool_result(
    mock_execute_request, mock_openai_request, langchain_openai, openai_url, decision
):
    """A tool result is evaluated at the next 'before model' step and can be blocked.

    Pins the gap surfaced in real traces: after a tool runs, the following model
    turn (whose trailing message is the tool result) must reach AI Guard.
    """
    from langchain.agents import create_agent

    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),  # before model: user prompt
        mock_evaluate_response("ALLOW"),  # before tool: the add tool call
        mock_evaluate_response(decision),  # before next model: the tool result
    ]

    def open_ai_mock(self, messages, *args, **kwargs):
        return _mock_openai_tool_call_response("add", {"a": 1, "b": 1})

    mock_openai_request.side_effect = open_ai_mock

    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.

        Args:
            a: first int
            b: second int
        """
        return a + b

    llm = langchain_openai.ChatOpenAI(temperature=0, n=1, base_url=openai_url)
    agent = create_agent(model=llm, tools=[add])

    with pytest.raises(AIGuardAbortError):
        agent.invoke({"messages": [HumanMessage(content="1 + 1")]})

    # prompt + tool call + tool result (the blocking evaluation)
    assert mock_execute_request.call_count == 3


def test_message_conversion():
    messages = [
        SystemMessage(content="You are a beautiful assistant"),
        HumanMessage(content="What day is today?"),
        AIMessage(
            content="",
            additional_kwargs={"function_call": {"name": "calendar_check", "arguments": '{"expression": "today"}'}},
        ),
        FunctionMessage(name="calendar_check", content="Today is Monday"),
        HumanMessage(content="One plus one?"),
        AIMessage(content="", tool_calls=[ToolCall(id="tool_call_1", name="add", args={"a": 1, "b": 1})]),
        ToolMessage(tool_call_id="tool_call_1", content="2"),
        AIMessage(role="assistant", content="One plus one is two"),
    ]
    result = _convert_messages(messages)
    assert len(result) == 8

    assert result[0]["role"] == "system"
    assert result[0]["content"] == "You are a beautiful assistant"

    assert result[1]["role"] == "user"
    assert result[1]["content"] == "What day is today?"

    assert result[2]["role"] == "assistant"
    assert len(result[2]["tool_calls"]) == 1
    assert result[2]["tool_calls"][0]["id"] == ""
    assert result[2]["tool_calls"][0]["function"]["name"] == "calendar_check"
    assert result[2]["tool_calls"][0]["function"]["arguments"] == '{"expression": "today"}'

    assert result[3]["role"] == "tool"
    assert result[3]["tool_call_id"] == ""
    assert result[3]["content"] == "Today is Monday"

    assert result[4]["role"] == "user"
    assert result[4]["content"] == "One plus one?"

    assert result[5]["role"] == "assistant"
    assert len(result[5]["tool_calls"]) == 1
    assert result[5]["tool_calls"][0]["id"] == "tool_call_1"
    assert result[5]["tool_calls"][0]["function"]["name"] == "add"
    assert result[5]["tool_calls"][0]["function"]["arguments"] == '{"a": 1, "b": 1}'

    assert result[6]["role"] == "tool"
    assert result[6]["tool_call_id"] == "tool_call_1"
    assert result[6]["content"] == "2"

    assert result[7]["role"] == "assistant"
    assert result[7]["content"] == "One plus one is two"


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_sync_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    for _ in model.stream(input="how can langsmith help with testing?"):
        pass

    mock_execute_request.assert_called_once()


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_sync_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    with pytest.raises(AIGuardAbortError):
        for _ in model.stream(input="how can langsmith help with testing?"):
            pass

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_streamed_chat_async_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    async for _ in model.astream(input="how can langsmith help with testing?"):
        pass

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_streamed_chat_async_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    with pytest.raises(AIGuardAbortError):
        async for _ in model.astream(input="how can langsmith help with testing?"):
            pass

    mock_execute_request.assert_called_once()


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_streamed_llm_sync_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    llm = langchain_openai.OpenAI(base_url=openai_url)

    for _ in llm.stream(input="How do I write technical documentation?"):
        pass

    mock_execute_request.assert_called_once()


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_streamed_llm_sync_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    llm = langchain_openai.OpenAI(base_url=openai_url)

    with pytest.raises(AIGuardAbortError):
        for _ in llm.stream(input="How do I write technical documentation?"):
            pass

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_streamed_llm_async_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    llm = langchain_openai.OpenAI(base_url=openai_url)

    async for _ in llm.astream(input="How do I write technical documentation?"):
        pass

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_streamed_llm_async_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    llm = langchain_openai.OpenAI(base_url=openai_url)

    with pytest.raises(AIGuardAbortError):
        async for _ in llm.astream(input="How do I write technical documentation?"):
            pass

    mock_execute_request.assert_called_once()


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_resets_context_after_block(mock_execute_request, langchain_openai, openai_url):
    """A blocked non-streaming chat call still releases the active counter.

    The ``.generate.before`` listener bumps the counter *before* evaluating
    (so it remains active during the underlying call), and the contrib's
    ``finally`` block dispatches ``.generate.finally`` which resets it on
    every exit path — including a block where the dispatch raises out of
    ``.before``.
    """
    from ddtrace.appsec._ai_guard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("DENY")
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)

    assert is_aiguard_context_active() is False
    with pytest.raises(AIGuardAbortError):
        chat.invoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])
    assert is_aiguard_context_active() is False


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_resets_context_after_block(mock_execute_request, langchain_openai, openai_url):
    """Async variant of :func:`test_chat_resets_context_after_block`."""
    from ddtrace.appsec._ai_guard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("DENY")
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)

    assert is_aiguard_context_active() is False
    with pytest.raises(AIGuardAbortError):
        await chat.ainvoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])
    assert is_aiguard_context_active() is False


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_resets_context_after_success(mock_execute_request, langchain_openai, openai_url):
    """After a successful sync chat stream, the AI Guard active counter is back at zero."""
    from ddtrace.appsec._ai_guard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    assert is_aiguard_context_active() is False
    for _ in model.stream(input="how can langsmith help with testing?"):
        pass
    assert is_aiguard_context_active() is False


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_streamed_chat_resets_context_after_async_success(mock_execute_request, langchain_openai, openai_url):
    """After a successful async chat stream, the AI Guard active counter is back at zero."""
    from ddtrace.appsec._ai_guard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    assert is_aiguard_context_active() is False
    async for _ in model.astream(input="how can langsmith help with testing?"):
        pass
    assert is_aiguard_context_active() is False


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_resets_context_after_block(mock_execute_request, langchain_openai, openai_url):
    """A blocked chat stream still releases the active counter (paired in the
    contrib's ``finally`` path / ``except`` path of ``shared_stream``).
    """
    from ddtrace.appsec._ai_guard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("DENY")
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    assert is_aiguard_context_active() is False
    with pytest.raises(AIGuardAbortError):
        for _ in model.stream(input="how can langsmith help with testing?"):
            pass
    assert is_aiguard_context_active() is False


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_streamed_llm_resets_context_after_success(mock_execute_request, langchain_openai, openai_url):
    from ddtrace.appsec._ai_guard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    llm = langchain_openai.OpenAI(base_url=openai_url)

    assert is_aiguard_context_active() is False
    for _ in llm.stream(input="How do I write technical documentation?"):
        pass
    assert is_aiguard_context_active() is False


# AIDEV-NOTE: ``filterwarnings`` suppresses an orthogonal pre-existing
# span-lifecycle warning: when a langchain stream is created but never
# iterated, ``shared_stream`` has already started the LLMObs span via
# ``integration.trace(...)`` but ``TracedStream.__iter__``'s ``finally``
# (which runs ``finalize_stream``) never executes, so the span is left
# open and the test runner's "Context was not cleared after test" warning
# fires. That span leak is a separate base stream-handler concern. These
# tests intentionally pin the *counter* contract: an unconsumed stream
# must not leave the AI Guard active-context counter incremented,
# regardless of whether the span itself is finalized.
@pytest.mark.filterwarnings("ignore:Context was not cleared after test:UserWarning")
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_unconsumed_stream_does_not_leak_context(mock_execute_request, langchain_openai, openai_url):
    """Creating a langchain stream and never iterating it must NOT leave the
    AI Guard active-context counter incremented. Otherwise a subsequent
    direct OpenAI call in the same task would see
    ``is_aiguard_context_active()`` return ``True`` and silently skip AI
    Guard evaluation (codex P2 finding on PR #17913). Counter is now bumped
    lazily by the ``.stream.started`` listener fired from the
    iteration-scoped generator wrapper in ``shared_stream`` — never running
    when the caller doesn't iterate.
    """
    from ddtrace.appsec._ai_guard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    assert is_aiguard_context_active() is False
    stream = model.stream(input="how can langsmith help with testing?")
    assert is_aiguard_context_active() is False
    stream.close()
    assert is_aiguard_context_active() is False


@pytest.mark.filterwarnings("ignore:Context was not cleared after test:UserWarning")
@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_streamed_chat_unconsumed_async_stream_does_not_leak_context(
    mock_execute_request, langchain_openai, openai_url
):
    """Async variant — see sync test for rationale."""
    from ddtrace.appsec._ai_guard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    assert is_aiguard_context_active() is False
    stream = model.astream(input="how can langsmith help with testing?")
    assert is_aiguard_context_active() is False
    await stream.aclose()
    assert is_aiguard_context_active() is False


# ---------------------------------------------------------------------------
# Span observability on AI Guard block
#
# Both non-streaming (``traced_llm_generate`` / ``traced_chat_model_generate``)
# and streaming (``shared_stream``) wrappers create the LLMObs span *before*
# dispatching the AI Guard before-hook. On block, the AIGuardAbortError is
# captured on the LLM span via ``set_exc_info`` and the span is finished
# normally — alongside the AI Guard span produced by ``client.evaluate``.
# These tests pin that contract so a regression to "AI Guard span only,
# no LLM span" surfaces immediately.
# ---------------------------------------------------------------------------


def _assert_langchain_block_spans(test_spans, decision):
    from ddtrace.appsec._constants import AI_GUARD

    spans = test_spans.spans
    ai_guard_span = next((s for s in spans if s.name == AI_GUARD.RESOURCE_TYPE), None)
    assert ai_guard_span is not None, f"AI Guard span not found in {[s.name for s in spans]}"
    assert ai_guard_span.get_tag(AI_GUARD.ACTION_TAG) == decision
    assert ai_guard_span.get_tag(AI_GUARD.BLOCKED_TAG) == "true"

    llm_span = next((s for s in spans if s.name != AI_GUARD.RESOURCE_TYPE), None)
    assert llm_span is not None, f"LangChain LLM span not found in {[s.name for s in spans]}"
    assert llm_span.error == 1, "LangChain LLM span should have error=1 after AI Guard block"
    assert "AIGuardAbortError" in (llm_span.get_tag("error.type") or ""), (
        f"LangChain span error.type should reference AIGuardAbortError, got: {llm_span.get_tag('error.type')!r}"
    )


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_block_emits_ai_guard_and_llm_spans(
    mock_execute_request, langchain_openai, openai_url, test_spans, decision
):
    """Non-streaming sync chat: both spans on block."""
    mock_execute_request.return_value = mock_evaluate_response(decision)

    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    with pytest.raises(AIGuardAbortError):
        chat.invoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    _assert_langchain_block_spans(test_spans, decision)


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_block_emits_ai_guard_and_llm_spans(
    mock_execute_request, langchain_openai, openai_url, test_spans, decision
):
    """Non-streaming async chat: both spans on block."""
    mock_execute_request.return_value = mock_evaluate_response(decision)

    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    with pytest.raises(AIGuardAbortError):
        await chat.ainvoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    _assert_langchain_block_spans(test_spans, decision)


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_block_emits_ai_guard_and_llm_spans(
    mock_execute_request, langchain_openai, openai_url, test_spans, decision
):
    """Streaming sync chat: both spans on block. The LangChain LLMObs span is
    created in ``shared_stream`` *before* the AI Guard dispatch; the AI Guard
    abort flows through the existing ``except Exception`` arm and finishes
    the span with ``set_exc_info``.
    """
    mock_execute_request.return_value = mock_evaluate_response(decision)

    model = langchain_openai.ChatOpenAI(base_url=openai_url)
    with pytest.raises(AIGuardAbortError):
        for _ in model.stream(input="how can langsmith help with testing?"):
            pass

    _assert_langchain_block_spans(test_spans, decision)


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_streamed_llm_block_emits_ai_guard_and_llm_spans(
    mock_execute_request, langchain_openai, openai_url, test_spans, decision
):
    """Streaming sync llm: both spans on block (mirrors ``traced_llm_stream``)."""
    mock_execute_request.return_value = mock_evaluate_response(decision)

    llm = langchain_openai.OpenAI(base_url=openai_url)
    with pytest.raises(AIGuardAbortError):
        for _ in llm.stream(input="How do I write technical documentation?"):
            pass

    _assert_langchain_block_spans(test_spans, decision)
