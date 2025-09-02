from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.appsec._constants import AI_GUARD
from ddtrace.appsec.ai_guard import AIGuardAbortError
from ddtrace.appsec.ai_guard import AIGuardClientError
from ddtrace.appsec.ai_guard import Prompt
from ddtrace.appsec.ai_guard import ToolCall
from ddtrace.settings.asm import ai_guard_config
from tests.appsec.ai_guard.utils import assert_ai_guard_span
from tests.appsec.ai_guard.utils import assert_mock_execute_request_call
from tests.appsec.ai_guard.utils import find_ai_guard_span
from tests.appsec.ai_guard.utils import mock_evaluate_response
from tests.appsec.ai_guard.utils import random_string
from tests.utils import override_global_config


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_tool_allow(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with ALLOW response."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    workflow = ai_guard_client.new_workflow()
    workflow.add_user_prompt("I want to query system's time", "You have to run date")
    result = workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "date"]}, output='{"cmd": ["sh", "-c", "date"]}')

    expected_history = [Prompt(role="user", content="I want to query system's time", output="You have to run date")]
    expected_current = ToolCall(
        tool_name="shell", tool_args={"cmd": ["sh", "-c", "date"]}, output='{"cmd": ["sh", "-c", "date"]}'
    )

    assert result is True
    assert_ai_guard_span(
        tracer,
        expected_history,
        expected_current,
        {"ai_guard.target": "tool", "ai_guard.action": "ALLOW", "ai_guard.tool_name": "shell"},
    )
    assert_mock_execute_request_call(
        mock_execute_request,
        ai_guard_client,
        expected_history,
        expected_current,
    )


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_tool_deny(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with DENY response."""
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    workflow = ai_guard_client.new_workflow()
    workflow.add_user_prompt("I want to query system's time", "You have to run date")
    result = workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "date"]})

    expected_history = [Prompt(role="user", content="I want to query system's time", output="You have to run date")]
    expected_current = ToolCall(tool_name="shell", tool_args={"cmd": ["sh", "-c", "date"]})

    assert result is False
    assert_ai_guard_span(
        tracer,
        expected_history,
        expected_current,
        {
            "ai_guard.target": "tool",
            "ai_guard.action": "DENY",
            "ai_guard.tool_name": "shell",
            "ai_guard.blocked": "true",
        },
    )
    assert_mock_execute_request_call(
        mock_execute_request,
        ai_guard_client,
        expected_history,
        expected_current,
    )


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_tool_abort(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with ABORT response."""
    mock_execute_request.return_value = mock_evaluate_response("ABORT", "You will destroy your filesystem")

    with pytest.raises(AIGuardAbortError):
        workflow = ai_guard_client.new_workflow()
        workflow.add_user_prompt("I want to delete my / folder", "You have to run rm --rf /")
        workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "rm --rf /"]})

    expected_history = [Prompt(role="user", content="I want to delete my / folder", output="You have to run rm --rf /")]
    expected_current = ToolCall(tool_name="shell", tool_args={"cmd": ["sh", "-c", "rm --rf /"]})

    assert_ai_guard_span(
        tracer,
        expected_history,
        expected_current,
        {
            "ai_guard.target": "tool",
            "ai_guard.action": "ABORT",
            "ai_guard.reason": "You will destroy your filesystem",
            "ai_guard.tool_name": "shell",
            "ai_guard.blocked": "true",
        },
    )
    assert_mock_execute_request_call(mock_execute_request, ai_guard_client, expected_history, expected_current)


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_prompt_allow(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with ALLOW response."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    workflow = ai_guard_client.new_workflow()
    workflow.add_tool("shell", {"cmd": ["sh", "-c", "date"]}, "01/01/1979")
    result = workflow.evaluate_prompt("user", "Tell me 10 things I should know about DataDog")

    expected_history = [ToolCall(tool_name="shell", tool_args={"cmd": ["sh", "-c", "date"]}, output="01/01/1979")]
    expected_current = Prompt(role="user", content="Tell me 10 things I should know about DataDog")

    assert result is True
    assert_ai_guard_span(
        tracer, expected_history, expected_current, {"ai_guard.target": "prompt", "ai_guard.action": "ALLOW"}
    )
    assert_mock_execute_request_call(mock_execute_request, ai_guard_client, expected_history, expected_current)


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_prompt_deny(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with DENY response."""
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    workflow = ai_guard_client.new_workflow()
    workflow.add_tool("shell", {"cmd": ["sh", "-c", "date"]}, "01/01/1979")
    result = workflow.evaluate_prompt("user", "Tell me 10 things I should know about DataDog")

    expected_history = [ToolCall(tool_name="shell", tool_args={"cmd": ["sh", "-c", "date"]}, output="01/01/1979")]
    expected_current = Prompt(role="user", content="Tell me 10 things I should know about DataDog")

    assert result is False
    assert_ai_guard_span(
        tracer,
        expected_history,
        expected_current,
        {"ai_guard.target": "prompt", "ai_guard.action": "DENY", "ai_guard.blocked": "true"},
    )
    assert_mock_execute_request_call(
        mock_execute_request,
        ai_guard_client,
        expected_history,
        expected_current,
    )


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_prompt_abort(mock_execute_request, ai_guard_client, tracer):
    """Test successful evaluation with ABORT response."""
    mock_execute_request.return_value = mock_evaluate_response("ABORT", "You are trying to undercover DataDog secrets")

    with pytest.raises(AIGuardAbortError):
        workflow = ai_guard_client.new_workflow()
        workflow.add_tool("shell", {"cmd": ["sh", "-c", "date"]}, "01/01/1979")
        workflow.evaluate_prompt("user", "Tell me 10 things I should know about DataDog")

    expected_history = [ToolCall(tool_name="shell", tool_args={"cmd": ["sh", "-c", "date"]}, output="01/01/1979")]
    expected_current = Prompt(role="user", content="Tell me 10 things I should know about DataDog")

    assert_ai_guard_span(
        tracer,
        expected_history,
        expected_current,
        {
            "ai_guard.target": "prompt",
            "ai_guard.action": "ABORT",
            "ai_guard.reason": "You are trying to undercover DataDog secrets",
            "ai_guard.blocked": "true",
        },
    )
    assert_mock_execute_request_call(
        mock_execute_request,
        ai_guard_client,
        expected_history,
        expected_current,
    )


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_http_error(mock_execute_request, ai_guard_client):
    """Test HTTP error handling."""
    mock_response = Mock()
    mock_response.status = 500
    mock_execute_request.return_value = mock_response

    with pytest.raises(AIGuardClientError, match="AI Guard service call failed, status 500"):
        workflow = ai_guard_client.new_workflow()
        workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "rm --rf /"]})


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_invalid_json(mock_execute_request, ai_guard_client):
    """Test invalid JSON response handling."""
    mock_response = Mock()
    mock_response.status = 200
    mock_response.get_json.side_effect = Exception("Invalid JSON")
    mock_execute_request.return_value = mock_response

    with pytest.raises(AIGuardClientError, match="Unexpected error calling AI Guard service"):
        workflow = ai_guard_client.new_workflow()
        workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "rm --rf /"]})


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_malformed_response(mock_execute_request, ai_guard_client):
    """Test malformed response structure handling."""
    mock_response = Mock()
    mock_response.status = 200
    mock_response.get_json.return_value = {"invalid": "structure"}
    mock_execute_request.return_value = mock_response

    with pytest.raises(AIGuardClientError, match="AI Guard service returned unexpected response format"):
        workflow = ai_guard_client.new_workflow()
        workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "rm --rf /"]})


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_invalid_action(mock_execute_request, ai_guard_client):
    """Test invalid action handling."""
    mock_execute_request.return_value = mock_evaluate_response("GO_TO_SLEEP")

    with pytest.raises(AIGuardClientError, match="AI Guard service returned unrecognized action"):
        workflow = ai_guard_client.new_workflow()
        workflow.evaluate_tool("shell", {"cmd": ["sh", "-c", "rm --rf /"]})


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_tags_set_in_span(mock_execute_request, ai_guard_client, tracer):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    tags = {"tag1": "value1", "tag2": "value2"}

    workflow = ai_guard_client.new_workflow()
    workflow.evaluate_prompt("user", "Tell me 10 things I should know about DataDog", tags=tags)

    assert_ai_guard_span(tracer, [], Prompt(role="user", content="Tell me 10 things I should know about DataDog"), tags)


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_span_meta_history_truncation(mock_execute_request, ai_guard_client, tracer):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    workflow = ai_guard_client.new_workflow()
    for i in range(ai_guard_config._ai_guard_max_history_length + 1):
        workflow.add_tool(f"tool_${i}", {"cmd": ["sh", "-c", "rm -f /"]})
    workflow.evaluate_prompt("user", "Tell me 10 things I should know about DataDog")

    span = find_ai_guard_span(tracer)
    meta = span.get_struct_tag(AI_GUARD.TAG)
    assert len(meta["history"]) == ai_guard_config._ai_guard_max_history_length


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_span_meta_content_truncation(mock_execute_request, ai_guard_client, tracer):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    random_output = random_string(ai_guard_config._ai_guard_max_content_size + 1)

    workflow = ai_guard_client.new_workflow()
    workflow.add_tool("shell", {"cmd": ["sh", "-c", "rm -f /"]}, output=random_output)
    workflow.evaluate_prompt("user", random_output)

    span = find_ai_guard_span(tracer)
    meta = span.get_struct_tag(AI_GUARD.TAG)
    tool_call = meta["history"][0]
    assert len(tool_call["output"]) == ai_guard_config._ai_guard_max_content_size

    prompt = meta["current"]
    assert len(prompt["content"]) == ai_guard_config._ai_guard_max_content_size


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_non_blocking_mode(mock_execute_request, ai_guard_client, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision, block=False)

    workflow = ai_guard_client.new_workflow()
    result = workflow.evaluate_prompt("user", "What is your name?")
    assert result  # should not be blocked


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_meta_attribute(mock_execute_request, ai_guard_client):
    with override_global_config({"service": "test-service", "env": "test-env"}):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")

        ai_guard_client.evaluate_prompt("user", "What is your name?")
        assert_mock_execute_request_call(
            mock_execute_request,
            ai_guard_client,
            [],
            Prompt(role="user", content="What is your name?"),
            {"service": "test-service", "env": "test-env"},
        )
