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


def _llm_result(messages):
    """Wrap assistant messages in the LLMResult shape a chat model returns."""
    from langchain_core.outputs import LLMResult

    return LLMResult(generations=[[ChatGeneration(message=m) for m in messages]])


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


# ---------------------------------------------------------------------------
# Response-side evaluation (APPSEC-70274)
#
# LangChain marks the AI Guard context active for the whole model call, so the
# OpenAI / Anthropic listeners skip their own response evaluation. These tests
# pin the replacement: the response is evaluated, and a block on it aborts the
# call instead of handing the answer back.
# ---------------------------------------------------------------------------


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


def test_convert_generations_includes_tool_calls():
    """A tool-call-only response must still convert to something evaluable.

    An application calling bind_tools(...).invoke(...) executes the returned call
    itself, with no agent hook in the path, so dropping tool calls here let an
    unsafe call reach user code unevaluated.
    """
    from langchain_core.outputs import Generation

    from ddtrace.aiguard.integrations._langchain import _convert_generations

    tool_only = ChatGeneration(message=AIMessage(content="", tool_calls=[ToolCall(id="c1", name="add", args={})]))
    converted = _convert_generations([tool_only])
    assert converted == [
        {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "add", "arguments": "{}"}}]}
    ]

    # Text and tool calls stay in one assistant turn, so the trailing message the
    # evaluator classifies still carries the tool call.
    with_text = ChatGeneration(message=AIMessage(content="hello", tool_calls=[ToolCall(id="c1", name="add", args={})]))
    assert _convert_generations([with_text]) == [
        {
            "role": "assistant",
            "content": "hello",
            "tool_calls": [{"id": "c1", "function": {"name": "add", "arguments": "{}"}}],
        }
    ]

    # Non-chat LLMs return a plain Generation carrying only text.
    assert _convert_generations([Generation(text="plain")]) == [{"role": "assistant", "content": "plain"}]

    # A converter failure on one generation must not lose the others.
    assert _convert_generations([object(), Generation(text="kept")]) == [{"role": "assistant", "content": "kept"}]


def test_tool_call_dedup_is_bound_to_the_evaluated_payload():
    """The dedup record must match the call, not just the message.

    A graph step or human-in-the-loop update can rewrite a tool's name or
    arguments after the response was evaluated. A bare "already checked" flag
    would wave the rewritten call straight through.
    """
    from ddtrace.aiguard.integrations._langchain import _EVALUATED_KEY
    from ddtrace.aiguard.integrations._langchain import _mark_tool_calls_evaluated
    from ddtrace.aiguard.integrations._langchain import _tool_call_already_evaluated

    msg = AIMessage(content="", tool_calls=[ToolCall(id="c1", name="add", args={"a": 1, "b": 1})])
    _mark_tool_calls_evaluated(msg)

    # The call as evaluated is skipped...
    assert _tool_call_already_evaluated(msg, "add", {"a": 1, "b": 1}) is True
    # ...but an edited argument is NOT.
    assert _tool_call_already_evaluated(msg, "add", {"a": 1, "b": 99}) is False
    # ...nor a swapped tool name.
    assert _tool_call_already_evaluated(msg, "rm_rf", {"a": 1, "b": 1}) is False
    # An unmarked message never matches.
    assert _tool_call_already_evaluated(AIMessage(content=""), "add", {"a": 1, "b": 1}) is False

    # Argument ordering is normalised, so a re-serialised identical call matches.
    assert _tool_call_already_evaluated(msg, "add", {"b": 1, "a": 1}) is True

    # The marker is not visible to providers: additional_kwargs is what langchain
    # serializes back into outbound requests.
    assert _EVALUATED_KEY not in msg.additional_kwargs


