from itertools import product
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.appsec._constants import AI_GUARD
from ddtrace.appsec.ai_guard import AIGuardAbortError
from ddtrace.appsec.ai_guard import AIGuardClientError
from ddtrace.appsec.ai_guard import Function
from ddtrace.appsec.ai_guard import Message
from ddtrace.appsec.ai_guard import Options
from ddtrace.appsec.ai_guard import ToolCall
from ddtrace.appsec.ai_guard import new_ai_guard_client
from ddtrace.settings.asm import ai_guard_config
from tests.appsec.ai_guard.utils import assert_ai_guard_span
from tests.appsec.ai_guard.utils import assert_mock_execute_request_call
from tests.appsec.ai_guard.utils import find_ai_guard_span
from tests.appsec.ai_guard.utils import mock_evaluate_response
from tests.appsec.ai_guard.utils import random_string
from tests.utils import override_global_config


TOOL_CALL = [
    Message(role="system", content="You are a beautiful AI assistant"),
    Message(role="user", content="What is 2 + 2"),
    Message(
        role="assistant",
        tool_calls=[
            ToolCall(id="call_1", function=Function(name="calc", arguments='{ "operator": ' + ', "args": [2, 2] }'))
        ],
    ),
]

TOOL_OUTPUT = [
    *TOOL_CALL,
    Message(role="tool", tool_call_id="call_1", content="5"),
]

PROMPT = [
    *TOOL_OUTPUT,
    Message(role="assistant", content="2 + 2 is 5"),
    Message(role="user", content="Are you sure?"),
]


def _build_test_params():
    actions = [
        {"action": "ALLOW", "reason": "Go ahead"},
        {"action": "DENY", "reason": "Nope"},
        {"action": "ABORT", "reason": "Kill it with fire"},
    ]
    block = [True, False]
    suites = [
        {"suite": "tool call", "target": "tool", "messages": TOOL_CALL},
        {"suite": "tool output", "target": "tool", "messages": TOOL_OUTPUT},
        {"suite": "prompt", "target": "prompt", "messages": PROMPT},
    ]
    params = []
    for action, block, suite in product(actions, block, suites):
        test_id = f"{suite['suite']}: {action['action']} action | blocking: {block})"
        params.append(
            pytest.param(
                action["action"],
                action["reason"],
                block,
                suite["suite"],
                suite["target"],
                suite["messages"],
                id=test_id,
            )
        )
    return params


def assert_telemetry(mocked, metric, tags):
    metrics = [(args[0].value, args[1].value) + args[2:] for args, kwargs in mocked.add_metric.call_args_list]
    assert ("count", "appsec", metric, 1, tags) in metrics


@pytest.mark.parametrize("action,reason,blocking,suite,target,messages", _build_test_params())
@patch("ddtrace.internal.telemetry.telemetry_writer._namespace")
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_method(
    mock_execute_request, telemetry_mock, ai_guard_client, tracer, action, reason, blocking, suite, target, messages
):
    """Test different combinations of evaluations."""
    mock_execute_request.return_value = mock_evaluate_response(action, reason, blocking)
    should_block = blocking and action != "ALLOW"

    if should_block:
        with pytest.raises(AIGuardAbortError) as exc_info:
            ai_guard_client.evaluate(messages, Options(block=blocking))
        assert exc_info.value.action == action
        assert exc_info.value.reason == reason
    else:
        result = ai_guard_client.evaluate(messages, Options(block=blocking))
        assert result["action"] == action
        assert result["reason"] == reason

    expected_tags = {"ai_guard.target": target, "ai_guard.action": action}
    if target == "tool":
        expected_tags.update({"ai_guard.tool_name": "calc"})
    if action != "ALLOW" and blocking:
        expected_tags.update({"ai_guard.blocked": "true"})
    assert_ai_guard_span(
        tracer,
        messages,
        expected_tags,
    )
    assert_telemetry(
        telemetry_mock,
        "ai_guard.requests",
        (
            ("action", action),
            ("block", "true" if should_block else "false"),
            ("error", "false"),
        ),
    )
    assert_mock_execute_request_call(mock_execute_request, ai_guard_client, messages)


