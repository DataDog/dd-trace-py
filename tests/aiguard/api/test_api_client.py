from itertools import product
import json
from unittest.mock import Mock
from unittest.mock import call
from unittest.mock import patch

import pytest

from ddtrace.aiguard import MCP
from ddtrace.aiguard import AIGuardAbortError
from ddtrace.aiguard import AIGuardClient
from ddtrace.aiguard import AIGuardClientError
from ddtrace.aiguard import ContentPart
from ddtrace.aiguard import Function
from ddtrace.aiguard import ImageURL
from ddtrace.aiguard import MCPServer
from ddtrace.aiguard import Message
from ddtrace.aiguard import Options
from ddtrace.aiguard import ToolCall
from ddtrace.aiguard import new_ai_guard_client
from ddtrace.aiguard._api_client import _agent_route
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.internal.http import HTTPConnection
from ddtrace.internal.native import ConnectionFailedError
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings.aiguard import ENV_TIMEOUT
from ddtrace.internal.settings.aiguard import AIGuardConfig
from ddtrace.internal.settings.aiguard import aiguard_config
from ddtrace.internal.telemetry import TELEMETRY_NAMESPACE
from ddtrace.internal.utils.time import HourGlass
from tests.aiguard.utils import assert_ai_guard_span
from tests.aiguard.utils import assert_mock_execute_request_call
from tests.aiguard.utils import find_ai_guard_span
from tests.aiguard.utils import mock_evaluate_response
from tests.aiguard.utils import override_ai_guard_config
from tests.aiguard.utils import random_string
from tests.utils import override_env
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
    Message(
        role="user",
        content=[
            ContentPart(type="input_text", text="what's in this image?"),
            ContentPart(
                type="input_image",
                image_url=ImageURL(
                    url=(
                        "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/"
                        "Gfp-wisconsin-madison-the-nature-boardwalk.jpg/2560px-"
                        "Gfp-wisconsin-madison-the-nature-boardwalk.jpg"
                    )
                ),
            ),
        ],
    ),
]


def _build_test_params():
    actions = [
        {"action": "ALLOW", "reason": "Go ahead", "tags": []},
        {"action": "DENY", "reason": "Nope", "tags": ["deny_everything", "test_deny"]},
        {"action": "ABORT", "reason": "Kill it with fire", "tags": ["alarm_tag", "abort_everything"]},
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
                action["tags"],
                block,
                suite["suite"],
                suite["target"],
                suite["messages"],
                id=test_id,
            )
        )
    return params


def assert_telemetry(mocked, metric, tags, source=AI_GUARD.SOURCE_SDK, integration=AI_GUARD.INTEGRATION_NONE, extra=()):
    """Assert one metric was emitted. Every AI Guard metric ends with the call-path tags, which
    default here to a direct SDK call; extra carries the tags the error metric appends after them.
    """
    metrics = [(args[0].value,) + args[1:] for args, kwargs in mocked.call_args_list]
    if metric == "requests":
        # Every evaluation that was not skipped by a middleware reports skipped=false.
        tags = tuple(tags) + (("skipped", "false"),)
    expected_tags = tuple(tags) + (("source", source), ("integration", integration)) + tuple(extra)
    assert ("ai_guard", metric, 1, expected_tags) in metrics


@pytest.mark.parametrize("action,reason,tags,blocking,suite,target,messages", _build_test_params())
@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_method(
    mock_execute_request,
    telemetry_mock,
    ai_guard_client,
    test_spans,
    action,
    reason,
    tags,
    blocking,
    suite,
    target,
    messages,
):
    """Test different combinations of evaluations."""
    mock_execute_request.return_value = mock_evaluate_response(action, reason, tags, blocking)
    should_block = blocking and action != "ALLOW"

    if should_block:
        with pytest.raises(AIGuardAbortError) as exc_info:
            ai_guard_client.evaluate(messages, Options(block=blocking))
        assert exc_info.value.action == action
        assert exc_info.value.reason == reason
        assert exc_info.value.tags == tags
        assert exc_info.value.sds == []
    else:
        result = ai_guard_client.evaluate(messages, Options(block=blocking))
        assert result["action"] == action
        assert result["reason"] == reason
        if tags:
            assert result["tags"] == tags
        assert result["sds"] == []

    expected_tags = {"ai_guard.target": target, "ai_guard.action": action, "ai_guard.redacted": "false"}
    if target == "tool":
        expected_tags.update({"ai_guard.tool_name": "calc"})
    if action != "ALLOW" and blocking:
        expected_tags.update({"ai_guard.blocked": "true"})
    expected_meta_struct = {"messages": messages}
    if tags:
        expected_meta_struct.update({"attack_categories": tags})
    assert_ai_guard_span(
        test_spans,
        expected_tags,
        expected_meta_struct,
    )
    assert_telemetry(
        telemetry_mock,
        "requests",
        (
            ("action", action),
            ("block", "true" if should_block else "false"),
            ("error", "false"),
            ("redacted", "false"),
        ),
    )
    assert_mock_execute_request_call(
        mock_execute_request, ai_guard_client, messages, endpoint="https://api.example.com/ai-guard"
    )