def test_tool_call_dedup_survives_a_copy_but_not_an_edit():
    """langgraph copies messages into state; the record must travel, the skip must not."""
    from ddtrace.aiguard.integrations._langchain import _mark_tool_calls_evaluated
    from ddtrace.aiguard.integrations._langchain import _tool_call_already_evaluated

    msg = AIMessage(content="", tool_calls=[ToolCall(id="c1", name="add", args={"a": 1})])
    _mark_tool_calls_evaluated(msg)
    # model_copy on pydantic v2 (langchain-core >= 0.2), copy on v1.
    copied = (getattr(msg, "model_copy", None) or msg.copy)()

    assert _tool_call_already_evaluated(copied, "add", {"a": 1}) is True
    # The copy carries the record, but an edited call still fails to match it.
    assert _tool_call_already_evaluated(copied, "add", {"a": 2}) is False


def test_legacy_function_call_dedup_matches_across_argument_shapes():
    """The legacy path compares a JSON-string payload against a parsed mapping."""
    from ddtrace.aiguard.integrations._langchain import _mark_tool_calls_evaluated
    from ddtrace.aiguard.integrations._langchain import _tool_call_already_evaluated

    msg = AIMessage(
        content="",
        additional_kwargs={"function_call": {"name": "add", "arguments": '{"a": 1, "b": 1}'}},
    )
    _mark_tool_calls_evaluated(msg)

    # Agent.plan hands over the parsed tool_input, not the raw string.
    assert _tool_call_already_evaluated(msg, "add", {"a": 1, "b": 1}) is True
    assert _tool_call_already_evaluated(msg, "add", {"a": 1, "b": 2}) is False


def test_failed_evaluation_does_not_mark_tool_calls():
    """A swallowed transport error must leave the execution-time check available.

    _evaluate_langchain_response fails open so the model call keeps working, but
    marking the calls anyway would make the agent hooks skip the only remaining
    check on a call nothing ever evaluated.
    """
    from unittest.mock import Mock

    from ddtrace.aiguard.integrations._langchain import _langchain_chatmodel_generate_after
    from ddtrace.aiguard.integrations._langchain import _tool_call_already_evaluated

    message = AIMessage(content="", tool_calls=[ToolCall(id="c1", name="add", args={"a": 1})])
    result = _llm_result([message])

    client = Mock()
    client.evaluate.side_effect = RuntimeError("ai guard unreachable")
    _langchain_chatmodel_generate_after(client, [[HumanMessage(content="1 + 1")]], result)

    client.evaluate.assert_called_once()
    assert _tool_call_already_evaluated(message, "add", {"a": 1}) is False


