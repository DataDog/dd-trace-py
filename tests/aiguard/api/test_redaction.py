"""Sensitive data redaction, see 'RFC AI Guard Sensitive Data Redaction'.

Most scenarios live in redaction_scenarios.json so other tracers can drive the same corpus. Each case is
run twice: once against redact_messages directly, once end to end through evaluate().
"""

from copy import deepcopy
import json
from pathlib import Path
from typing import Any
from typing import Optional
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.aiguard import AIGuardAbortError
from ddtrace.aiguard import AIGuardClient
from ddtrace.aiguard import Message
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.aiguard._redaction import Segment
from ddtrace.aiguard._redaction import _resolve_writable_string
from ddtrace.aiguard._redaction import _split_segments
from ddtrace.aiguard._redaction import redact_messages
from tests.aiguard.utils import find_ai_guard_span
from tests.aiguard.utils import mock_evaluate_response
from tests.aiguard.utils import override_ai_guard_config
from tests.utils import TracerSpanContainer


SCENARIOS: list[dict[str, Any]] = json.loads((Path(__file__).parent / "redaction_scenarios.json").read_text())["cases"]

# Matches the messages the corpus builds its paths against.
SIMPLE: list[Message] = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "My SSN is 123-45-6789"},
]
REPLACEMENTS: list[dict[str, str]] = [{"path": "messages[1].content", "replacement": "My SSN is <REDACTED>"}]
REDACTED: list[Message] = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "My SSN is <REDACTED>"},
]


def _corpus_params() -> list[Any]:
    return [pytest.param(case, id=case["id"]) for case in SCENARIOS]


def _metrics(add_count_metric: Mock, metric: str) -> list[tuple[Any, Any]]:
    """Every (value, tags) pair recorded for the given metric."""
    return [
        (args[2], args[3])
        for args, _ in add_count_metric.call_args_list
        if args[0].value == "ai_guard" and args[1] == metric
    ]


def _meta_struct(test_spans: TracerSpanContainer) -> dict[str, Any]:
    struct = find_ai_guard_span(test_spans)._get_struct_tag(AI_GUARD.TAG)
    assert struct is not None
    return struct


@pytest.mark.parametrize("case", _corpus_params())
def test_redact_messages_corpus(case: dict[str, Any]) -> None:
    """Every corpus scenario, applied by redact_messages directly."""
    messages = deepcopy(case["messages"])
    untouched = deepcopy(messages)

    result = redact_messages(messages, case["redaction_replacements"])

    assert messages == untouched, "the caller's messages must never be mutated"
    if case["expected_messages"] == "SAME":
        assert result is messages, "nothing applied: the original list object must come back"
    else:
        assert result is not messages
        assert result == case["expected_messages"]


