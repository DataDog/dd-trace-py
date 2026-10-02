import json
from typing import Any
from unittest.mock import patch

import langchain
import langchain_core
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.messages import AIMessageChunk
from langchain_core.messages import FunctionMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from langchain_core.messages import ToolCall
from langchain_core.messages import ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.outputs.chat_result import ChatGeneration
from langchain_core.outputs.chat_result import ChatResult
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.prompts import MessagesPlaceholder
from langchain_core.tools import tool
import pytest

from ddtrace.aiguard import AIGuardAbortError
from ddtrace.aiguard.integrations._langchain import _convert_messages
from ddtrace.internal.utils.version import parse_version
from tests.aiguard.utils import mock_evaluate_response
from tests.aiguard.utils import override_ai_guard_config


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
requires_astream_events_v2 = pytest.mark.skipif(
    parse_version(langchain_core.__version__) < (0, 2, 0),
    reason="astream_events(version='v2') introduced in langchain-core 0.2",
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


def _evaluated_messages(mock_execute_request, index: int) -> list:
    """Messages sent to AI Guard by the index-th evaluate call."""
    return mock_execute_request.call_args_list[index][0][1]["data"]["attributes"]["messages"]


def _assert_evaluated_response(mock_execute_request, content: str) -> None:
    """Assert the second evaluation carried the model's answer back to AI Guard.

    A LangChain call makes two evaluations: the request, then request + response.
    """
    assert mock_execute_request.call_count == 2
    response_eval = _evaluated_messages(mock_execute_request, 1)
    assert response_eval[-1]["role"] == "assistant"
    assert content in response_eval[-1]["content"]


# Tool-call prompt whose recorded responses (tests/cassettes/openai) ask for add(a=1, b=1).
TOOL_PROMPT = "What is 1 + 1? Use the add tool."
TEXT_AND_TOOL_PROMPT = "Say what you are about to do, then add 1 and 1 with the add tool."


@tool
def add(a: int, b: int) -> int:
    """Adds a and b.

    Args:
        a: first int
        b: second int
    """
    return a + b


def _tool_call_evaluations(mock_execute_request) -> list:
    """The function of every evaluation whose trailing message is a tool call, in call order."""
    functions = []
    for index in range(mock_execute_request.call_count):
        trailing = _evaluated_messages(mock_execute_request, index)[-1]
        if trailing.get("tool_calls"):
            functions.append(trailing["tool_calls"][0]["function"])
    return functions


def _ai_guard_spans(test_spans) -> list:
    from ddtrace.aiguard._constants import AI_GUARD

    return [s for s in test_spans.spans if s.name == AI_GUARD.RESOURCE_TYPE]


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_openai_chat_sync_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    chat.invoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    _assert_evaluated_response(mock_execute_request, "'Whom' is used as the object of a verb")


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_openai_chat_async_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    await chat.ainvoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    _assert_evaluated_response(mock_execute_request, "'Whom' is used as the object of a verb")


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_openai_chat_sync_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    # The prompt should be blocked for both DENY and ABORT
    with pytest.raises(AIGuardAbortError):
        chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
        chat.invoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_openai_chat_async_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    # The prompt should be blocked for both DENY and ABORT
    with pytest.raises(AIGuardAbortError):
        chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
        await chat.ainvoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    mock_execute_request.assert_called_once()


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_openai_chat_sync_block_config_disabled(mock_execute_request, langchain_openai, openai_url, decision):
    """When _ai_guard_block=False (DD_AI_GUARD_BLOCK=false), DENY/ABORT should NOT raise AIGuardAbortError
    even when the server response has is_blocking_enabled=True.
    """
    mock_execute_request.return_value = mock_evaluate_response(decision, block=True)

    with override_ai_guard_config(dict(_ai_guard_block=False)):
        chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
        # Should NOT raise because local config passes Options(block=False) which overrides server response
        chat.invoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])
        # Both the request and the response evaluation run: neither can block.
        assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_openai_chat_async_block_config_disabled(mock_execute_request, langchain_openai, openai_url, decision):
    """When _ai_guard_block=False (DD_AI_GUARD_BLOCK=false), DENY/ABORT should NOT raise AIGuardAbortError
    even when the server response has is_blocking_enabled=True.
    """
    mock_execute_request.return_value = mock_evaluate_response(decision, block=True)

    with override_ai_guard_config(dict(_ai_guard_block=False)):
        chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
        await chat.ainvoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])
        # Both the request and the response evaluation run: neither can block.
        assert mock_execute_request.call_count == 2


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_openai_llm_sync_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    llm = langchain_openai.OpenAI(base_url=openai_url)
    llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    _assert_evaluated_response(mock_execute_request, "Cogito, ergo sum")


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_openai_llm_async_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    llm = langchain_openai.OpenAI(base_url=openai_url)
    await llm.ainvoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    _assert_evaluated_response(mock_execute_request, "Cogito, ergo sum")


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_openai_llm_sync_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    # The prompt should be blocked for both DENY and ABORT
    with pytest.raises(AIGuardAbortError):
        llm = langchain_openai.OpenAI(base_url=openai_url)
        llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_openai_llm_async_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    # The prompt should be blocked for both DENY and ABORT
    with pytest.raises(AIGuardAbortError):
        llm = langchain_openai.OpenAI(base_url=openai_url)
        await llm.ainvoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    mock_execute_request.assert_called_once()


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_openai_chat_sync_response_block(mock_execute_request, langchain_openai, openai_url, decision):
    """An allowed prompt whose response is blocked aborts rather than returning the answer."""
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response(decision)]

    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    with pytest.raises(AIGuardAbortError):
        chat.invoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_openai_chat_async_response_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response(decision)]

    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    with pytest.raises(AIGuardAbortError):
        await chat.ainvoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    assert mock_execute_request.call_count == 2


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_openai_llm_sync_response_block(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response("DENY")]

    llm = langchain_openai.OpenAI(base_url=openai_url)
    with pytest.raises(AIGuardAbortError):
        llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    assert mock_execute_request.call_count == 2


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_chat_response_evaluation_carries_request_context(mock_execute_request, langchain_openai, openai_url):
    """The response evaluation sends the prompt alongside the answer, not the answer alone."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    chat.invoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    messages = _evaluated_messages(mock_execute_request, 1)
    assert messages[0] == {"role": "user", "content": "When do you use 'whom' instead of 'who'?"}
    assert messages[-1]["role"] == "assistant"


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_bound_tool_call_response_block(mock_execute_request, langchain_openai, openai_url, decision):
    """With bind_tools and no agent, the response evaluation is the only check on the tool call."""
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response(decision)]

    llm = langchain_openai.ChatOpenAI(temperature=0, n=1, base_url=openai_url).bind_tools([add])
    with pytest.raises(AIGuardAbortError):
        llm.invoke([HumanMessage(content=TOOL_PROMPT)])

    assert _tool_call_evaluations(mock_execute_request) == [{"name": "add", "arguments": '{"a": 1, "b": 1}'}]


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_bound_tool_call_response_allow(mock_execute_request, langchain_openai, openai_url, test_spans):
    from ddtrace.aiguard._constants import AI_GUARD

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    llm = langchain_openai.ChatOpenAI(temperature=0, n=1, base_url=openai_url).bind_tools([add])
    result = llm.invoke([HumanMessage(content=TOOL_PROMPT)])

    assert result.tool_calls[0]["name"] == "add"
    assert mock_execute_request.call_count == 2
    response_span = _ai_guard_spans(test_spans)[-1]
    assert response_span.get_tag(AI_GUARD.TARGET_TAG) == "tool"
    assert response_span.get_tag(AI_GUARD.TOOL_NAME_TAG) == "add"


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_text_and_tool_call_response_evaluated_as_one_turn(
    mock_execute_request, langchain_openai, openai_url, test_spans
):
    """A reply carrying text and a tool call is evaluated as a single assistant turn targeting the tool."""
    from ddtrace.aiguard._constants import AI_GUARD

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    llm = langchain_openai.ChatOpenAI(temperature=0, n=1, base_url=openai_url).bind_tools([add])
    llm.invoke([HumanMessage(content=TEXT_AND_TOOL_PROMPT)])

    response = _evaluated_messages(mock_execute_request, 1)
    assert response[0] == {"role": "user", "content": TEXT_AND_TOOL_PROMPT}
    assert response[-1]["role"] == "assistant"
    assert response[-1]["content"] == "I will add 1 and 1 using the add tool."
    assert response[-1]["tool_calls"][0]["function"] == {"name": "add", "arguments": '{"a": 1, "b": 1}'}
    response_span = _ai_guard_spans(test_spans)[-1]
    assert response_span.get_tag(AI_GUARD.TARGET_TAG) == "tool"
    assert response_span.get_tag(AI_GUARD.TOOL_NAME_TAG) == "add"


@requires_legacy_agents
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_agent_action_evaluated_once(mock_execute_request, langchain_openai, openai_url):
    """The legacy function_call path: Agent.plan does not repeat the response evaluation of the same call."""
    from langchain.agents import AgentExecutor
    from langchain.agents import create_openai_functions_agent

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    agent_prompt = ChatPromptTemplate.from_messages([("human", "{input}"), MessagesPlaceholder("agent_scratchpad")])
    llm = langchain_openai.ChatOpenAI(temperature=0, n=1, base_url=openai_url)
    agent_executor = AgentExecutor(agent=create_openai_functions_agent(llm, [add], agent_prompt), tools=[add])
    agent_executor.agent.stream_runnable = False

    result = agent_executor.invoke({"input": "1 + 1"})

    assert [f["name"] for f in _tool_call_evaluations(mock_execute_request)] == ["add"]
    final_answer = _evaluated_messages(mock_execute_request, mock_execute_request.call_count - 1)[-1]
    assert final_answer == {"role": "assistant", "content": result["output"]}


@requires_legacy_agents
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
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
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
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
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
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
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
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
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
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
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_create_agent_action_sync_allow(mock_execute_request, langchain_openai, openai_url):
    """An allowed tool call executes, the agent completes, and each step is evaluated exactly once."""
    from langchain.agents import create_agent

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    llm = langchain_openai.ChatOpenAI(temperature=0, n=1, base_url=openai_url)
    agent = create_agent(model=llm, tools=[add])

    result = agent.invoke({"messages": [HumanMessage(content=TOOL_PROMPT)]})

    # 1. the prompt, 2. the first turn's tool call, 3. the tool result before the
    # second turn, 4. the final answer. ToolNode adds none: the call it runs is the
    # one already evaluated with the model response.
    assert mock_execute_request.call_count == 4
    assert _tool_call_evaluations(mock_execute_request) == [{"name": "add", "arguments": '{"a": 1, "b": 1}'}]
    assert any(
        m.get("role") == "tool" and m.get("content") == "2" for m in _evaluated_messages(mock_execute_request, 2)
    )
    assert _evaluated_messages(mock_execute_request, 3)[-1] == {
        "role": "assistant",
        "content": result["messages"][-1].content,
    }


@requires_create_agent
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_create_agent_edited_tool_call_is_reevaluated(mock_execute_request, langchain_openai, openai_url):
    """A tool call rewritten after the model response was evaluated is evaluated again before it runs."""
    from langchain.agents import create_agent
    from langchain.agents.middleware import AgentMiddleware

    class RewriteArguments(AgentMiddleware):
        def after_model(self, state, runtime):
            message = state["messages"][-1]
            if not message.tool_calls:
                return None
            # model_copy keeps the message id and metadata, so only the call payload differs.
            edited = [{**message.tool_calls[0], "args": {"a": 1, "b": 99}}]
            return {"messages": [message.model_copy(update={"tool_calls": edited})]}

    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),  # prompt
        mock_evaluate_response("ALLOW"),  # model response, add(1, 1)
        mock_evaluate_response("DENY"),  # rewritten call, add(1, 99)
    ]

    llm = langchain_openai.ChatOpenAI(temperature=0, n=1, base_url=openai_url)
    agent = create_agent(model=llm, tools=[add], middleware=[RewriteArguments()])

    with pytest.raises(AIGuardAbortError):
        agent.invoke({"messages": [HumanMessage(content=TOOL_PROMPT)]})

    assert _tool_call_evaluations(mock_execute_request) == [
        {"name": "add", "arguments": '{"a": 1, "b": 1}'},
        {"name": "add", "arguments": '{"a": 1, "b": 99}'},
    ]


@requires_create_agent
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_create_agent_tool_call_evaluated_when_response_evaluation_fails(
    mock_execute_request, langchain_openai, openai_url
):
    """A response evaluation that fails open leaves the tool call to be evaluated before it runs."""
    from langchain.agents import create_agent

    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),  # prompt
        ConnectionError("AI Guard unreachable"),  # model response
        mock_evaluate_response("DENY"),  # tool call, from ToolNode
    ]

    llm = langchain_openai.ChatOpenAI(temperature=0, n=1, base_url=openai_url)
    agent = create_agent(model=llm, tools=[add])

    with pytest.raises(AIGuardAbortError):
        agent.invoke({"messages": [HumanMessage(content=TOOL_PROMPT)]})

    assert mock_execute_request.call_count == 3
    assert _evaluated_messages(mock_execute_request, 2)[-1]["tool_calls"][0]["function"]["name"] == "add"


@requires_create_agent
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("langchain_openai.chat_models.ChatOpenAI._generate", autospec=True)
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
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


@requires_create_agent
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_create_agent_prompt_block(mock_execute_request, langchain_openai, openai_url, decision):
    """AI Guard blocks at the prompt (before-model) step of a create_agent graph.

    The first evaluation returns DENY/ABORT so the LLM is never called. Pins
    regression: the pregel stream generator previously used ``except Exception``
    which silently swallowed ``span.finish()`` for BaseException subclasses
    (AIGuardAbortError), causing the entire trace to be dropped.
    """
    from langchain.agents import create_agent

    mock_execute_request.return_value = mock_evaluate_response(decision)

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

    assert mock_execute_request.call_count == 1  # blocked at prompt, LLM never called


@requires_create_agent
@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_create_agent_prompt_block_async(mock_execute_request, langchain_openai, openai_url, decision):
    """Async variant of test_create_agent_prompt_block."""
    from langchain.agents import create_agent

    mock_execute_request.return_value = mock_evaluate_response(decision)

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

    assert mock_execute_request.call_count == 1  # blocked at prompt, LLM never called


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


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_sync_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    for _ in model.stream(input="how can langsmith help with testing?"):
        pass

    mock_execute_request.assert_called_once()


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_sync_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    with pytest.raises(AIGuardAbortError):
        for _ in model.stream(input="how can langsmith help with testing?"):
            pass

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_streamed_chat_async_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    async for _ in model.astream(input="how can langsmith help with testing?"):
        pass

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_streamed_chat_async_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    with pytest.raises(AIGuardAbortError):
        async for _ in model.astream(input="how can langsmith help with testing?"):
            pass

    mock_execute_request.assert_called_once()


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_llm_sync_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    llm = langchain_openai.OpenAI(base_url=openai_url)

    for _ in llm.stream(input="How do I write technical documentation?"):
        pass

    mock_execute_request.assert_called_once()


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_streamed_llm_sync_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    llm = langchain_openai.OpenAI(base_url=openai_url)

    with pytest.raises(AIGuardAbortError):
        for _ in llm.stream(input="How do I write technical documentation?"):
            pass

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_streamed_llm_async_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    llm = langchain_openai.OpenAI(base_url=openai_url)

    async for _ in llm.astream(input="How do I write technical documentation?"):
        pass

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_streamed_llm_async_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    llm = langchain_openai.OpenAI(base_url=openai_url)

    with pytest.raises(AIGuardAbortError):
        async for _ in llm.astream(input="How do I write technical documentation?"):
            pass

    mock_execute_request.assert_called_once()


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_chat_resets_context_after_block(mock_execute_request, langchain_openai, openai_url):
    """A blocked non-streaming chat call still releases its AI Guard claim.

    The claim is held by AI Guard's wrapper around generate, which releases it in a
    finally, so a block raised out of .before cannot leave it behind.
    """
    from ddtrace.aiguard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("DENY")
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)

    assert is_aiguard_context_active() is False
    with pytest.raises(AIGuardAbortError):
        chat.invoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])
    assert is_aiguard_context_active() is False


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_chat_async_resets_context_after_block(mock_execute_request, langchain_openai, openai_url):
    """Async variant of :func:`test_chat_resets_context_after_block`."""
    from ddtrace.aiguard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("DENY")
    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)

    assert is_aiguard_context_active() is False
    with pytest.raises(AIGuardAbortError):
        await chat.ainvoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])
    assert is_aiguard_context_active() is False


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_resets_context_after_success(mock_execute_request, langchain_openai, openai_url):
    """After a successful sync chat stream, no AI Guard claim is left behind."""
    from ddtrace.aiguard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    assert is_aiguard_context_active() is False
    for _ in model.stream(input="how can langsmith help with testing?"):
        pass
    assert is_aiguard_context_active() is False


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_streamed_chat_resets_context_after_async_success(mock_execute_request, langchain_openai, openai_url):
    """After a successful async chat stream, no AI Guard claim is left behind."""
    from ddtrace.aiguard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    assert is_aiguard_context_active() is False
    async for _ in model.astream(input="how can langsmith help with testing?"):
        pass
    assert is_aiguard_context_active() is False


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_resets_context_after_block(mock_execute_request, langchain_openai, openai_url):
    """A blocked chat stream still releases its AI Guard claim."""
    from ddtrace.aiguard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("DENY")
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    assert is_aiguard_context_active() is False
    with pytest.raises(AIGuardAbortError):
        for _ in model.stream(input="how can langsmith help with testing?"):
            pass
    assert is_aiguard_context_active() is False


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_llm_resets_context_after_success(mock_execute_request, langchain_openai, openai_url):
    from ddtrace.aiguard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    llm = langchain_openai.OpenAI(base_url=openai_url)

    assert is_aiguard_context_active() is False
    for _ in llm.stream(input="How do I write technical documentation?"):
        pass
    assert is_aiguard_context_active() is False


# ---------------------------------------------------------------------------
# Streamed response evaluation (APPSEC-70286)
#
# LangChain streams are buffered and evaluated around each model's own _stream /
# _astream: below LangChain's callbacks, so no callback sees a token before the
# verdict, and above the provider, whose reads LangChain wraps in its own timeout.
# The model read claims both phases, so the provider's own buffer stays passthrough.
# ---------------------------------------------------------------------------


def _stream_evaluation_on():
    return override_ai_guard_config(dict(_ai_guard_analyze_stream_responses_enabled=True))


def _chunk_text(chunks) -> str:
    return "".join(chunk if isinstance(chunk, str) else chunk.content for chunk in chunks)


class _ToolCallStreamingModel(BaseChatModel):
    """Chat model with no network that streams an add(a=1, b=1) tool call across two chunks.

    Once it has seen the tool's result it answers in text instead, so an agent loop ends.
    """

    @property
    def _llm_type(self) -> str:
        return "fake-tool-call-stream"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            yield ChatGenerationChunk(message=AIMessageChunk(content="1 + 1 is 2"))
            return
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="", tool_call_chunks=[{"name": "add", "args": '{"a": 1, "b": 1}', "id": "call_1", "index": 0}]
            )
        )
        yield ChatGenerationChunk(message=AIMessageChunk(content=""))


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_response_evaluated_before_delivery(
    mock_execute_request, openai_stream_evaluation, langchain_openai, openai_url
):
    """LangChain evaluates the request and the buffered response; the OpenAI buffer below stays passthrough."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    chunks = list(model.stream(input="how can langsmith help with testing?"))

    assert chunks
    _assert_evaluated_response(mock_execute_request, _chunk_text(chunks))


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_streamed_chat_async_response_evaluated_before_delivery(
    mock_execute_request, openai_stream_evaluation, langchain_openai, openai_url
):
    """Async variant -- see the sync test."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    chunks = [chunk async for chunk in model.astream(input="how can langsmith help with testing?")]

    assert chunks
    _assert_evaluated_response(mock_execute_request, _chunk_text(chunks))


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_response_block_delivers_no_chunk(
    mock_execute_request, openai_stream_evaluation, langchain_openai, openai_url, decision
):
    """A blocked streamed response raises before any chunk reaches the caller."""
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response(decision)]
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    received = []
    with pytest.raises(AIGuardAbortError):
        for chunk in model.stream(input="how can langsmith help with testing?"):
            received.append(chunk)

    assert received == []
    assert mock_execute_request.call_count == 2


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_response_block_not_swallowed_by_fallbacks(
    mock_execute_request, langchain_openai, openai_url, decision
):
    """with_fallbacks catches Exception; the block must not fall through to the fallback model's stream."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response(decision)] + [
        mock_evaluate_response("ALLOW")
    ] * 2
    model = langchain_openai.ChatOpenAI(base_url=openai_url).with_fallbacks(
        [FakeListChatModel(responses=["fallback answer"])]
    )

    received = []
    with _stream_evaluation_on(), pytest.raises(AIGuardAbortError):
        for chunk in model.stream(input="how can langsmith help with testing?"):
            received.append(chunk)

    assert received == []


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_response_evaluated_for_any_provider(mock_execute_request, langchain):
    """The buffer wraps the model's own _stream, so a model with no AI Guard provider integration is covered too."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    model = FakeListChatModel(responses=["hello from a provider AI Guard does not patch"])

    with _stream_evaluation_on():
        chunks = list(model.stream(input="hi"))

    assert _chunk_text(chunks) == "hello from a provider AI Guard does not patch"
    _assert_evaluated_response(mock_execute_request, "hello from a provider AI Guard does not patch")


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_response_block_for_any_provider(mock_execute_request, langchain, decision):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response(decision)]
    model = FakeListChatModel(responses=["blocked answer"])

    received = []
    with _stream_evaluation_on(), pytest.raises(AIGuardAbortError):
        for chunk in model.stream(input="hi"):
            received.append(chunk)

    assert received == []


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_response_conversion_error_fails_open(mock_execute_request, langchain):
    """A converter failure skips the evaluation; it must not break the caller's stream."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    model = FakeListChatModel(responses=["still delivered"])

    with (
        _stream_evaluation_on(),
        patch("ddtrace.aiguard.integrations._langchain._convert_response_message", side_effect=RuntimeError("boom")),
    ):
        chunks = list(model.stream(input="hi"))

    assert _chunk_text(chunks) == "still delivered"
    assert mock_execute_request.call_count == 1  # the request only


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_llm_response_evaluated_before_delivery(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    llm = langchain_openai.OpenAI(base_url=openai_url)

    with _stream_evaluation_on():
        chunks = list(llm.stream(input="How do I write technical documentation?"))

    assert chunks
    _assert_evaluated_response(mock_execute_request, _chunk_text(chunks))


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_response_not_evaluated_when_flag_disabled(
    mock_execute_request, openai_stream_evaluation, langchain_openai, openai_url
):
    """Turning the flag off at runtime stops stream response evaluation: only the request is evaluated."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    with override_ai_guard_config(dict(_ai_guard_analyze_stream_responses_enabled=False)):
        assert list(model.stream(input="how can langsmith help with testing?"))

    mock_execute_request.assert_called_once()


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_tool_call_marked_evaluated_for_agent_hooks(mock_execute_request, langchain):
    """The agent hooks must see a streamed tool call as already evaluated, or they evaluate it twice.

    The record is put on the last replayed chunk and carried into the aggregate by
    chunk addition, which is how the legacy agent's output parser builds its message.
    """
    from ddtrace.aiguard.integrations._langchain import _tool_call_already_evaluated

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with _stream_evaluation_on():
        chunks = list(_ToolCallStreamingModel().stream(input="What is 1 + 1?"))

    aggregate = chunks[0]
    for chunk in chunks[1:]:
        aggregate = aggregate + chunk
    assert mock_execute_request.call_count == 2
    assert _evaluated_messages(mock_execute_request, 1)[-1]["tool_calls"][0]["function"]["name"] == "add"
    assert _tool_call_already_evaluated(aggregate, "add", {"a": 1, "b": 1})
    assert not _tool_call_already_evaluated(aggregate, "add", {"a": 2, "b": 2})


@requires_legacy_agents
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streaming_agent_evaluates_tool_call_once(mock_execute_request, langchain):
    """AgentExecutor streams its runnable by default; the agent hook must not evaluate the streamed call again."""
    from langchain.agents import AgentExecutor
    from langchain.agents import create_tool_calling_agent

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    agent_prompt = ChatPromptTemplate.from_messages([("human", "{input}"), MessagesPlaceholder("agent_scratchpad")])
    agent = create_tool_calling_agent(_ToolCallStreamingModel(), [add], agent_prompt)
    agent_executor = AgentExecutor(agent=agent, tools=[add], max_iterations=3)
    assert agent_executor.agent.stream_runnable is True

    with _stream_evaluation_on():
        result = agent_executor.invoke({"input": "1 + 1"})

    assert result["output"] == "1 + 1 is 2"
    assert [f["name"] for f in _tool_call_evaluations(mock_execute_request)] == ["add"]


class _EvaluationOrderHandler(BaseCallbackHandler):
    """Records how many AI Guard evaluations had run when each callback fired."""

    def __init__(self, mock_execute_request):
        self._mock = mock_execute_request
        self.tokens: list = []
        self.ends: list = []

    def on_llm_new_token(self, token, **kwargs):
        self.tokens.append((token, self._mock.call_count))

    def on_llm_end(self, response, **kwargs):
        self.ends.append(self._mock.call_count)


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_callbacks_see_tokens_after_verdict(mock_execute_request, langchain):
    """LangChain reports each streamed token to callbacks; none may fire before the response verdict."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    handler = _EvaluationOrderHandler(mock_execute_request)

    with _stream_evaluation_on():
        chunks = list(FakeListChatModel(responses=["hello"]).stream(input="hi", config={"callbacks": [handler]}))

    assert _chunk_text(chunks) == "hello"
    assert handler.tokens and {count for _, count in handler.tokens} == {2}
    assert handler.ends == [2]


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_block_reaches_no_callback(mock_execute_request, langchain, decision):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response(decision)]
    handler = _EvaluationOrderHandler(mock_execute_request)

    with _stream_evaluation_on(), pytest.raises(AIGuardAbortError):
        list(FakeListChatModel(responses=["blocked answer"]).stream(input="hi", config={"callbacks": [handler]}))

    assert handler.tokens == []
    assert handler.ends == []


