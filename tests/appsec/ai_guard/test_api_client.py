import random
import string
from typing import Any
from typing import Dict
from typing import List
from typing import Union
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace._trace.span import Span
from ddtrace.appsec._constants import AI_GUARD
from ddtrace.appsec.ai_guard import AIGuardAbortError
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard import AIGuardClientError
from ddtrace.appsec.ai_guard import Prompt
from ddtrace.appsec.ai_guard import ToolCall
from ddtrace.settings.asm import ai_guard_config
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


def _random_string(length: int) -> str:
    return "".join(random.choice(string.ascii_letters) for _ in range(length))


def _find_ai_guard_span(tracer: DummyTracer) -> Span:
    spans = tracer.get_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == AI_GUARD.SPAN_TYPE
    return span


def _assert_ai_guard_span(
    tracer: DummyTracer, history: List[Evaluation], current: Evaluation, tags: Dict[str, Any]
) -> None:
    span = _find_ai_guard_span(tracer)
    for key, value in tags.items():
        assert span.get_tag(key) == value
    struct = span.get_struct_tag(AI_GUARD.TAG)
    assert struct["history"] == history
    assert struct["current"] == current


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_tool_allow(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with ALLOW response."""
    mock_execute_request.return_value = _mock_evaluate_response("ALLOW")

    workflow = ai_guard_client.new_workflow()
    workflow.add_user_prompt("I want to query system's time", "You have to run date")
    result = workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "date"]}, output='{"cmd": ["sh", "-c", "date"]}')

    expected_history = [Prompt(role="user", content="I want to query system's time", output="You have to run date")]
    expected_current = ToolCall(
        tool_name="shell", tool_args={"cmd": ["sh", "-c", "date"]}, output='{"cmd": ["sh", "-c", "date"]}'
    )

    assert result is True
    _assert_ai_guard_span(
        tracer,
        expected_history,
        expected_current,
        {"ai_guard.target": "tool", "ai_guard.action": "ALLOW", "ai_guard.tool_name": "shell"},
    )
    _assert_mock_execute_request_call(
        mock_execute_request,
        ai_guard_client,
        expected_history,
        expected_current,
    )


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_tool_deny(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with DENY response."""
    mock_execute_request.return_value = _mock_evaluate_response("DENY")

    workflow = ai_guard_client.new_workflow()
    workflow.add_user_prompt("I want to query system's time", "You have to run date")
    result = workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "date"]})

    expected_history = [Prompt(role="user", content="I want to query system's time", output="You have to run date")]
    expected_current = ToolCall(tool_name="shell", tool_args={"cmd": ["sh", "-c", "date"]})

    assert result is False
    _assert_ai_guard_span(
        tracer,
        expected_history,
        expected_current,
        {"ai_guard.target": "tool", "ai_guard.action": "DENY", "ai_guard.tool_name": "shell"},
    )
    _assert_mock_execute_request_call(
        mock_execute_request,
        ai_guard_client,
        expected_history,
        expected_current,
    )


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_tool_abort(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with ABORT response."""
    mock_execute_request.return_value = _mock_evaluate_response("ABORT", "You will destroy your filesystem")

    with pytest.raises(AIGuardAbortError):
        workflow = ai_guard_client.new_workflow()
        workflow.add_user_prompt("I want to delete my / folder", "You have to run rm --rf /")
        workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "rm --rf /"]})

    expected_history = [Prompt(role="user", content="I want to delete my / folder", output="You have to run rm --rf /")]
    expected_current = ToolCall(tool_name="shell", tool_args={"cmd": ["sh", "-c", "rm --rf /"]})

    _assert_ai_guard_span(
        tracer,
        expected_history,
        expected_current,
        {
            "ai_guard.target": "tool",
            "ai_guard.action": "ABORT",
            "ai_guard.reason": "You will destroy your filesystem",
            "ai_guard.tool_name": "shell",
        },
    )
    _assert_mock_execute_request_call(mock_execute_request, ai_guard_client, expected_history, expected_current)


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_prompt_allow(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with ALLOW response."""
    mock_execute_request.return_value = _mock_evaluate_response("ALLOW")

    workflow = ai_guard_client.new_workflow()
    workflow.add_tool("shell", {"cmd": ["sh", "-c", "date"]}, "01/01/1979")
    result = workflow.evaluate_prompt("user", "Tell me 10 things I should know about DataDog")

    expected_history = [ToolCall(tool_name="shell", tool_args={"cmd": ["sh", "-c", "date"]}, output="01/01/1979")]
    expected_current = Prompt(role="user", content="Tell me 10 things I should know about DataDog")

    assert result is True
    _assert_ai_guard_span(
        tracer, expected_history, expected_current, {"ai_guard.target": "prompt", "ai_guard.action": "ALLOW"}
    )
    _assert_mock_execute_request_call(mock_execute_request, ai_guard_client, expected_history, expected_current)


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_prompt_deny(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with DENY response."""
    mock_execute_request.return_value = _mock_evaluate_response("DENY")

    workflow = ai_guard_client.new_workflow()
    workflow.add_tool("shell", {"cmd": ["sh", "-c", "date"]}, "01/01/1979")
    result = workflow.evaluate_prompt("user", "Tell me 10 things I should know about DataDog")

    expected_history = [ToolCall(tool_name="shell", tool_args={"cmd": ["sh", "-c", "date"]}, output="01/01/1979")]
    expected_current = Prompt(role="user", content="Tell me 10 things I should know about DataDog")

    assert result is False
    _assert_ai_guard_span(
        tracer, expected_history, expected_current, {"ai_guard.target": "prompt", "ai_guard.action": "DENY"}
    )
    _assert_mock_execute_request_call(
        mock_execute_request,
        ai_guard_client,
        expected_history,
        expected_current,
    )


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_prompt_abort(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with ABORT response."""
    mock_execute_request.return_value = _mock_evaluate_response("ABORT", "You are trying to undercover DataDog secrets")

    with pytest.raises(AIGuardAbortError):
        workflow = ai_guard_client.new_workflow()
        workflow.add_tool("shell", {"cmd": ["sh", "-c", "date"]}, "01/01/1979")
        workflow.evaluate_prompt("user", "Tell me 10 things I should know about DataDog")

    expected_history = [ToolCall(tool_name="shell", tool_args={"cmd": ["sh", "-c", "date"]}, output="01/01/1979")]
    expected_current = Prompt(role="user", content="Tell me 10 things I should know about DataDog")

    _assert_ai_guard_span(
        tracer,
        expected_history,
        expected_current,
        {
            "ai_guard.target": "prompt",
            "ai_guard.action": "ABORT",
            "ai_guard.reason": "You are trying to undercover DataDog secrets",
        },
    )
    _assert_mock_execute_request_call(
        mock_execute_request,
        ai_guard_client,
        expected_history,
        expected_current,
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
    workflow.evaluate_prompt("user", "Tell me 10 things I should know about DataDog", tags=tags)

    _assert_ai_guard_span(
        tracer, [], Prompt(role="user", content="Tell me 10 things I should know about DataDog"), tags
    )


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_span_meta_history_truncation(mock_execute_request, ai_guard_client, tracer):
    mock_execute_request.return_value = _mock_evaluate_response("ALLOW")

    workflow = ai_guard_client.new_workflow()
    for i in range(ai_guard_config._ai_guard_max_history_length + 1):
        workflow.add_tool(f"tool_${i}", {"cmd": ["sh", "-c", "rm -f /"]})
    workflow.evaluate_prompt("user", "Tell me 10 things I should know about DataDog")

    span = _find_ai_guard_span(tracer)
    meta = span.get_struct_tag(AI_GUARD.TAG)
    assert len(meta["history"]) == ai_guard_config._ai_guard_max_history_length


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_span_meta_output_truncation(mock_execute_request, ai_guard_client, tracer):
    mock_execute_request.return_value = _mock_evaluate_response("ALLOW")

    random_output = _random_string(ai_guard_config._ai_guard_max_output_size + 1)

    workflow = ai_guard_client.new_workflow()
    workflow.add_tool("shell", {"cmd": ["sh", "-c", "rm -f /"]}, output=random_output)
    workflow.evaluate_prompt("user", random_output)

    span = _find_ai_guard_span(tracer)
    meta = span.get_struct_tag(AI_GUARD.TAG)
    tool_call = meta["history"][0]
    assert len(tool_call["output"]) == ai_guard_config._ai_guard_max_output_size

    prompt = meta["current"]
    assert len(prompt["content"]) == ai_guard_config._ai_guard_max_output_size