@pytest.mark.parametrize(
    "options,should_block",
    [
        pytest.param(None, True, id="options omitted follows remote blocking"),
        pytest.param(Options(), True, id="block omitted follows remote blocking"),
        pytest.param(Options(block=False), False, id="explicit block false disables blocking"),
    ],
)
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_block_defaults_to_remote_is_blocking_enabled(
    mock_execute_request, ai_guard_client, options, should_block
):
    mock_execute_request.return_value = mock_evaluate_response("DENY", "Nope", ["deny_everything"], True)

    if should_block:
        with pytest.raises(AIGuardAbortError):
            ai_guard_client.evaluate(TOOL_CALL, options)
    else:
        result = ai_guard_client.evaluate(TOOL_CALL, options)
        assert result["action"] == "DENY"


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
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
    assert_telemetry(telemetry_mock, "requests", (("error", "true"),))
    assert_telemetry(
        telemetry_mock, "error", (("type", "bad_status"),), extra=(("http_status", str(mock_response.status)),)
    )


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_http_error_empty_json_body(mock_execute_request, telemetry_mock, ai_guard_client):
    """Test HTTP error handling when the response body is empty."""
    mock_response = Mock()
    mock_response.status = 500
    mock_response.get_json.return_value = None
    mock_execute_request.return_value = mock_response

    with pytest.raises(AIGuardClientError) as exc_info:
        ai_guard_client.evaluate(TOOL_CALL)

    assert str(exc_info.value) == "AI Guard service call failed, status: 500"
    assert exc_info.value.status == 500
    assert exc_info.value.errors == []
    assert_telemetry(telemetry_mock, "requests", (("error", "true"),))
    assert_telemetry(
        telemetry_mock, "error", (("type", "bad_status"),), extra=(("http_status", str(mock_response.status)),)
    )


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_invalid_json(mock_execute_request, telemetry_mock, ai_guard_client):
    """Test invalid JSON response handling."""
    mock_response = Mock()
    mock_response.status = 200
    mock_response.get_json.side_effect = Exception("Invalid JSON")
    mock_execute_request.return_value = mock_response

    with pytest.raises(AIGuardClientError) as exc_info:
        ai_guard_client.evaluate(TOOL_CALL)

    assert str(exc_info.value) == "AI Guard service returned an undecodable response body: Invalid JSON"
    assert exc_info.value.status == 200
    assert_telemetry(telemetry_mock, "requests", (("error", "true"),))
    assert_telemetry(telemetry_mock, "error", (("type", "bad_response"),))


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_invalid_json_error_status(mock_execute_request, telemetry_mock, ai_guard_client):
    """An undecodable body on a failing status is reported as bad_status, not bad_response."""
    mock_response = Mock()
    mock_response.status = 502
    mock_response.get_json.side_effect = Exception("Invalid JSON")
    mock_execute_request.return_value = mock_response

    with pytest.raises(AIGuardClientError) as exc_info:
        ai_guard_client.evaluate(TOOL_CALL)

    assert exc_info.value.status == 502
    assert_telemetry(telemetry_mock, "requests", (("error", "true"),))
    assert_telemetry(
        telemetry_mock, "error", (("type", "bad_status"),), extra=(("http_status", str(mock_response.status)),)
    )


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_transport_failure(mock_execute_request, telemetry_mock, ai_guard_client):
    """A failure reaching the service reports which kind of transport failure it was."""
    mock_execute_request.side_effect = ConnectionFailedError("Connection refused")

    with pytest.raises(AIGuardClientError) as exc_info:
        ai_guard_client.evaluate(TOOL_CALL)

    assert str(exc_info.value) == (
        "Unexpected error calling AI Guard service (ConnectionFailedError): Connection refused"
    )
    assert_telemetry(telemetry_mock, "requests", (("error", "true"),))
    assert_telemetry(telemetry_mock, "error", (("type", AI_GUARD.ERROR_CONNECTION),))


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_malformed_response(mock_execute_request, telemetry_mock, ai_guard_client):
    """Test malformed response structure handling."""
    mock_response = Mock()
    mock_response.status = 200
    mock_response.get_json.return_value = {"invalid": "structure"}
    mock_execute_request.return_value = mock_response

    with pytest.raises(AIGuardClientError) as exc_info:
        ai_guard_client.evaluate(TOOL_CALL)

    assert str(exc_info.value).startswith("AI Guard service returned unexpected response format")
    assert_telemetry(telemetry_mock, "requests", (("error", "true"),))
    assert_telemetry(telemetry_mock, "error", (("type", "bad_response"),))


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_without_registered_tracer(mock_execute_request, ai_guard_client):
    """With no tracer on the core registry the verdict can't be reported, so evaluate fails before the request."""
    with patch("ddtrace.aiguard._api_client.core") as core_mock:
        core_mock.root.get_item.return_value = None
        with pytest.raises(AIGuardClientError, match="requires the global tracer"):
            ai_guard_client.evaluate(TOOL_CALL)

    core_mock.root.get_item.assert_called_once_with("tracer")
    mock_execute_request.assert_not_called()


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_invalid_action(mock_execute_request, telemetry_mock, ai_guard_client):
    """Test invalid action handling."""
    mock_execute_request.return_value = mock_evaluate_response("GO_TO_SLEEP")

    with pytest.raises(AIGuardClientError) as exc_info:
        ai_guard_client.evaluate(TOOL_CALL)

    assert (
        str(exc_info.value)
        == "AI Guard service returned unrecognized action: 'GO_TO_SLEEP'. Expected ['ALLOW', 'DENY', 'ABORT']"
    )
    assert_telemetry(telemetry_mock, "requests", (("error", "true"),))
    assert_telemetry(telemetry_mock, "error", (("type", "bad_response"),))


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_custom_tags(mock_execute_request, ai_guard_client, test_spans):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    ai_guard_client.evaluate(PROMPT, tags={"customer.tenant": "acme", "customer.tier": "gold"})

    span = find_ai_guard_span(test_spans)
    assert span.get_tag("customer.tenant") == "acme"
    assert span.get_tag("customer.tier") == "gold"


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_custom_tags_ignore_reserved_ai_guard_keys(mock_execute_request, ai_guard_client, test_spans):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    ai_guard_client.evaluate(
        PROMPT,
        tags={
            AI_GUARD.ACTION_TAG: "DENY",
            AI_GUARD.TARGET_TAG: "tool",
            AI_GUARD.BLOCKED_TAG: "true",
            AI_GUARD.TOOL_NAME_TAG: "fake_tool",
            "ai_guard.custom": "value",
            "customer.tenant": "acme",
        },
    )

    span = find_ai_guard_span(test_spans)
    assert span.get_tag(AI_GUARD.ACTION_TAG) == "ALLOW"
    assert span.get_tag(AI_GUARD.TARGET_TAG) == "prompt"
    assert span.get_tag(AI_GUARD.BLOCKED_TAG) is None
    assert span.get_tag(AI_GUARD.TOOL_NAME_TAG) is None
    assert span.get_tag("ai_guard.custom") is None
    assert span.get_tag("customer.tenant") == "acme"