@requires_astream_events_v2
@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_astream_events_streams_after_verdict(mock_execute_request, langchain):
    """astream_events() on a bare model emits its stream events, all of them after the verdict."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    model = FakeListChatModel(responses=["hello"])

    stream_events = []
    with _stream_evaluation_on():
        async for event in model.astream_events("hi", version="v2"):
            if event["event"] == "on_chat_model_stream":
                stream_events.append(mock_execute_request.call_count)

    assert len(stream_events) == len("hello")
    assert set(stream_events) == {2}


@requires_astream_events_v2
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_chain_astream_events_block_emits_no_model_output(mock_execute_request, langchain, decision):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response(decision)]
    chain = ChatPromptTemplate.from_messages([("human", "{input}")]) | FakeListChatModel(responses=["blocked"])

    events = []
    with _stream_evaluation_on(), pytest.raises(AIGuardAbortError):
        async for event in chain.astream_events({"input": "hi"}, version="v2"):
            events.append(event["event"])

    assert "on_chat_model_stream" not in events
    assert "on_chat_model_end" not in events


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_invoke_streaming_internally_evaluated_once_before_tokens(mock_execute_request, langchain):
    """invoke() streams through _stream when asked to; the buffer evaluates it and .generate.after skips it."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    handler = _EvaluationOrderHandler(mock_execute_request)

    with _stream_evaluation_on():
        result = FakeListChatModel(responses=["hello"]).invoke("hi", config={"callbacks": [handler]}, stream=True)

    assert result.content == "hello"
    _assert_evaluated_response(mock_execute_request, "hello")
    assert {count for _, count in handler.tokens} <= {2}


