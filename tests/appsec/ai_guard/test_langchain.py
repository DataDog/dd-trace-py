from unittest.mock import AsyncMock
from unittest.mock import patch

import langchain
from langchain.agents import AgentExecutor
from langchain.agents import create_openai_functions_agent
import langchain_core
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.prompts import MessagesPlaceholder
import pytest

from ddtrace.appsec.ai_guard import AIGuardAbortError
from tests.appsec.ai_guard.utils import mock_evaluate_response
from tests.appsec.ai_guard.utils import mock_openai_tool_response


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
@patch("openai.resources.chat.completions.completions.Completions.create")
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_agent_action_sync_block(mock_execute_request, mock_openai_request, langchain_openai, openai_url, decision):
    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),  # Allow the initial prompt
        mock_evaluate_response(decision),  # Deny/abort the tool call
    ]
    mock_openai_request.return_value = mock_openai_tool_response("add", {"a": 1, "b": 1})

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
@patch("openai.resources.chat.completions.completions.AsyncCompletions.create", new_callable=AsyncMock)
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_agent_action_async_block(
    mock_execute_request, mock_openai_request, langchain_openai, openai_url, decision
):
    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),  # Allow the initial prompt
        mock_evaluate_response(decision),  # Deny/abort the tool call
    ]
    mock_openai_request.return_value = mock_openai_tool_response("add", {"a": 1, "b": 1})

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
