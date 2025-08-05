from typing import Any
from typing import Dict
from typing import List
from typing import Union
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.appsec.ai_guard import AIGuardAbortError
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard import AIGuardClientError
from ddtrace.appsec.ai_guard import Prompt
from ddtrace.appsec.ai_guard import ToolCall
from tests.utils import DummyTracer


Evaluation = Union[Prompt, ToolCall]


def _mock_evaluate_response(action: str, reason: str = "") -> Mock:
    mock_response = Mock()
    mock_response.status = 200
    mock_response.get_json.return_value = {"data": {"attributes": {"action": action, "reason": reason}}}
    return mock_response


def _assert_mock_execute_request_call(
    mock_execute_request, ai_guard_client: AIGuardClient, history: List[Evaluation], current: Evaluation
):
    expected_payload = {
        "data": {
            "attributes": {
                "history": history,
                "current": current,
            }
        }
    }
    mock_execute_request.assert_called_once_with(
        f"{ai_guard_client._endpoint}/evaluate",
        expected_payload,
    )


def _assert_ai_guard_span(tracer: DummyTracer, tags: Dict[str, Any]) -> None:
    spans = tracer.get_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "ai_guard"
    for key, value in tags.items():
        assert span.get_tag(key) == value


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_tool_allow(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with ALLOW response."""
    mock_execute_request.return_value = _mock_evaluate_response("ALLOW")

    workflow = ai_guard_client.new_workflow()
    workflow.add_user_prompt("I want to query system's time", "You have to run date")
    result = workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "date"]})

    assert result is True
    _assert_ai_guard_span(
        tracer, {"ai_guard.target": "tool", "ai_guard.action": "ALLOW", "ai_guard.tool_name": "shell"}
    )
    _assert_mock_execute_request_call(
        mock_execute_request,
        ai_guard_client,
        [Prompt(role="user", content="I want to query system's time", output="You have to run date")],
        ToolCall(tool_name="shell", tool_args={"cmd": ["sh", "-c", "date"]}),
    )


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_tool_deny(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with DENY response."""
    mock_execute_request.return_value = _mock_evaluate_response("DENY")

    workflow = ai_guard_client.new_workflow()
    workflow.add_user_prompt("I want to query system's time", "You have to run date")
    result = workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "date"]})

    assert result is False
    _assert_ai_guard_span(tracer, {"ai_guard.target": "tool", "ai_guard.action": "DENY", "ai_guard.tool_name": "shell"})
    _assert_mock_execute_request_call(
        mock_execute_request,
        ai_guard_client,
        [Prompt(role="user", content="I want to query system's time", output="You have to run date")],
        ToolCall(tool_name="shell", tool_args={"cmd": ["sh", "-c", "date"]}),
    )


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_tool_abort(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with ABORT response."""
    mock_execute_request.return_value = _mock_evaluate_response("ABORT", "You will destroy your filesystem")

    with pytest.raises(AIGuardAbortError):
        workflow = ai_guard_client.new_workflow()
        workflow.add_user_prompt("I want to delete my / folder", "You have to run rm --rf /")
        workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "rm --rf /"]})

    _assert_ai_guard_span(
        tracer,
        {
            "ai_guard.target": "tool",
            "ai_guard.action": "ABORT",
            "ai_guard.reason": "You will destroy your filesystem",
            "ai_guard.tool_name": "shell",
        },
    )
    _assert_mock_execute_request_call(
        mock_execute_request,
        ai_guard_client,
        [Prompt(role="user", content="I want to delete my / folder", output="You have to run rm --rf /")],
        ToolCall(tool_name="shell", tool_args={"cmd": ["sh", "-c", "rm --rf /"]}),
    )


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_prompt_allow(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with ALLOW response."""
    mock_execute_request.return_value = _mock_evaluate_response("ALLOW")

    workflow = ai_guard_client.new_workflow()
    workflow.add_tool("shell", {"cmd": ["sh", "-c", "date"]}, "01/01/1979")
    result = workflow.evaluate_prompt("user", "Tell me 10 things I should know about DataDog")

    assert result is True
    _assert_ai_guard_span(tracer, {"ai_guard.target": "prompt", "ai_guard.action": "ALLOW"})
    _assert_mock_execute_request_call(
        mock_execute_request,
        ai_guard_client,
        [ToolCall(tool_name="shell", tool_args={"cmd": ["sh", "-c", "date"]}, output="01/01/1979")],
        Prompt(role="user", content="Tell me 10 things I should know about DataDog"),
    )


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_prompt_deny(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with DENY response."""
    mock_execute_request.return_value = _mock_evaluate_response("DENY")

    workflow = ai_guard_client.new_workflow()
    workflow.add_tool("shell", {"cmd": ["sh", "-c", "date"]}, "01/01/1979")
    result = workflow.evaluate_prompt("user", "Tell me 10 things I should know about DataDog")

    assert result is False
    _assert_ai_guard_span(tracer, {"ai_guard.target": "prompt", "ai_guard.action": "DENY"})
    _assert_mock_execute_request_call(
        mock_execute_request,
        ai_guard_client,
        [ToolCall(tool_name="shell", tool_args={"cmd": ["sh", "-c", "date"]}, output="01/01/1979")],
        Prompt(role="user", content="Tell me 10 things I should know about DataDog"),
    )


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_prompt_abort(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with ABORT response."""
    mock_execute_request.return_value = _mock_evaluate_response("ABORT", "You are trying to undercover DataDog secrets")

    with pytest.raises(AIGuardAbortError):
        workflow = ai_guard_client.new_workflow()
        workflow.add_tool("shell", {"cmd": ["sh", "-c", "date"]}, "01/01/1979")
        workflow.evaluate_prompt("user", "Tell me 10 things I should know about DataDog")

    _assert_ai_guard_span(
        tracer,
        {
            "ai_guard.target": "prompt",
            "ai_guard.action": "ABORT",
            "ai_guard.reason": "You are trying to undercover DataDog secrets",
        },
    )
    _assert_mock_execute_request_call(
        mock_execute_request,
        ai_guard_client,
        [ToolCall(tool_name="shell", tool_args={"cmd": ["sh", "-c", "date"]}, output="01/01/1979")],
        Prompt(role="user", content="Tell me 10 things I should know about DataDog"),
    )


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_http_error(mock_execute_request, ai_guard_client, tracer):
    """Test HTTP error handling."""
    mock_response = Mock()
    mock_response.status = 500
    mock_execute_request.return_value = mock_response

    with pytest.raises(AIGuardClientError, match="AI Guard service call failed, status 500"):
        workflow = ai_guard_client.new_workflow()
        workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "rm --rf /"]})


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_invalid_json(mock_execute_request, ai_guard_client, tracer):
    """Test invalid JSON response handling."""
    mock_response = Mock()
    mock_response.status = 200
    mock_response.get_json.side_effect = Exception("Invalid JSON")
    mock_execute_request.return_value = mock_response

    with pytest.raises(AIGuardClientError, match="Unexpected error calling AI Guard service"):
        workflow = ai_guard_client.new_workflow()
        workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "rm --rf /"]})


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_malformed_response(mock_execute_request, ai_guard_client, tracer):
    """Test malformed response structure handling."""
    mock_response = Mock()
    mock_response.status = 200
    mock_response.get_json.return_value = {"invalid": "structure"}
    mock_execute_request.return_value = mock_response

    with pytest.raises(AIGuardClientError, match="AI Guard service returned unexpected response format"):
        workflow = ai_guard_client.new_workflow()
        workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "rm --rf /"]})


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_invalid_action(mock_execute_request, ai_guard_client, tracer):
    """Test invalid action handling."""
    mock_execute_request.return_value = _mock_evaluate_response("GO_TO_SLEEP")

    with pytest.raises(AIGuardClientError, match="AI Guard service returned unrecognized action"):
        workflow = ai_guard_client.new_workflow()
        workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "rm --rf /"]})


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_tags_set_in_span(mock_execute_request, ai_guard_client, tracer):
    mock_execute_request.return_value = _mock_evaluate_response("ALLOW")
    tags = {"tag1": "value1", "tag2": "value2"}

    workflow = ai_guard_client.new_workflow()
    workflow.evaluate_prompt("user", "Tell me 10 things I should know about DataDog", tags)

    _assert_ai_guard_span(tracer, tags)