@pytest.mark.parametrize("case", _corpus_params())
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_applies_corpus(
    mock_execute_request: Mock,
    ai_guard_client: AIGuardClient,
    test_spans: TracerSpanContainer,
    case: dict[str, Any],
) -> None:
    """Every corpus scenario, end to end: the SDK result, meta struct and the outgoing payload."""
    messages = deepcopy(case["messages"])
    sent = deepcopy(messages)
    mock_execute_request.return_value = mock_evaluate_response(
        "ALLOW", redaction_replacements=case["redaction_replacements"]
    )
    expected = messages if case["expected_messages"] == "SAME" else case["expected_messages"]

    with override_ai_guard_config(dict(_ai_guard_redaction_enabled=case.get("redaction_enabled", True))):
        result = ai_guard_client.evaluate(messages)

    assert result["messages"] == expected
    if case["expected_messages"] == "SAME":
        assert result["messages"] is messages
    assert _meta_struct(test_spans)["messages"] == expected
    # The service must always receive the originals: it needs the raw text to compute the replacements.
    payload = mock_execute_request.call_args[0][1]
    assert payload["data"]["attributes"]["messages"] == sent


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_history_is_redacted_again_on_every_turn(
    mock_execute_request: Mock,
    ai_guard_client: AIGuardClient,
    test_spans: TracerSpanContainer,
) -> None:
    """Redaction is copy-on-write, so an original left in the caller's history is redacted again next turn.

    The service needs the raw text to compute a replacement, so the history it receives on the second
    turn still carries the SSN the application never had redacted in its own list.
    """
    messages = deepcopy(SIMPLE)
    mock_execute_request.return_value = mock_evaluate_response("ALLOW", redaction_replacements=REPLACEMENTS)

    first = ai_guard_client.evaluate(messages)

    assert first["messages"] == REDACTED
    assert messages == SIMPLE, "the application keeps its own list, originals included"

    test_spans.reset()
    messages.append({"role": "assistant", "content": "Your SSN is <REDACTED>."})
    messages.append({"role": "user", "content": "my email is paco@paco.es"})
    mock_execute_request.return_value = mock_evaluate_response(
        "ALLOW",
        redaction_replacements=[
            {"path": "messages[1].content", "replacement": "My SSN is <REDACTED>"},
            {"path": "messages[3].content", "replacement": "my email is <REDACTED>"},
        ],
    )

    second = ai_guard_client.evaluate(messages)

    sent = mock_execute_request.call_args[0][1]["data"]["attributes"]["messages"]
    assert sent[1]["content"] == "My SSN is 123-45-6789", "the service must still see the original history"
    expected: list[Message] = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "My SSN is <REDACTED>"},
        {"role": "assistant", "content": "Your SSN is <REDACTED>."},
        {"role": "user", "content": "my email is <REDACTED>"},
    ]
    assert second["messages"] == expected
    assert _meta_struct(test_spans)["messages"] == expected


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_paths_are_not_reused_across_turns(
    mock_execute_request: Mock,
    ai_guard_client: AIGuardClient,
    test_spans: TracerSpanContainer,
) -> None:
    """Paths belong to the array they were returned for: a redacted index says nothing about the next turn."""
    messages = deepcopy(SIMPLE)
    mock_execute_request.return_value = mock_evaluate_response("ALLOW", redaction_replacements=REPLACEMENTS)

    ai_guard_client.evaluate(messages)

    test_spans.reset()
    # The application rebuilds the context: the sensitive message moves to index 0 and a benign one
    # takes over index 1, the index the previous turn redacted.
    messages = [messages[1], {"role": "user", "content": "what is the weather?"}]
    mock_execute_request.return_value = mock_evaluate_response(
        "ALLOW", redaction_replacements=[{"path": "messages[0].content", "replacement": "My SSN is <REDACTED>"}]
    )

    result = ai_guard_client.evaluate(messages)

    expected: list[Message] = [
        {"role": "user", "content": "My SSN is <REDACTED>"},
        {"role": "user", "content": "what is the weather?"},
    ]
    assert result["messages"] == expected
    assert _meta_struct(test_spans)["messages"] == expected


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_redacted_is_reported_per_turn(
    mock_execute_request: Mock,
    add_count_metric: Mock,
    ai_guard_client: AIGuardClient,
    test_spans: TracerSpanContainer,
) -> None:
    """Each evaluation reports its own outcome: a redacting turn does not mark the next one redacted."""
    messages = deepcopy(SIMPLE)
    mock_execute_request.return_value = mock_evaluate_response("ALLOW", redaction_replacements=REPLACEMENTS)

    ai_guard_client.evaluate(messages)

    assert find_ai_guard_span(test_spans).get_tag(AI_GUARD.REDACTED_TAG) == "true"

    test_spans.reset()
    clean: list[Message] = [{"role": "user", "content": "what is the weather?"}]
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    result = ai_guard_client.evaluate(clean)

    assert result["messages"] is clean
    assert find_ai_guard_span(test_spans).get_tag(AI_GUARD.REDACTED_TAG) == "false"
    redacted_tags = [dict(tags).get("redacted") for _, tags in _metrics(add_count_metric, AI_GUARD.REQUESTS_METRIC)]
    assert redacted_tags == ["true", "false"]


def test_redaction_never_raises() -> None:
    """An unexpected failure degrades to the original messages instead of breaking the caller."""

    class Undeepcopyable:
        def __deepcopy__(self, memo: dict[int, Any]) -> Any:
            raise RuntimeError("boom")

    messages: list[Any] = [{"role": "user", "content": "My SSN is 123-45-6789", "extra": Undeepcopyable()}]

    result = redact_messages(messages, [{"path": "messages[0].content", "replacement": "redacted"}])

    assert result is messages