class _SelfReportingChatModel(BaseChatModel):
    """Streams inside _generate and reports each token to the run manager itself, like ChatOpenAI(streaming=True)."""

    @property
    def _llm_type(self) -> str:
        return "fake-self-reporting"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.language_models.chat_models import generate_from_stream

        return generate_from_stream(self._stream(messages, stop, run_manager, **kwargs))

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.language_models.chat_models import agenerate_from_stream

        return await agenerate_from_stream(self._astream(messages, stop, run_manager, **kwargs))

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        for text in ("self ", "reported"):
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=text))
            if run_manager:
                run_manager.on_llm_new_token(text, chunk=chunk)
            yield chunk

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        for text in ("self ", "reported"):
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=text))
            if run_manager:
                await run_manager.on_llm_new_token(text, chunk=chunk)
            yield chunk


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_tokens_reported_from_inside_stream_wait_for_verdict(mock_execute_request, langchain):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    handler = _EvaluationOrderHandler(mock_execute_request)

    with _stream_evaluation_on():
        result = _SelfReportingChatModel().invoke("hi", config={"callbacks": [handler]})

    assert result.content == "self reported"
    _assert_evaluated_response(mock_execute_request, "self reported")
    assert [token for token, _ in handler.tokens] == ["self ", "reported"]
    assert {count for _, count in handler.tokens} == {2}


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_tokens_reported_from_inside_astream_wait_for_verdict(mock_execute_request, langchain):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    handler = _EvaluationOrderHandler(mock_execute_request)

    with _stream_evaluation_on():
        result = await _SelfReportingChatModel().ainvoke("hi", config={"callbacks": [handler]})

    assert result.content == "self reported"
    _assert_evaluated_response(mock_execute_request, "self reported")
    assert [token for token, _ in handler.tokens] == ["self ", "reported"]
    assert {count for _, count in handler.tokens} == {2}


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_tokens_reported_from_inside_stream_dropped_on_block(mock_execute_request, langchain, decision):
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response(decision)]
    handler = _EvaluationOrderHandler(mock_execute_request)

    with _stream_evaluation_on(), pytest.raises(AIGuardAbortError):
        _SelfReportingChatModel().invoke("hi", config={"callbacks": [handler]})

    assert handler.tokens == []


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_llm_tokens_reported_by_the_model_wait_for_verdict(mock_execute_request, langchain):
    """BaseLLM.stream() hands the run manager to _stream, where the model reports each token."""
    from langchain_core.language_models.llms import LLM
    from langchain_core.outputs import GenerationChunk

    class _SelfReportingLLM(LLM):
        @property
        def _llm_type(self) -> str:
            return "fake-self-reporting-llm"

        def _call(self, prompt, stop=None, run_manager=None, **kwargs):
            raise NotImplementedError

        def _stream(self, prompt, stop=None, run_manager=None, **kwargs):
            for text in ("a", "b"):
                if run_manager:
                    run_manager.on_llm_new_token(text)
                yield GenerationChunk(text=text)

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    handler = _EvaluationOrderHandler(mock_execute_request)

    with _stream_evaluation_on():
        chunks = list(_SelfReportingLLM().stream("hi", config={"callbacks": [handler]}))

    assert chunks == ["a", "b"]
    _assert_evaluated_response(mock_execute_request, "ab")
    assert handler.tokens == [("a", 2), ("b", 2)]


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_subclass_calling_super_stream_is_buffered_once(mock_execute_request, langchain):
    class _Subclass(_SelfReportingChatModel):
        def _stream(self, *args, **kwargs):
            yield from super()._stream(*args, **kwargs)

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with _stream_evaluation_on():
        chunks = list(_Subclass().stream(input="hi"))

    assert _chunk_text(chunks) == "self reported"
    _assert_evaluated_response(mock_execute_request, "self reported")


