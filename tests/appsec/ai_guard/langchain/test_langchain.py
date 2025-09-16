import json
from typing import Any
from unittest.mock import patch

import langchain
from langchain.agents import AgentExecutor
from langchain.agents import create_openai_functions_agent
import langchain_core
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
from tests.appsec.ai_guard.utils import mock_evaluate_response


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


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_openai_chat_sync_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    chat.invoke(input=[langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_openai_chat_async_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    await chat.ainvoke(input=[langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    mock_execute_request.assert_called_once()


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_openai_chat_sync_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    # The prompt should be blocked for both DENY and ABORT
    with pytest.raises(AIGuardAbortError):
        chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
        chat.invoke(input=[langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_openai_chat_async_block(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    # The prompt should be blocked for both DENY and ABORT
    with pytest.raises(AIGuardAbortError):
        chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
        await chat.ainvoke(input=[langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])

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


# TODO use testagent cassettes instead of mocking OpenAI
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("langchain_openai.chat_models.ChatOpenAI._generate")
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_agent_action_sync_block(mock_execute_request, mock_openai_request, langchain_openai, openai_url, decision):
    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),  # Allow the initial prompt
        mock_evaluate_response(decision),  # Deny/abort the tool call
    ]
    mock_openai_request.return_value = _mock_openai_tool_response("add", {"a": 1, "b": 1})

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
    llm = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    agent = create_openai_functions_agent(llm, tools, agent_prompt)

    agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False, return_intermediate_steps=False)
    agent_executor.agent.stream_runnable = False

    if decision == "DENY":
        result = agent_executor.invoke({"input": "1 + 1"})
        assert result["output"] == "Tool call 'add' was blocked due to security policies."
    else:
        with pytest.raises(AIGuardAbortError):
            agent_executor.invoke({"input": "1 + 1"})

    assert mock_execute_request.call_count == 2  # One for prompt, one for tool
    assert mock_openai_request.call_count == 1  # Initial prompt that returns a function call result


# TODO use testagent cassettes instead of mocking OpenAI
@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("langchain_openai.chat_models.ChatOpenAI._agenerate", autospec=True)
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_agent_action_async_block(
    mock_execute_request, mock_openai_request, langchain_openai, openai_url, decision
):
    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),  # Allow the initial prompt
        mock_evaluate_response(decision),  # Deny/abort the tool call
    ]
    mock_openai_request.return_value = _mock_openai_tool_response("add", {"a": 1, "b": 1})

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
    llm = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    agent = create_openai_functions_agent(llm, tools, agent_prompt)

    agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False, return_intermediate_steps=False)
    agent_executor.agent.stream_runnable = False

    if decision == "DENY":
        result = await agent_executor.ainvoke({"input": "1 + 1"})
        assert result["output"] == "Tool call 'add' was blocked due to security policies."
    else:
        with pytest.raises(AIGuardAbortError):
            await agent_executor.ainvoke({"input": "1 + 1"})

    assert mock_execute_request.call_count == 2  # One for prompt, one for tool
    assert mock_openai_request.call_count == 1  # Initial prompt that returns a function call result


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
        AIMessage(content="", tool_calls=[ToolCall(id="1", name="add", args={"a": 1, "b": 1})]),
        ToolMessage(tool_call_id="1", tool_name="add", content="2"),
        AIMessage(role="assistant", content="One plus one is two"),
    ]
    result = _convert_messages(messages)
    assert len(result) == 6  # collapse tool and function messages

    assert result[0]["role"] == "system"
    assert result[0]["content"] == "You are a beautiful assistant"

    assert result[1]["role"] == "user"
    assert result[1]["content"] == "What day is today?"

    assert result[2]["tool_name"] == "calendar_check"
    assert result[2]["tool_args"] == {"expression": "today"}
    assert result[2]["output"] == "Today is Monday"

    assert result[3]["role"] == "user"
    assert result[3]["content"] == "One plus one?"

    assert result[4]["tool_name"] == "add"
    assert result[4]["tool_args"] == {"a": 1, "b": 1}
    assert result[4]["output"] == "2"

    assert result[5]["role"] == "assistant"
    assert result[5]["content"] == "One plus one is two"


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