@pytest.mark.parametrize("max_messages", [0, 1, 16])
@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_span_meta_messages_truncation(mock_execute_request, telemetry_mock, ai_guard_client, test_spans, max_messages):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    messages = []
    for i in range(max_messages + 1):
        messages.append(Message(role="user", content=f"Message {i}"))
    with override_ai_guard_config(dict(_ai_guard_max_messages_length=max_messages)):
        evaluation = ai_guard_client.evaluate(messages)

    span = find_ai_guard_span(test_spans)
    meta = span._get_struct_tag(AI_GUARD.TAG)
    assert len(meta["messages"]) == max_messages
    assert meta["messages"] == messages[1:]
    assert_mock_execute_request_call(mock_execute_request, ai_guard_client, messages)
    assert evaluation["messages"] is messages
    assert_telemetry(telemetry_mock, "truncated", (("type", "messages"),))


@pytest.mark.parametrize("content_part", [True, False])
@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_span_meta_content_truncation(mock_execute_request, telemetry_mock, ai_guard_client, test_spans, content_part):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    random_output = random_string(aiguard_config._ai_guard_max_content_size + 1)
    if content_part:
        messages = [Message(role="user", content=[ContentPart(type="text", text=random_output)])]
    else:
        messages = [Message(role="user", content=random_output)]
    ai_guard_client.evaluate(messages)

    span = find_ai_guard_span(test_spans)
    meta = span._get_struct_tag(AI_GUARD.TAG)
    prompt = meta["messages"][0]
    content = prompt["content"]
    if content_part:
        content = content[0]["text"]
    assert len(content) == aiguard_config._ai_guard_max_content_size
    assert_telemetry(telemetry_mock, "truncated", (("type", "content"),))


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_message_immutability(mock_execute_request, telemetry_mock, ai_guard_client, tracer, test_spans):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    messages = [
        Message(role="assistant", tool_calls=[ToolCall(id="call_1", function=Function(name="test", arguments="{}"))])
    ]
    with tracer.trace("test"):
        ai_guard_client.evaluate(messages)
        # Update messages before being flushed
        messages[0].get("tool_calls").append(ToolCall(id="call_2", function=Function(name="test", arguments="{}")))
        messages.append(
            Message(
                role="assistant", tool_calls=[ToolCall(id="call_2", function=Function(name="test", arguments="{}"))]
            )
        )

    span = test_spans.spans[1]  # AI Guard span
    meta = span._get_struct_tag(AI_GUARD.TAG)
    messages = meta["messages"]
    assert len(messages) == 1
    assert len(messages[0]["tool_calls"]) == 1