@pytest.mark.parametrize(
    "path,expected",
    [
        pytest.param(
            "messages[1].tool_calls[0].function.arguments",
            [("messages", 1), ("tool_calls", 0), ("function", None), ("arguments", None)],
            id="the RFC worked example",
        ),
        pytest.param("messages[0].content", [("messages", 0), ("content", None)], id="content"),
        pytest.param("messages[0].content[1].text", [("messages", 0), ("content", 1), ("text", None)], id="text"),
        pytest.param("messages[01].content", [("messages", 1), ("content", None)], id="leading zero index"),
        pytest.param("messages[-1].content", None, id="negative index"),
        pytest.param("messages[0].con-tent", None, id="hyphen"),
        pytest.param("messages[0].content.", None, id="trailing dot"),
        pytest.param("", None, id="empty"),
    ],
)
def test_split_segments(path: str, expected: Optional[list[Segment]]) -> None:
    """The per-segment tokenizer every tracer implements identically."""
    assert _split_segments(path) == expected


def test_resolve_writable_string_against_the_rfc_example() -> None:
    """Path resolution against the canonical Message projection."""
    root: dict[str, Any] = {
        "messages": [
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "call_1", "function": {"name": "send", "arguments": '{"ssn":"1"}'}}],
            },
        ]
    }

    def resolved_value(path: str) -> Any:
        resolved = _resolve_writable_string(root, path)
        return None if resolved is None else resolved[0][resolved[1]]

    assert resolved_value("messages[0].content") == "hello"
    assert resolved_value("messages[1].tool_calls[0].function.arguments") == '{"ssn":"1"}'
    # Not redactable: a structural field, and a path that resolves to no string at all.
    assert resolved_value("messages[1].tool_calls[0].function.name") is None
    assert resolved_value("messages[0].missing") is None


@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_redaction_survives_message_truncation(
    mock_execute_request: Mock,
    add_count_metric: Mock,
    ai_guard_client: AIGuardClient,
    test_spans: TracerSpanContainer,
) -> None:
    """Paths index the full evaluated array, so redaction runs before meta struct truncates it."""
    messages: list[Message] = [{"role": "user", "content": f"message {i} ssn 123-45-6789"} for i in range(20)]
    mock_execute_request.return_value = mock_evaluate_response(
        "ALLOW",
        redaction_replacements=[
            {"path": "messages[0].content", "replacement": "message 0 ssn <REDACTED>"},
            {"path": "messages[19].content", "replacement": "message 19 ssn <REDACTED>"},
        ],
    )

    result = ai_guard_client.evaluate(messages)

    # The result carries every message, both redacted at their original indexes.
    assert len(result["messages"]) == 20
    assert result["messages"][0]["content"] == "message 0 ssn <REDACTED>"
    assert result["messages"][19]["content"] == "message 19 ssn <REDACTED>"
    # Meta struct keeps the last 16, so messages[0] is dropped but messages[19] is still redacted.
    struct_messages = _meta_struct(test_spans)["messages"]
    assert len(struct_messages) == 16
    assert struct_messages[-1]["content"] == "message 19 ssn <REDACTED>"
    assert struct_messages[0]["content"] == "message 4 ssn 123-45-6789"
    # Truncating twice must not be reported twice.
    assert _metrics(add_count_metric, AI_GUARD.TRUNCATED_METRIC) == [(1, (("type", "messages"),))]


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_redacted_content_is_truncated_in_meta_struct(
    mock_execute_request: Mock,
    ai_guard_client: AIGuardClient,
    test_spans: TracerSpanContainer,
) -> None:
    """A redacted string longer than the content limit is truncated for meta struct only."""
    replacement = "<REDACTED>" * 100
    messages: list[Message] = [{"role": "user", "content": "My SSN is 123-45-6789"}]
    mock_execute_request.return_value = mock_evaluate_response(
        "ALLOW", redaction_replacements=[{"path": "messages[0].content", "replacement": replacement}]
    )

    with override_ai_guard_config(dict(_ai_guard_max_content_size=32)):
        result = ai_guard_client.evaluate(messages)

    assert result["messages"][0]["content"] == replacement
    assert _meta_struct(test_spans)["messages"][0]["content"] == replacement[:32]