@pytest.mark.parametrize("stream_evaluation", [True, False], ids=["buffered", "unbuffered"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_stream_claims_once_not_per_chunk(mock_execute_request, langchain, stream_evaluation):
    """The claim covers the model read (buffered) or its first read (unbuffered), never each chunk."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from ddtrace.aiguard import _context

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    model = FakeListChatModel(responses=["an answer streamed one character at a time"])

    with (
        override_ai_guard_config(dict(_ai_guard_analyze_stream_responses_enabled=stream_evaluation)),
        patch.object(_context, "set_aiguard_context_active", wraps=_context.set_aiguard_context_active) as claim,
    ):
        chunks = list(model.stream(input="hi"))

    assert len(chunks) > 10
    assert claim.call_count == 1


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_stream_under_outer_response_claim_is_still_evaluated(mock_execute_request, langchain):
    """An outer framework's claim (a Strands invocation running a LangChain tool) must not skip this model's check."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from ddtrace.aiguard._context import Phase
    from ddtrace.aiguard._context import aiguard_context

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with _stream_evaluation_on(), aiguard_context(Phase.REQUEST, Phase.RESPONSE):
        chunks = list(FakeListChatModel(responses=["tool answer"]).stream(input="hi"))

    assert _chunk_text(chunks) == "tool answer"
    _assert_evaluated_response(mock_execute_request, "tool answer")


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_inner_model_streamed_during_a_buffered_read_gets_its_own_buffer(mock_execute_request, langchain, decision):
    """A router model reading another model's stream must not switch off that model's buffer."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    inner_handler = _EvaluationOrderHandler(mock_execute_request)

    class _RouterModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "fake-router"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise NotImplementedError

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            inner = FakeListChatModel(responses=["blocked inner answer"])
            for chunk in inner.stream(messages, config={"callbacks": [inner_handler]}):
                yield ChatGenerationChunk(message=chunk)

    # Router request, inner request, inner response.
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW")] * 2 + [mock_evaluate_response(decision)]

    with _stream_evaluation_on(), pytest.raises(AIGuardAbortError):
        list(_RouterModel().stream(input="hi"))

    assert inner_handler.tokens == []


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_stream_read_directly_is_buffered_for_classes_defined_before_and_after_patch(mock_execute_request, langchain):
    """Some paths (stream_events v3) read _stream without going through generate or stream first."""

    class _DefinedAfterPatch(_SelfReportingChatModel):
        def _stream(self, *args, **kwargs):
            yield from super()._stream(*args, **kwargs)

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    for model_class in (_SelfReportingChatModel, _DefinedAfterPatch):
        mock_execute_request.reset_mock()
        with _stream_evaluation_on():
            chunks = list(model_class()._stream([HumanMessage(content="hi")]))

        assert "".join(chunk.message.content for chunk in chunks) == "self reported"
        # The request, then the request plus the buffered response.
        assert mock_execute_request.call_count == 2, model_class
        assert _evaluated_messages(mock_execute_request, 0) == [{"role": "user", "content": "hi"}]
        assert _evaluated_messages(mock_execute_request, 1)[-1] == {"role": "assistant", "content": "self reported"}


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_stream_read_directly_blocks_request_before_the_model_is_read(mock_execute_request, langchain, decision):
    """With stream analysis off nothing else checks a direct _stream read's request: the buffer claims it."""
    reads = []

    class _RecordingModel(_SelfReportingChatModel):
        def _stream(self, *args, **kwargs):
            reads.append(True)
            yield from super()._stream(*args, **kwargs)

    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(AIGuardAbortError):
        list(_RecordingModel()._stream([HumanMessage(content="hi")]))

    assert mock_execute_request.call_count == 1
    assert _evaluated_messages(mock_execute_request, 0) == [{"role": "user", "content": "hi"}]
    assert reads == []


class _GenerateOnlyChatModel(BaseChatModel):
    """Chat model without _stream: LangChain serves its stream() through invoke()."""

    @property
    def _llm_type(self) -> str:
        return "fake-generate-only"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="answer"))])