# ---------------------------------------------------------------------------
# source / integration telemetry tags (APPSEC-69866)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,expected_source,expected_integration",
    [
        pytest.param({}, "sdk", "none", id="defaults to a direct sdk call"),
        pytest.param(
            {"source": AI_GUARD.SOURCE_AUTO, "integration": AI_GUARD.INTEGRATION_OPENAI},
            "auto",
            "openai",
            id="auto instrumentation reports its package",
        ),
        pytest.param(
            {"source": AI_GUARD.SOURCE_SDK, "integration": AI_GUARD.INTEGRATION_OPENAI},
            "sdk",
            "none",
            id="sdk source pins integration to none",
        ),
    ],
)
@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_reports_call_path_tags(
    mock_execute_request, telemetry_mock, ai_guard_client, kwargs, expected_source, expected_integration
):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    ai_guard_client.evaluate(TOOL_CALL, **kwargs)

    assert_telemetry(
        telemetry_mock,
        "requests",
        (("action", "ALLOW"), ("block", "false"), ("error", "false"), ("redacted", "false")),
        source=expected_source,
        integration=expected_integration,
    )


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_error_metrics_report_call_path_tags(mock_execute_request, telemetry_mock, ai_guard_client):
    """A failed auto-instrumented evaluation still attributes both the requests and error metrics."""
    mock_execute_request.side_effect = ConnectionFailedError("Connection refused")

    with pytest.raises(AIGuardClientError):
        ai_guard_client.evaluate(TOOL_CALL, source=AI_GUARD.SOURCE_AUTO, integration=AI_GUARD.INTEGRATION_ANTHROPIC)

    assert_telemetry(telemetry_mock, "requests", (("error", "true"),), source="auto", integration="anthropic")
    assert_telemetry(
        telemetry_mock, "error", (("type", AI_GUARD.ERROR_CONNECTION),), source="auto", integration="anthropic"
    )


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_truncated_metric_reports_call_path_tags(mock_execute_request, telemetry_mock, ai_guard_client):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    messages = [
        Message(role="user", content="Tell me 10 things I should know about DataDog")
        for _ in range(aiguard_config._ai_guard_max_messages_length + 1)
    ]
    ai_guard_client.evaluate(messages, source=AI_GUARD.SOURCE_AUTO, integration=AI_GUARD.INTEGRATION_LANGCHAIN)

    assert_telemetry(telemetry_mock, "truncated", (("type", "messages"),), source="auto", integration="langchain")


@pytest.mark.parametrize(
    "sds_findings",
    [
        pytest.param(
            [
                {
                    "rule_display_name": "Credit Card Number",
                    "rule_tag": "credit_card",
                    "category": "pii",
                    "matched_text": "4111111111111111",
                    "location": {
                        "start_index": 10,
                        "end_index_exclusive": 26,
                        "path": "messages[0].content[0].text",
                    },
                }
            ],
            id="single finding",
        ),
        pytest.param(
            [
                {
                    "rule_display_name": "Credit Card Number",
                    "rule_tag": "credit_card",
                    "category": "pii",
                    "matched_text": "4111111111111111",
                    "location": {
                        "start_index": 10,
                        "end_index_exclusive": 26,
                        "path": "messages[0].content[0].text",
                    },
                },
                {
                    "rule_display_name": "Social Security Number",
                    "rule_tag": "ssn",
                    "category": "pii",
                    "matched_text": "123-45-6789",
                    "location": {
                        "start_index": 30,
                        "end_index_exclusive": 41,
                        "path": "messages[1].tool_calls[0].function.arguments",
                    },
                },
            ],
            id="multiple findings",
        ),
    ],
)
@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_sds_findings(mock_execute_request, telemetry_mock, ai_guard_client, test_spans, sds_findings):
    """Test that sds_findings from the response are added to the span meta_struct and SDK response."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW", sds_findings=sds_findings)

    result = ai_guard_client.evaluate(PROMPT)

    assert result["sds"] == sds_findings

    expected_meta_struct = {"messages": PROMPT, "sds": sds_findings}
    assert_ai_guard_span(
        test_spans,
        {"ai_guard.target": "prompt", "ai_guard.action": "ALLOW"},
        expected_meta_struct,
    )


@pytest.mark.parametrize(
    "sds_findings",
    [
        pytest.param(None, id="absent"),
        pytest.param([], id="empty list"),
    ],
)
@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_sds_findings_empty(mock_execute_request, telemetry_mock, ai_guard_client, test_spans, sds_findings):
    """Test that empty or absent sds_findings are not added to the span meta_struct."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW", sds_findings=sds_findings)

    result = ai_guard_client.evaluate(PROMPT)

    assert result["sds"] == (sds_findings if sds_findings else [])

    span = find_ai_guard_span(test_spans)
    meta = span._get_struct_tag(AI_GUARD.TAG)
    assert "sds" not in meta


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_sds_findings_in_abort_error(mock_execute_request, telemetry_mock, ai_guard_client, test_spans):
    """Test that sds_findings are included in AIGuardAbortError."""
    sds_findings = [
        {
            "rule_display_name": "Credit Card Number",
            "rule_tag": "credit_card",
            "category": "pii",
            "matched_text": "4111111111111111",
            "location": {
                "start_index": 10,
                "end_index_exclusive": 26,
                "path": "messages[0].content[0].text",
            },
        }
    ]
    mock_execute_request.return_value = mock_evaluate_response("ABORT", sds_findings=sds_findings)

    with pytest.raises(AIGuardAbortError) as exc_info:
        ai_guard_client.evaluate(PROMPT, Options(block=True))

    assert exc_info.value.sds == sds_findings


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_tag_probabilities_in_result_and_meta_struct(mock_execute_request, ai_guard_client, test_spans):
    """Test that tag probabilities are added to the SDK response and span meta_struct."""
    tag_probs = {"jailbreak": 0.91, "prompt_injection": 0.42}
    mock_execute_request.return_value = mock_evaluate_response(
        "DENY", reason="Nope", tags=["jailbreak"], block=False, tag_probs=tag_probs
    )

    result = ai_guard_client.evaluate(PROMPT, Options(block=False))

    assert result["tag_probs"] == tag_probs

    expected_meta_struct = {"messages": PROMPT, "attack_categories": ["jailbreak"], "tag_probs": tag_probs}
    assert_ai_guard_span(
        test_spans,
        {"ai_guard.target": "prompt", "ai_guard.action": "DENY", "ai_guard.reason": "Nope"},
        expected_meta_struct,
    )


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_tag_probabilities_in_abort_error(mock_execute_request, ai_guard_client):
    """Test that tag probabilities are included in AIGuardAbortError."""
    tag_probs = {"jailbreak": 0.91}
    mock_execute_request.return_value = mock_evaluate_response(
        "ABORT", reason="blocked", tags=["jailbreak"], tag_probs=tag_probs
    )

    with pytest.raises(AIGuardAbortError) as exc_info:
        ai_guard_client.evaluate(PROMPT, Options(block=True))

    assert exc_info.value.tag_probs == tag_probs


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
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