def test_mixed_text_and_tool_call_response_stays_one_turn():
    """A reply with both text and a tool call must keep them in a single message.

    AIGuardClient reads target / tool_name off the trailing message, so splitting
    them would classify the tool call as a prompt and drop its name.
    """
    from ddtrace.aiguard._api_client import AIGuardClient
    from ddtrace.aiguard.integrations._langchain import _convert_generations

    generation = ChatGeneration(
        message=AIMessage(content="calling add", tool_calls=[ToolCall(id="c1", name="add", args={"a": 1})])
    )
    converted = _convert_generations([generation])

    assert len(converted) == 1
    assert converted[0]["content"] == "calling add"
    assert converted[0]["tool_calls"][0]["function"]["name"] == "add"
    # The classification AIGuardClient would derive from this trailing message.
    assert AIGuardClient._get_tool_name(converted[-1], converted) == "add"


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("langchain_openai.chat_models.ChatOpenAI._generate", autospec=True)
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_standalone_tool_call_response_is_blocked(
    mock_execute_request, mock_openai_request, langchain_openai, openai_url, decision
):
    """bind_tools(...).invoke(...) with no agent: the tool call must still be evaluated.

    Neither Agent.plan nor ToolNode runs here, and the provider listener is
    suppressed by the LangChain context, so the response listener is the only
    thing that can catch an unsafe tool call.
    """
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response(decision)]
    mock_openai_request.side_effect = lambda self, messages, *a, **kw: _mock_openai_tool_call_response(
        "add", {"a": 1, "b": 1}
    )

    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.

        Args:
            a: first int
            b: second int
        """
        return a + b

    llm = langchain_openai.ChatOpenAI(temperature=0, n=1, base_url=openai_url).bind_tools([add])

    with pytest.raises(AIGuardAbortError):
        llm.invoke([HumanMessage(content="1 + 1")])

    assert mock_execute_request.call_count == 2
    # The blocked evaluation carried the tool call, not an empty assistant turn.
    evaluated = _evaluated_messages(mock_execute_request, 1)
    assert evaluated[-1]["tool_calls"][0]["function"]["name"] == "add"


@patch("langchain_openai.chat_models.ChatOpenAI._generate", autospec=True)
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_standalone_tool_call_response_is_evaluated_on_allow(
    mock_execute_request, mock_openai_request, langchain_openai, openai_url
):
    """Same path, allowed: the call is returned but it was scanned first."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    mock_openai_request.side_effect = lambda self, messages, *a, **kw: _mock_openai_tool_call_response(
        "add", {"a": 1, "b": 1}
    )

    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.

        Args:
            a: first int
            b: second int
        """
        return a + b

    llm = langchain_openai.ChatOpenAI(temperature=0, n=1, base_url=openai_url).bind_tools([add])
    result = llm.invoke([HumanMessage(content="1 + 1")])

    assert result.tool_calls[0]["name"] == "add"
    assert mock_execute_request.call_count == 2
    evaluated = _evaluated_messages(mock_execute_request, 1)
    assert evaluated[-1]["tool_calls"][0]["function"]["name"] == "add"


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
@patch("langchain_openai.chat_models.ChatOpenAI._generate", autospec=True)
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
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

    # Four evaluations across the agent loop:
    #   1. before model (user prompt)
    #   2. before tool (the ``add`` tool call)
    #   3. before the second model turn, whose trailing message is the tool
    #      result -> AI Guard evaluates the tool output in context.
    #   4. after the second model turn -> the agent's final text answer.
    # The first model turn adds no after-evaluation: its response is a bare
    # tool call, and tool calls are evaluated at step 2 rather than twice.
    assert mock_execute_request.call_count == 4
    assert any(getattr(m, "content", None) == "The answer is 2" for m in result["messages"])

    # The third evaluation must carry the tool result (role="tool") so AI Guard
    # sees the tool output, not just the original prompt.
    tool_result_eval_messages = _evaluated_messages(mock_execute_request, 2)
    assert any(m.get("role") == "tool" and m.get("content") == "2" for m in tool_result_eval_messages)

    # The final evaluation carries the agent's answer, which nothing scanned before.
    final_eval_messages = _evaluated_messages(mock_execute_request, 3)
    assert final_eval_messages[-1] == {"role": "assistant", "content": "The answer is 2"}


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
    """A blocked non-streaming chat call still releases the active counter.

    The ``.generate.before`` listener bumps the counter *before* evaluating
    (so it remains active during the underlying call), and the contrib's
    ``finally`` block dispatches ``.generate.finally`` which resets it on
    every exit path — including a block where the dispatch raises out of
    ``.before``.
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
    """After a successful sync chat stream, the AI Guard active counter is back at zero."""
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
    """After a successful async chat stream, the AI Guard active counter is back at zero."""
    from ddtrace.aiguard._context import is_aiguard_context_active

    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    model = langchain_openai.ChatOpenAI(base_url=openai_url)

    assert is_aiguard_context_active() is False
    async for _ in model.astream(input="how can langsmith help with testing?"):
        pass
    assert is_aiguard_context_active() is False


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streamed_chat_resets_context_after_block(mock_execute_request, langchain_openai, openai_url):
    """A blocked chat stream still releases the active counter (paired in the
    contrib's ``finally`` path / ``except`` path of ``shared_stream``).
    """
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


# ``filterwarnings`` suppresses an orthogonal pre-existing
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
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
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


# ---------------------------------------------------------------------------
# APPSEC-68147 (LangChain): a response block errors the span, but the model
# already ran and the tokens were already spent. Output and usage must survive,
# otherwise blocked calls vanish from cost accounting when LangChain is the only
# instrumented LLM integration.
# ---------------------------------------------------------------------------