def _disable_streaming_model():
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    if "disable_streaming" not in getattr(BaseChatModel, "model_fields", BaseChatModel.__fields__):
        pytest.skip("disable_streaming needs a newer langchain-core")
    return FakeListChatModel(responses=["answer"], disable_streaming=True)


@pytest.mark.parametrize(
    "make_model", [_GenerateOnlyChatModel, _disable_streaming_model], ids=["no-_stream", "disable_streaming"]
)
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_stream_falling_back_to_invoke_evaluates_the_request_once(mock_execute_request, langchain, make_model):
    model = make_model()
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    chunks = list(model.stream(input="hi"))

    assert _chunk_text(chunks) == "answer"
    requests = [
        index
        for index in range(mock_execute_request.call_count)
        if _evaluated_messages(mock_execute_request, index)[-1]["role"] == "user"
    ]
    assert len(requests) == 1


class _HangingProviderStream:
    """Class-based async iterator, like an SDK stream: cancelling its read does not close it."""

    def __init__(self):
        self.closed = False
        self._sent = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        import asyncio

        if not self._sent:
            self._sent = True
            return ChatGenerationChunk(message=AIMessageChunk(content="partial"))
        await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_cancelled_buffered_astream_closes_the_provider_stream(mock_execute_request, langchain):
    import asyncio

    provider_stream = _HangingProviderStream()

    class _HangingModel(_SelfReportingChatModel):
        def _astream(self, *args, **kwargs):
            return provider_stream

    async def consume():
        return [chunk async for chunk in _HangingModel()._astream([HumanMessage(content="hi")])]

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with _stream_evaluation_on(), pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(consume(), timeout=0.1)

    assert provider_stream.closed


