from unittest.mock import Mock
from unittest.mock import patch

import langchain
import pytest

from ddtrace.appsec.ai_guard import AIGuardAbortError


def _mock_evaluate_response(action: str, reason: str = "") -> Mock:
    mock_response = Mock()
    mock_response.status = 200
    mock_response.get_json.return_value = {"data": {"attributes": {"action": action, "reason": reason}}}
    return mock_response


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_openai_chat_sync_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = _mock_evaluate_response("ALLOW")

    chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
    chat.invoke(input=[langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    mock_execute_request.assert_called_once()


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_openai_chat_sync_deny_abort(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = _mock_evaluate_response(decision)

    # The prompt should be blocked for both DENY and ABORT
    with pytest.raises(AIGuardAbortError):
        chat = langchain_openai.ChatOpenAI(temperature=0, max_tokens=256, n=1, base_url=openai_url)
        chat.invoke(input=[langchain.schema.HumanMessage(content="When do you use 'whom' instead of 'who'?")])

    mock_execute_request.assert_called_once()


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_openai_llm_sync_allow(mock_execute_request, langchain_openai, openai_url):
    mock_execute_request.return_value = _mock_evaluate_response("ALLOW")

    llm = langchain_openai.OpenAI(base_url=openai_url)
    llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    mock_execute_request.assert_called_once()


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_openai_llm_sync_deny_abort(mock_execute_request, langchain_openai, openai_url, decision):
    mock_execute_request.return_value = _mock_evaluate_response(decision)

    # The prompt should be blocked for both DENY and ABORT
    with pytest.raises(AIGuardAbortError):
        llm = langchain_openai.OpenAI(base_url=openai_url)
        llm.invoke("Can you explain what Descartes meant by 'I think, therefore I am'?")

    mock_execute_request.assert_called_once()


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_tool_invoke_allow(mock_execute_request, langchain_core, langchain_openai, openai_url):
    import langchain_core.tools

    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.

        Args:
            a: first int
            b: second int
        """
        return a + b

    mock_execute_request.return_value = _mock_evaluate_response("ALLOW")

    tool_result = add.invoke({"a": 2, "b": 1})
    assert tool_result == 3

    mock_execute_request.assert_called_once()


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_tool_invoke_deny(mock_execute_request, langchain_core, langchain_openai, openai_url):
    import langchain_core.tools

    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.

        Args:
            a: first int
            b: second int
        """
        return a + b

    mock_execute_request.return_value = _mock_evaluate_response("DENY")

    tool_result = add.invoke({"a": 2, "b": 1})
    assert tool_result == "Tool call 'add' was blocked due to security policies, the tool was not executed."

    mock_execute_request.assert_called_once()


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_tool_invoke_abort(mock_execute_request, langchain_core, langchain_openai, openai_url):
    import langchain_core.tools

    @langchain_core.tools.tool
    def add(a: int, b: int) -> int:
        """Adds a and b.

        Args:
            a: first int
            b: second int
        """
        return a + b

    mock_execute_request.return_value = _mock_evaluate_response("ABORT")

    with pytest.raises(AIGuardAbortError):
        add.invoke({"a": 2, "b": 1})

    mock_execute_request.assert_called_once()