@pytest.mark.parametrize(
    "site,config,param,expected",
    [
        pytest.param(
            "datadoghq.com",
            "",
            "",
            "https://app.datadoghq.com/api/v2/ai-guard",
        ),
        pytest.param(
            "ap1.datadoghq.com",
            "",
            "",
            "https://ap1.datadoghq.com/api/v2/ai-guard",
        ),
        pytest.param(
            "datadoghq.com",
            "https://from-config",
            "",
            "https://from-config",
        ),
        pytest.param(
            "datadoghq.com",
            "",
            "https://from-param",
            "https://from-param",
        ),
        pytest.param(
            "datadoghq.com",
            "https://from-config",
            "https://from-param",
            "https://from-param",
        ),
    ],
)
def test_endpoint_discovery(site, config, param, expected):
    with override_global_config(
        dict(
            _dd_site=site,
            _dd_api_key="test-api-key",
            _dd_app_key="test-app-key",
        )
    ):
        with override_ai_guard_config(dict(_ai_guard_endpoint=config)):
            client = new_ai_guard_client(endpoint=param)
            assert client._endpoint == expected


def test_execute_request_preserves_port_and_query_string(ai_guard_client):
    """_execute_request must not force a default port into the connection's Host header, and
    must not silently drop the query string from the target URL.
    """
    created_connections = []

    class MockHTTPConnection:
        def __init__(self, base_url, timeout=None):
            self.base_url = base_url
            self.requests: list = []
            created_connections.append(self)

        def request(self, method, path, body=None, headers=None):
            self.requests.append({"method": method, "path": path})

        def getresponse(self):
            return Mock(status=200, read=lambda: b'{"action": "ALLOW", "tags": []}')

        def close(self):
            pass

    with patch("ddtrace.aiguard._api_client.HTTPConnection", MockHTTPConnection):
        ai_guard_client._execute_request("https://example.com:9443/ai-guard/evaluate?resource=abc", {})

    assert len(created_connections) == 1
    conn = created_connections[0]
    assert conn.base_url == "https://example.com:9443"
    assert conn.requests[0]["path"] == "/ai-guard/evaluate?resource=abc"