def test_unpatch_removes_stream_buffers(langchain):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from ddtrace.contrib.internal.langchain.patch import patch as langchain_patch
    from ddtrace.contrib.internal.langchain.patch import unpatch as langchain_unpatch

    unpatch_ok = False
    langchain_unpatch()
    try:
        assert "__init_subclass__" not in BaseChatModel.__dict__
        assert not hasattr(FakeListChatModel.__dict__["_stream"], "__wrapped__")
        assert not hasattr(_SelfReportingChatModel.__dict__["_astream"], "__wrapped__")
        unpatch_ok = True
    finally:
        langchain_patch()
    assert unpatch_ok
    assert hasattr(FakeListChatModel.__dict__["_stream"], "__wrapped__")


def test_stream_buffers_do_not_keep_dynamic_model_classes_alive(langchain):
    import gc
    import weakref

    from ddtrace.aiguard.integrations._langchain import _buffered_methods

    class _DynamicModel(_SelfReportingChatModel):
        def _stream(self, *args, **kwargs):
            yield from super()._stream(*args, **kwargs)

    assert hasattr(_DynamicModel.__dict__["_stream"], "__wrapped__")
    assert _buffered_methods[_DynamicModel] == ["_stream"]

    model_class = weakref.ref(_DynamicModel)
    del _DynamicModel
    gc.collect()

    assert model_class() is None