@pytest.mark.parametrize("action", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_blocked_evaluation_reports_redacted_messages(
    mock_execute_request: Mock,
    ai_guard_client: AIGuardClient,
    test_spans: TracerSpanContainer,
    action: str,
) -> None:
    """A block still redacts what it reports, and the abort error carries no conversation.

    The messages can be sensitive and arbitrarily large, and errors get logged, so the span is
    the only place they are reported on the block path.
    """
    messages = deepcopy(SIMPLE)
    mock_execute_request.return_value = mock_evaluate_response(action, redaction_replacements=REPLACEMENTS)

    with pytest.raises(AIGuardAbortError) as exc_info:
        ai_guard_client.evaluate(messages)

    assert _meta_struct(test_spans)["messages"] == REDACTED
    assert not hasattr(exc_info.value, "messages")
    assert "123-45-6789" not in str(exc_info.value)


@pytest.mark.parametrize(
    "redaction_enabled,replacements,expected",
    [
        pytest.param(True, REPLACEMENTS, "true", id="something redacted"),
        pytest.param(True, None, "false", id="nothing to redact"),
        # A path pointing at a structural field resolves read-only, so no replacement is applied.
        pytest.param(True, [{"path": "messages[1].role", "replacement": "user"}], "false", id="every path skipped"),
        pytest.param(False, REPLACEMENTS, None, id="kill switch off means no tag at all"),
    ],
)
@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_redacted_is_reported(
    mock_execute_request: Mock,
    add_count_metric: Mock,
    ai_guard_client: AIGuardClient,
    test_spans: TracerSpanContainer,
    redaction_enabled: bool,
    replacements: Optional[list[dict[str, str]]],
    expected: Optional[str],
) -> None:
    """Whether redaction happened is reported both as a span tag and as an ai_guard.requests tag."""
    messages = deepcopy(SIMPLE)
    mock_execute_request.return_value = mock_evaluate_response("ALLOW", redaction_replacements=replacements)

    with override_ai_guard_config(dict(_ai_guard_redaction_enabled=redaction_enabled)):
        ai_guard_client.evaluate(messages)

    assert find_ai_guard_span(test_spans).get_tag(AI_GUARD.REDACTED_TAG) == expected
    [(_, telemetry_tags)] = _metrics(add_count_metric, AI_GUARD.REQUESTS_METRIC)
    assert dict(telemetry_tags).get("redacted") == expected


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_sds_findings_do_not_drive_redaction(
    mock_execute_request: Mock,
    ai_guard_client: AIGuardClient,
    test_spans: TracerSpanContainer,
) -> None:
    """Findings are detection metadata: on their own they never change a message."""
    messages = deepcopy(SIMPLE)
    findings: list[dict[str, Any]] = [
        {
            "rule_display_name": "US Social Security Number Scanner",
            "rule_tag": "us_ssn",
            "category": "ssn",
            "location": {"path": "messages[1].content", "start_index": 10, "end_index_exclusive": 21},
        }
    ]
    mock_execute_request.return_value = mock_evaluate_response("ALLOW", sds_findings=findings)

    result = ai_guard_client.evaluate(messages)

    assert result["messages"] is messages
    assert _meta_struct(test_spans)["sds"] == findings


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_redacted_messages_are_isolated_from_the_caller(
    mock_execute_request: Mock,
    ai_guard_client: AIGuardClient,
    test_spans: TracerSpanContainer,
) -> None:
    """Mutating the input afterwards must not reach the result or the reported messages."""
    messages = deepcopy(SIMPLE)
    mock_execute_request.return_value = mock_evaluate_response("ALLOW", redaction_replacements=REPLACEMENTS)

    result = ai_guard_client.evaluate(messages)
    messages[1]["content"] = "mutated after the call"
    messages.append({"role": "user", "content": "appended after the call"})

    assert result["messages"] == REDACTED
    assert _meta_struct(test_spans)["messages"] == REDACTED


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_kill_switch_keeps_findings_and_evaluation(
    mock_execute_request: Mock,
    ai_guard_client: AIGuardClient,
    test_spans: TracerSpanContainer,
) -> None:
    """With redaction disabled the evaluation still runs and findings are still reported.

    Their offsets describe the redacted string ([10, 20) spans <REDACTED>), so they do not
    match the originals reported here: forwarded untouched, never sliced on.
    """
    messages = deepcopy(SIMPLE)
    findings: list[dict[str, Any]] = [
        {
            "rule_tag": "us_ssn",
            "category": "ssn",
            "location": {"path": "messages[1].content", "start_index": 10, "end_index_exclusive": 20},
        }
    ]
    mock_execute_request.return_value = mock_evaluate_response(
        "ALLOW", sds_findings=findings, redaction_replacements=REPLACEMENTS
    )

    with override_ai_guard_config(dict(_ai_guard_redaction_enabled=False)):
        result = ai_guard_client.evaluate(messages)

    assert result["action"] == "ALLOW"
    assert result["messages"] is messages
    assert _meta_struct(test_spans)["messages"] == SIMPLE
    assert _meta_struct(test_spans)["sds"] == findings