@patch("ddtrace.internal.telemetry.telemetry_writer._namespace")
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_http_error(mock_execute_request, telemetry_mock, ai_guard_client):
    """Test HTTP error handling."""
    errors = [{"status": "500", "title": "Internal server error"}]
    mock_response = Mock()
    mock_response.status = 500
    mock_response.get_json.return_value = {"errors": errors}
    mock_execute_request.return_value = mock_response

    with pytest.raises(AIGuardClientError) as exc_info:
        ai_guard_client.evaluate(TOOL_CALL)

    assert exc_info.value.status == 500
    assert exc_info.value.errors == errors
    assert_telemetry(telemetry_mock, "ai_guard.requests", (("error", "true"),))


@patch("ddtrace.internal.telemetry.telemetry_writer._namespace")
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_invalid_json(mock_execute_request, telemetry_mock, ai_guard_client):
    """Test invalid JSON response handling."""
    mock_response = Mock()
    mock_response.status = 200
    mock_response.get_json.side_effect = Exception("Invalid JSON")
    mock_execute_request.return_value = mock_response

    with pytest.raises(AIGuardClientError) as exc_info:
        ai_guard_client.evaluate(TOOL_CALL)

    assert str(exc_info.value) == "Unexpected error calling AI Guard service: Invalid JSON"
    assert_telemetry(telemetry_mock, "ai_guard.requests", (("error", "true"),))


@patch("ddtrace.internal.telemetry.telemetry_writer._namespace")
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_malformed_response(mock_execute_request, telemetry_mock, ai_guard_client):
    """Test malformed response structure handling."""
    mock_response = Mock()
    mock_response.status = 200
    mock_response.get_json.return_value = {"invalid": "structure"}
    mock_execute_request.return_value = mock_response

    with pytest.raises(AIGuardClientError) as exc_info:
        ai_guard_client.evaluate(TOOL_CALL)

    assert str(exc_info.value).startswith("AI Guard service returned unexpected response format")
    assert_telemetry(telemetry_mock, "ai_guard.requests", (("error", "true"),))


@patch("ddtrace.internal.telemetry.telemetry_writer._namespace")
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_evaluate_invalid_action(mock_execute_request, telemetry_mock, ai_guard_client):
    """Test invalid action handling."""
    mock_execute_request.return_value = mock_evaluate_response("GO_TO_SLEEP")

    with pytest.raises(AIGuardClientError) as exc_info:
        ai_guard_client.evaluate(TOOL_CALL)

    assert (
        str(exc_info.value)
        == "AI Guard service returned unrecognized action: 'GO_TO_SLEEP'. Expected ['ALLOW', 'DENY', 'ABORT']"
    )
    assert_telemetry(telemetry_mock, "ai_guard.requests", (("error", "true"),))


@patch("ddtrace.internal.telemetry.telemetry_writer._namespace")
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_span_meta_messages_truncation(mock_execute_request, telemetry_mock, ai_guard_client, tracer):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    messages = []
    for i in range(ai_guard_config._ai_guard_max_messages_length + 1):
        messages.append(Message(role="user", content="Tell me 10 things I should know about DataDog"))
    ai_guard_client.evaluate(messages)

    span = find_ai_guard_span(tracer)
    meta = span._get_struct_tag(AI_GUARD.TAG)
    assert len(meta["messages"]) == ai_guard_config._ai_guard_max_messages_length
    assert_telemetry(telemetry_mock, "ai_guard.truncated", (("type", "messages"),))


@patch("ddtrace.internal.telemetry.telemetry_writer._namespace")
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_span_meta_content_truncation(mock_execute_request, telemetry_mock, ai_guard_client, tracer):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    random_output = random_string(ai_guard_config._ai_guard_max_content_size + 1)

    ai_guard_client.evaluate([Message(role="user", content=random_output)])

    span = find_ai_guard_span(tracer)
    meta = span._get_struct_tag(AI_GUARD.TAG)
    prompt = meta["messages"][0]
    assert len(prompt["content"]) == ai_guard_config._ai_guard_max_content_size
    assert_telemetry(telemetry_mock, "ai_guard.truncated", (("type", "content"),))


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_meta_attribute(mock_execute_request):
    messages = [Message(role="user", content="What is your name?")]
    with override_global_config(
        {
            "service": "test-service",
            "env": "test-env",
            "_dd_api_key": "test-api-key",
            "_dd_app_key": "test-application-key",
        }
    ):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")

        ai_guard_client = new_ai_guard_client()
        ai_guard_client.evaluate(messages)
        assert_mock_execute_request_call(
            mock_execute_request,
            ai_guard_client,
            messages,
            {"service": "test-service", "env": "test-env"},
        )