def test_merge_message_chunks_matches_chunk_addition():
    from ddtrace.aiguard.integrations._langchain import _merge_message_chunks

    chunks = [
        AIMessageChunk(content="Hi ", tool_call_chunks=[{"name": "add", "args": '{"a": 1', "id": "c1", "index": 0}]),
        AIMessageChunk(content="there", tool_call_chunks=[{"name": None, "args": ', "b": 1}', "id": None, "index": 0}]),
    ]
    expected = chunks[0] + chunks[1]

    merged = _merge_message_chunks(chunks)

    assert merged.content == expected.content == "Hi there"
    assert merged.tool_calls == expected.tool_calls


def _recording_http_client(seen: list):
    """An httpx client that records the claims held when the provider request actually goes out."""
    import httpx

    from ddtrace.aiguard._context import Phase
    from ddtrace.aiguard._context import is_aiguard_context_active

    def _record(_request):
        seen.append((is_aiguard_context_active(Phase.REQUEST), is_aiguard_context_active(Phase.RESPONSE)))

    return httpx.Client(event_hooks={"request": [_record]})


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_non_streamed_chat_claims_both_phases(mock_execute_request, langchain_openai, openai_url):
    """Observed from the provider's seat: both phases are held when the OpenAI request is sent."""
    from ddtrace.aiguard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    seen: list = []
    chat = langchain_openai.ChatOpenAI(
        temperature=0, max_tokens=256, n=1, base_url=openai_url, http_client=_recording_http_client(seen)
    )

    chat.invoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    assert seen == [(True, True)]
    assert is_aiguard_context_active() is False


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_claims_both_phases(mock_execute_request, langchain_openai, openai_url):
    """Observed from the provider's seat: both phases are held when the OpenAI stream request is sent."""
    from ddtrace.aiguard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    seen: list = []
    model = langchain_openai.ChatOpenAI(base_url=openai_url, http_client=_recording_http_client(seen))

    with _stream_evaluation_on():
        assert list(model.stream(input="how can langsmith help with testing?"))

    assert seen == [(True, True)]
    assert is_aiguard_context_active() is False


@pytest.mark.parametrize("stream_evaluation", [True, False], ids=["buffered", "unbuffered"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_stream_loop_body_is_unclaimed(mock_execute_request, langchain_openai, openai_url, stream_evaluation):
    """The claim is held only while a chunk is pulled, never while the caller's loop body runs.

    Direct SDK calls made inside the loop are therefore evaluated as usual.
    """
    from ddtrace.aiguard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    observed = []
    with override_ai_guard_config(dict(_ai_guard_analyze_stream_responses_enabled=stream_evaluation)):
        for _ in model.stream(input="how can langsmith help with testing?"):
            observed.append(is_aiguard_context_active())

    assert observed and not any(observed), observed


_open_inner_streams: list = []


class _InnerStreamLeftOpenModel(BaseChatModel):
    """Chat model whose _generate starts an inner stream and returns with it still open."""

    @property
    def _llm_type(self) -> str:
        return "fake-inner-stream"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        inner = FakeListChatModel(responses=["inner answer"]).stream(input="inner prompt")
        next(inner)
        _open_inner_streams.append(inner)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="outer answer"))])


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_generate_with_inner_stream_left_open_leaves_no_claim(mock_execute_request, langchain):
    """Generate must release its own claim even when an inner stream is still open when it returns.

    Releasing "the latest claim" would take the stream's instead and leave generate's
    held, so every later provider request on the thread would skip AI Guard.
    """
    from ddtrace.aiguard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    _InnerStreamLeftOpenModel().invoke("hi")
    try:
        assert is_aiguard_context_active() is False
    finally:
        for inner in _open_inner_streams:
            inner.close()
        _open_inner_streams.clear()
    assert is_aiguard_context_active() is False


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_async_stream_closed_from_another_task_leaves_no_claim(mock_execute_request, langchain):
    """Breaking out of astream() early and closing it from another task leaves no claim in this task."""
    import asyncio

    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from ddtrace.aiguard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    iterator = FakeListChatModel(responses=["several chunks"]).astream(input="hi").__aiter__()

    await iterator.__anext__()
    assert is_aiguard_context_active() is False
    await asyncio.create_task(iterator.aclose())
    assert is_aiguard_context_active() is False