def _langchain_integration():
    from ddtrace import config as dd_config
    from ddtrace.llmobs._integrations import LangChainIntegration

    return LangChainIntegration(integration_config=dd_config.langchain)


def _llm_result_with_usage(text="blocked body"):
    from langchain_core.outputs import LLMResult

    return LLMResult(
        generations=[[ChatGeneration(message=AIMessage(content=text))]],
        llm_output={"token_usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}},
    )


def _output_contents(span):
    from ddtrace.llmobs._utils import get_llmobs_output_messages

    return [m.get("content") for m in (get_llmobs_output_messages(span) or [])]


def test_chat_output_and_usage_recorded_when_ai_guard_blocks_response():
    """Errored span + a real response: output and token metrics are still recorded."""
    from ddtrace.llmobs._utils import get_llmobs_metrics
    from ddtrace.trace import tracer

    integration = _langchain_integration()
    with tracer.trace("langchain.request", span_type="llm") as span:
        span.error = 1
        integration._llmobs_set_tags_from_chat_model(
            span, [[HumanMessage(content="1 + 1")]], {}, _llm_result_with_usage()
        )
        assert _output_contents(span) == ["blocked body"]
        metrics = get_llmobs_metrics(span) or {}
        assert metrics.get("input_tokens") == 3
        assert metrics.get("output_tokens") == 5
        assert metrics.get("total_tokens") == 8


def test_chat_output_suppressed_on_plain_error():
    """A genuine model error leaves no response, so output stays blank."""
    from ddtrace.trace import tracer

    integration = _langchain_integration()
    with tracer.trace("langchain.request", span_type="llm") as span:
        span.error = 1
        integration._llmobs_set_tags_from_chat_model(span, [[HumanMessage(content="1 + 1")]], {}, None)
        assert _output_contents(span) == [""]


def test_llm_output_and_usage_recorded_when_ai_guard_blocks_response():
    """Non-chat LLM variant of the same contract."""
    from ddtrace.llmobs._utils import get_llmobs_metrics
    from ddtrace.trace import tracer

    integration = _langchain_integration()
    with tracer.trace("langchain.request", span_type="llm") as span:
        span.error = 1
        integration._llmobs_set_tags_from_llm(span, ["1 + 1"], {}, _llm_result_with_usage())
        assert _output_contents(span) == ["blocked body"]
        metrics = get_llmobs_metrics(span) or {}
        assert metrics.get("total_tokens") == 8


def test_llm_output_suppressed_on_plain_error():
    from ddtrace.trace import tracer

    integration = _langchain_integration()
    with tracer.trace("langchain.request", span_type="llm") as span:
        span.error = 1
        integration._llmobs_set_tags_from_llm(span, ["1 + 1"], {}, None)
        assert _output_contents(span) == [""]


def test_apm_shadow_token_metrics_recorded_when_ai_guard_blocks_response():
    """The APM span keeps its token metrics when a response block errors the span.

    Driven through the public llmobs_set_tags so the APM shadow path actually
    runs; the LLMObs assertions above call the private helpers and skip it.
    """
    from ddtrace.trace import tracer

    integration = _langchain_integration()
    with tracer.trace("langchain.request", span_type="llm") as span:
        span.error = 1
        integration.llmobs_set_tags(span, [[HumanMessage(content="1 + 1")]], {}, _llm_result_with_usage(), "chat")
        assert span.get_metric("_dd.llmobs.input_tokens") == 3
        assert span.get_metric("_dd.llmobs.total_tokens") == 8


def test_apm_shadow_token_metrics_absent_without_a_response():
    """A genuine error leaves no result, so there are no tokens to record."""
    from ddtrace.trace import tracer

    integration = _langchain_integration()
    with tracer.trace("langchain.request", span_type="llm") as span:
        span.error = 1
        integration.llmobs_set_tags(span, [[HumanMessage(content="1 + 1")]], {}, None, "chat")
        assert span.get_metric("_dd.llmobs.total_tokens") is None