@pytest.mark.parametrize(
    "raw_timeout, expected_timeout_ms",
    [
        ("10000", 10_000),
        # Floored to whole seconds, as documented.
        ("1500", 1_000),
        # Would floor to 0, so the default applies.
        ("500", 10_000),
        ("0", 10_000),
        ("-1", 10_000),
    ],
)
def test_configured_timeout_reaching_the_transport(raw_timeout, expected_timeout_ms):
    created_connections = []

    class RecordingHTTPConnection(HTTPConnection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created_connections.append(self)

        def request(self, *args, **kwargs):
            raise ConnectionFailedError("refused")

    with override_env({ENV_TIMEOUT: raw_timeout}):
        timeout = AIGuardConfig()._ai_guard_timeout

    with override_ai_guard_config(
        dict(
            _ai_guard_timeout=timeout,
            _dd_api_key="test-api-key",
            _dd_app_key="test-app-key",
        )
    ):
        client = new_ai_guard_client(endpoint="https://api.example.com/ai-guard")

    with patch("ddtrace.aiguard._api_client.HTTPConnection", RecordingHTTPConnection):
        with pytest.raises(ConnectionFailedError):
            client._execute_request("https://api.example.com/ai-guard/evaluate", {})

    assert created_connections[0]._timeout_ms == expected_timeout_ms


@pytest.mark.parametrize("raw_timeout", ["999", "500", "0", "-1"])
def test_timeout_flooring_to_zero_warns_and_uses_the_default(raw_timeout):
    with override_env({ENV_TIMEOUT: raw_timeout}):
        with patch("ddtrace.internal.settings.aiguard.log") as log:
            config = AIGuardConfig()

    assert config._ai_guard_timeout == 10_000
    log.warning.assert_called_once()
    assert ENV_TIMEOUT in log.warning.call_args[0]


@pytest.mark.parametrize("raw_timeout", ["1000", "1500"])
def test_timeout_of_at_least_one_second_is_kept_without_warning(raw_timeout):
    with override_env({ENV_TIMEOUT: raw_timeout}):
        with patch("ddtrace.internal.settings.aiguard.log") as log:
            config = AIGuardConfig()

    assert config._ai_guard_timeout == int(raw_timeout)
    log.warning.assert_not_called()


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_event_tag_in_root_span(mock_execute_request, ai_guard_client, tracer):
    """Test that AI Guard event tag is set on the root span of the trace."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW", reason="It's fine")

    with tracer.trace("root_span") as root_span:
        ai_guard_client.evaluate(PROMPT, Options(block=False))

    assert root_span.get_tag(AI_GUARD.EVENT_TAG) == "true"


@pytest.mark.parametrize("mcp_servers", [None, []], ids=["none", "empty"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_omits_empty_mcp_servers(mock_execute_request, ai_guard_client, mcp_servers):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    ai_guard_client.evaluate([Message(role="user", content="hi")], mcp_servers=mcp_servers)

    assert set(mock_execute_request.call_args.args[1]["data"]["attributes"]) == {"messages", "meta"}


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_sends_mcp_servers(mock_execute_request, ai_guard_client):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    server = MCPServer(
        id="mcp_b77bc22583ac5006",
        managed_by="client",
        transport="streamable_http",
        tools={"wiki_search": "search"},
        url="https://mcp.example.com",
    )
    tool_call = ToolCall(id="call_1", function=Function(name="wiki_search", arguments="{}"))
    tool_call["mcp"] = MCP(server_id=server["id"])
    messages = [Message(role="user", content="hi"), Message(role="assistant", tool_calls=[tool_call])]

    ai_guard_client.evaluate(messages, mcp_servers=[server])

    attributes = mock_execute_request.call_args.args[1]["data"]["attributes"]
    assert attributes["mcp_servers"] == [server]
    assert attributes["messages"][-1]["tool_calls"][0]["mcp"] == {"server_id": "mcp_b77bc22583ac5006"}


def _agent_requests_calls(telemetry_mock):
    return [
        call
        for call in telemetry_mock.call_args_list
        if call.args[0] == TELEMETRY_NAMESPACE.AI_GUARD and call.args[1] == AI_GUARD.AGENT_REQUESTS_METRIC
    ]


class RecordingConnection:
    """Stands in for the native HTTP connection, recording what the client sent."""

    instances: list = []

    status = 200
    body = b'{"data": {"attributes": {"action": "ALLOW", "tags": []}}}'
    response_headers: dict = {}

    def __init__(self, base_url, timeout=None):
        self.base_url = base_url
        self.requests: list = []
        type(self).instances.append(self)

    def request(self, method, path, body=None, headers=None):
        self.requests.append({"method": method, "path": path, "body": body, "headers": headers})

    def getresponse(self):
        response = Mock(status=self.status, read=lambda: self.body)
        response.getheader = lambda name: self.response_headers.get(name)
        return response

    def close(self):
        pass


@pytest.fixture
def recording_connection():
    class Recorder(RecordingConnection):
        instances: list = []
        response_headers: dict = {}

    with patch("ddtrace.aiguard._api_client.HTTPConnection", Recorder):
        yield Recorder


@pytest.mark.parametrize(
    "agent_url, expected_base",
    [
        ("http://agent:8126", "http://agent:8126"),
        ("http://agent:8126/", "http://agent:8126"),
        ("unix:///var/run/datadog/apm.socket", "unix:///var/run/datadog/apm.socket"),
    ],
)
def test_agent_transport_reaches_the_proxy_route(agent_url, expected_base, recording_connection):
    """Without credentials the evaluation goes to the Agent route, keys left to the Agent."""
    with override_global_config(dict(_dd_site="datadoghq.com", _dd_api_key="", _dd_app_key="")):
        with patch("ddtrace.internal.agent.info", return_value={"endpoints": [AI_GUARD.AGENT_PROXY_ENDPOINT]}):
            with patch.object(agent_config, "trace_agent_url", agent_url):
                client = new_ai_guard_client()
                client.evaluate([Message(role="user", content="hi")])

    assert "DD-API-KEY" not in client._headers
    assert "DD-APPLICATION-KEY" not in client._headers
    assert len(recording_connection.instances) == 1
    sent = recording_connection.instances[0].requests[0]
    assert recording_connection.instances[0].base_url == expected_base
    assert sent["method"] == "POST"
    assert sent["path"] == "/ai_guard/proxy/api/v2/ai-guard/evaluate"
    assert "DD-API-KEY" not in sent["headers"]
    assert "DD-APPLICATION-KEY" not in sent["headers"]
    assert sent["headers"]["DD-AI-GUARD-SOURCE"] == "SDK"
    assert json.loads(sent["body"])["data"]["attributes"]["messages"] == [{"role": "user", "content": "hi"}]


def test_setup_without_keys_or_agent_route_still_returns_a_client():
    """AI Guard must still install when the Agent is not ready; only evaluations fail."""
    with override_global_config(dict(_dd_site="datadoghq.com", _dd_api_key="", _dd_app_key="")):
        with patch("ddtrace.internal.agent.info", return_value={"endpoints": []}):
            client = new_ai_guard_client()

            with pytest.raises(AIGuardClientError, match="does not advertise"):
                client.evaluate([Message(role="user", content="hi")])


def test_absent_route_is_rechecked_at_most_once_per_interval(recording_connection):
    """A route that appears after startup is picked up, without polling /info per evaluation."""
    messages = [Message(role="user", content="hi")]
    with override_global_config(dict(_dd_site="datadoghq.com", _dd_api_key="", _dd_app_key="")):
        with patch("ddtrace.internal.agent.info", return_value={"endpoints": []}) as absent:
            with patch.object(agent_config, "trace_agent_url", "http://agent:8126"):
                client = new_ai_guard_client()
                for _ in range(3):
                    with pytest.raises(AIGuardClientError):
                        client.evaluate(messages)

                assert absent.call_count == 1

        # Once the interval elapses the Agent is checked again, and the route is found.
        with patch("ddtrace.internal.agent.info", return_value={"endpoints": [AI_GUARD.AGENT_PROXY_ENDPOINT]}):
            with patch.object(agent_config, "trace_agent_url", "http://agent:8126"):
                with patch.object(_agent_route, "_throttle", HourGlass(duration=0)):
                    client.evaluate(messages)

    assert recording_connection.instances[0].base_url == "http://agent:8126"


def test_unversioned_404_sends_the_client_back_to_discovery(recording_connection):
    """A 404 without the Agent's version header means the route is gone."""
    recording_connection.status = 404
    recording_connection.body = b'{"errors": ["not found"]}'
    messages = [Message(role="user", content="hi")]

    with override_global_config(dict(_dd_site="datadoghq.com", _dd_api_key="", _dd_app_key="")):
        with patch("ddtrace.internal.agent.info", return_value={"endpoints": [AI_GUARD.AGENT_PROXY_ENDPOINT]}):
            with patch.object(agent_config, "trace_agent_url", "http://agent:8126"):
                client = new_ai_guard_client()
                with pytest.raises(AIGuardClientError, match="no longer serves"):
                    client.evaluate(messages)

                # The route was forgotten, so the next evaluation has to discover again.
                with pytest.raises(AIGuardClientError, match="no longer advertises"):
                    client.evaluate(messages)


def test_versioned_404_stays_an_ordinary_service_error(recording_connection):
    """A 404 from the Agent route itself is the API's answer, not a vanished route."""
    recording_connection.status = 404
    recording_connection.body = b'{"errors": ["unknown resource"]}'
    recording_connection.response_headers = {AI_GUARD.AGENT_VERSION_HEADER: "7.79.0"}

    with override_global_config(dict(_dd_site="datadoghq.com", _dd_api_key="", _dd_app_key="")):
        with patch("ddtrace.internal.agent.info", return_value={"endpoints": [AI_GUARD.AGENT_PROXY_ENDPOINT]}):
            with patch.object(agent_config, "trace_agent_url", "http://agent:8126"):
                client = new_ai_guard_client()
                with pytest.raises(AIGuardClientError, match="service call failed"):
                    client.evaluate([Message(role="user", content="hi")])


@pytest.mark.parametrize("status", [401, 403])
def test_agent_mode_credential_errors_name_the_agent(status, recording_connection):
    """401 and 403 in Agent mode are the Agent's credentials, not the service's."""
    recording_connection.status = status
    recording_connection.body = b'{"errors": ["forbidden"]}'
    recording_connection.response_headers = {AI_GUARD.AGENT_VERSION_HEADER: "7.79.0"}

    with override_global_config(dict(_dd_site="datadoghq.com", _dd_api_key="", _dd_app_key="")):
        with patch("ddtrace.internal.agent.info", return_value={"endpoints": [AI_GUARD.AGENT_PROXY_ENDPOINT]}):
            with patch.object(agent_config, "trace_agent_url", "http://agent:8126"):
                client = new_ai_guard_client()
                with pytest.raises(AIGuardClientError, match="Agent's credentials") as raised:
                    client.evaluate([Message(role="user", content="hi")])

    assert raised.value.status == status
    assert raised.value.errors == ["forbidden"]


def test_endpoint_override_is_not_evidence_of_agent_support(recording_connection):
    """An endpoint override names a direct endpoint, which a keyless client cannot call."""
    with override_global_config(dict(_dd_site="datadoghq.com", _dd_api_key="", _dd_app_key="")):
        with patch("ddtrace.internal.agent.info", return_value={"endpoints": []}):
            client = new_ai_guard_client(endpoint="https://api.example.com/ai-guard")

            with pytest.raises(AIGuardClientError):
                client.evaluate([Message(role="user", content="hi")])

    assert recording_connection.instances == []


@pytest.mark.parametrize("api_key, app_key", [("only-api", ""), ("", "only-app")])
def test_one_key_still_sends_that_credential_header(api_key, app_key):
    """Direct construction with a single key keeps sending the header it always sent."""
    client = AIGuardClient("https://api.example.com/ai-guard", api_key, app_key)

    assert client._headers.get("DD-API-KEY") == (api_key or None)
    assert client._headers.get("DD-APPLICATION-KEY") == (app_key or None)


def test_credentials_win_over_the_agent_route():
    """A service with both keys keeps calling AI Guard directly, whatever the Agent advertises."""
    with override_global_config(dict(_dd_site="datadoghq.com", _dd_api_key="test-api-key", _dd_app_key="test-app-key")):
        with patch("ddtrace.internal.agent.info") as mock_info:
            client = new_ai_guard_client()

    mock_info.assert_not_called()
    assert client._endpoint == "https://app.datadoghq.com/api/v2/ai-guard"
    assert client._headers["DD-API-KEY"] == "test-api-key"
    assert client._headers["DD-APPLICATION-KEY"] == "test-app-key"


@patch("ddtrace.internal.telemetry.telemetry_writer.add_gauge_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_transport_metric_reports_direct(mock_execute_request, gauge_mock, ai_guard_client):
    """A client with credentials reports the direct transport."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    ai_guard_client.evaluate([Message(role="user", content="hi")])

    gauge_mock.assert_any_call(
        TELEMETRY_NAMESPACE.AI_GUARD, AI_GUARD.TRANSPORT_METRIC, 1, ((AI_GUARD.TRANSPORT_TAG, "direct"),)
    )


@patch("ddtrace.internal.telemetry.telemetry_writer.add_gauge_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_transport_metric_reports_agent(mock_execute_request, gauge_mock, agent_transport_client):
    """A client using the Agent route reports the agent transport."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    agent_transport_client.evaluate([Message(role="user", content="hi")])

    gauge_mock.assert_any_call(
        TELEMETRY_NAMESPACE.AI_GUARD, AI_GUARD.TRANSPORT_METRIC, 1, ((AI_GUARD.TRANSPORT_TAG, "agent"),)
    )


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
def test_agent_requests_metric_counts_each_success_once(telemetry_mock, agent_transport_client, recording_connection):
    """Every evaluation sent through the Agent is counted exactly once, with its status."""
    agent_transport_client.evaluate([Message(role="user", content="hi")])

    assert _agent_requests_calls(telemetry_mock) == [
        call(
            TELEMETRY_NAMESPACE.AI_GUARD,
            AI_GUARD.AGENT_REQUESTS_METRIC,
            1,
            (
                (AI_GUARD.STATUS_CODE_TAG, "200"),
                (AI_GUARD.ERROR_TYPE_TAG, "none"),
                (AI_GUARD.AGENT_ERROR_TAG, "none"),
            ),
        )
    ]


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_agent_requests_metric_on_transport_failure(mock_execute_request, telemetry_mock, agent_transport_client):
    """A transport failure is counted once, with its error type and no status."""
    mock_execute_request.side_effect = ConnectionFailedError("Connection refused")

    with pytest.raises(AIGuardClientError):
        agent_transport_client.evaluate([Message(role="user", content="hi")])

    assert _agent_requests_calls(telemetry_mock) == [
        call(
            TELEMETRY_NAMESPACE.AI_GUARD,
            AI_GUARD.AGENT_REQUESTS_METRIC,
            1,
            (
                (AI_GUARD.STATUS_CODE_TAG, AI_GUARD.STATUS_NONE),
                (AI_GUARD.ERROR_TYPE_TAG, AI_GUARD.ERROR_CONNECTION),
                (AI_GUARD.AGENT_ERROR_TAG, "none"),
            ),
        )
    ]


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
def test_agent_requests_metric_reports_the_agent_error_code(
    telemetry_mock, agent_transport_client, recording_connection
):
    """The Agent's own error code is read from its header and tagged."""
    recording_connection.status = 413
    recording_connection.body = b'{"errors":[{"code":"payload_too_large"}]}'
    recording_connection.response_headers = {
        AI_GUARD.AGENT_ERROR_HEADER: "payload_too_large",
        AI_GUARD.AGENT_VERSION_HEADER: "7.79.0",
    }

    with pytest.raises(AIGuardClientError) as raised:
        agent_transport_client.evaluate([Message(role="user", content="hi")])

    assert raised.value.status == 413
    assert _agent_requests_calls(telemetry_mock) == [
        call(
            TELEMETRY_NAMESPACE.AI_GUARD,
            AI_GUARD.AGENT_REQUESTS_METRIC,
            1,
            (
                (AI_GUARD.STATUS_CODE_TAG, "413"),
                (AI_GUARD.ERROR_TYPE_TAG, "none"),
                (AI_GUARD.AGENT_ERROR_TAG, "payload_too_large"),
            ),
        )
    ]


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_agent_requests_metric_not_reported_in_direct_mode(mock_execute_request, telemetry_mock, ai_guard_client):
    """The agent.requests metric describes the Agent route only."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    ai_guard_client.evaluate([Message(role="user", content="hi")])

    assert telemetry_mock.call_args_list != []
    assert _agent_requests_calls(telemetry_mock) == []