# ``filterwarnings`` suppresses an orthogonal pre-existing
# span-lifecycle warning: when a langchain stream is created but never
# iterated, ``shared_stream`` has already started the LLMObs span via
# ``integration.trace(...)`` but ``TracedStream.__iter__``'s ``finally``
# (which runs ``finalize_stream``) never executes, so the span is left
# open and the test runner's "Context was not cleared after test" warning
# fires. That span leak is a separate base stream-handler concern. These
# tests pin the claim contract: an unconsumed stream must not leave an AI
# Guard claim behind, regardless of whether the span itself is finalized.
@pytest.mark.filterwarnings("ignore:Context was not cleared after test:UserWarning")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_unconsumed_stream_does_not_leak_context(mock_execute_request, langchain_openai, openai_url):
    """Creating a langchain stream and never iterating it must not leave an AI Guard claim.

    Otherwise a later direct OpenAI call in the same task would skip AI Guard
    evaluation (codex P2 finding on PR #17913). The claim is only held while a
    chunk is pulled, which never happens here.
    """
    from ddtrace.aiguard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    assert is_aiguard_context_active() is False
    stream = model.stream(input="how can langsmith help with testing?")
    assert is_aiguard_context_active() is False
    stream.close()
    assert is_aiguard_context_active() is False


@pytest.mark.filterwarnings("ignore:Context was not cleared after test:UserWarning")
@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_streamed_chat_unconsumed_async_stream_does_not_leak_context(
    mock_execute_request, langchain_openai, openai_url
):
    """Async variant — see sync test for rationale."""
    from ddtrace.aiguard._context import is_aiguard_context_active

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
    from ddtrace.aiguard._constants import AI_GUARD

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
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
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
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
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
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
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
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
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


def test_langchain_kill_switch_enabled_registers_listeners():
    """DD_AI_GUARD_LANGCHAIN_ENABLED defaults to true: LangChain listeners register."""
    from unittest.mock import Mock

    from ddtrace.aiguard import _listener

    with override_ai_guard_config(dict(_ai_guard_langchain_enabled=True)):
        with patch.object(_listener.core, "on") as mock_on:
            _listener._langchain_listen(Mock())
            assert mock_on.call_count > 0


def test_langchain_kill_switch_disabled_skips_listeners():
    """DD_AI_GUARD_LANGCHAIN_ENABLED=false: no LangChain listeners are registered."""
    from unittest.mock import Mock

    from ddtrace.aiguard import _listener

    with override_ai_guard_config(dict(_ai_guard_langchain_enabled=False)):
        with patch.object(_listener.core, "on") as mock_on:
            _listener._langchain_listen(Mock())
            mock_on.assert_not_called()


def _langchain_span(test_spans):
    from ddtrace.aiguard._constants import AI_GUARD

    return next(s for s in test_spans.spans if s.name != AI_GUARD.RESOURCE_TYPE)


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_chat_response_block_keeps_llmobs_output_and_usage(
    mock_execute_request, langchain_openai, openai_url, llmobs, test_spans
):
    from ddtrace.llmobs._utils import get_llmobs_metrics
    from ddtrace.llmobs._utils import get_llmobs_output_messages

    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response("DENY")]

    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    with pytest.raises(AIGuardAbortError):
        chat.invoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    span = _langchain_span(test_spans)
    assert span.error == 1
    output = get_llmobs_output_messages(span)
    assert "'Whom' is used as the object of a verb" in output[0]["content"]
    metrics = get_llmobs_metrics(span)
    assert metrics["input_tokens"] > 0
    assert metrics["output_tokens"] > 0
    assert span.get_metric("_dd.llmobs.total_tokens") == metrics["total_tokens"]


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_llm_response_block_keeps_llmobs_output_and_usage(
    mock_execute_request, langchain_openai, openai_url, llmobs, test_spans
):
    from ddtrace.llmobs._utils import get_llmobs_metrics
    from ddtrace.llmobs._utils import get_llmobs_output_messages

    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response("DENY")]

    llm = langchain_openai.OpenAI(base_url=openai_url)
    with pytest.raises(AIGuardAbortError):
        llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    span = _langchain_span(test_spans)
    assert span.error == 1
    assert "Cogito, ergo sum" in get_llmobs_output_messages(span)[0]["content"]
    metrics = get_llmobs_metrics(span)
    assert metrics["total_tokens"] > 0
    assert span.get_metric("_dd.llmobs.total_tokens") == metrics["total_tokens"]


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_prompt_block_leaves_llmobs_output_empty(
    mock_execute_request, langchain_openai, openai_url, llmobs, test_spans
):
    """Blocked before the model ran: there is no output and no token usage to report."""
    from ddtrace.llmobs._utils import get_llmobs_metrics
    from ddtrace.llmobs._utils import get_llmobs_output_messages

    mock_execute_request.return_value = mock_evaluate_response("DENY")

    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    with pytest.raises(AIGuardAbortError):
        chat.invoke(input=[HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    span = _langchain_span(test_spans)
    assert span.error == 1
    assert [m.get("content") for m in get_llmobs_output_messages(span)] == [""]
    assert not get_llmobs_metrics(span)
    assert span.get_metric("_dd.llmobs.total_tokens") is None
