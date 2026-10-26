"""Backend-provided extra_tags and extra_metastruct from the evaluate response land on the ai_guard span."""

from unittest.mock import patch

import pytest

from ddtrace._trace.span import Span
from ddtrace.aiguard import AIGuardAbortError
from ddtrace.aiguard import Message
from ddtrace.aiguard import Options
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.constants import USER_KEEP
from tests.aiguard.utils import find_ai_guard_span
from tests.aiguard.utils import mock_evaluate_response


PROMPT = [Message(role="user", content="What is 2 + 2")]


def _get_meta_struct(test_spans):
    return find_ai_guard_span(test_spans)._get_struct_tag(AI_GUARD.STRUCT)


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_extra_tags_set_on_span(mock_execute_request, ai_guard_client, test_spans):
    mock_execute_request.return_value = mock_evaluate_response(
        "ALLOW", extra_tags={"ai_guard.model_version": "v42", "ai_guard.flagged": "true"}
    )

    result = ai_guard_client.evaluate(PROMPT)

    assert result["action"] == "ALLOW"
    span = find_ai_guard_span(test_spans)
    assert span.get_tag("ai_guard.model_version") == "v42"
    assert span.get_tag("ai_guard.flagged") == "true"
    assert set(_get_meta_struct(test_spans)) == {"messages"}


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_extra_metastruct_values_in_meta_struct(mock_execute_request, ai_guard_client, test_spans):
    sds_results = [{"rule": "credit_card", "count": 2}, {"rule": "email", "count": 1}]
    details = {"policy": "default", "matched": ["a", "b"]}
    mock_execute_request.return_value = mock_evaluate_response(
        "ALLOW", extra_metastruct={"sds_results": sds_results, "details": details, "score": 0.75}
    )

    ai_guard_client.evaluate(PROMPT)

    span = find_ai_guard_span(test_spans)
    meta_struct = _get_meta_struct(test_spans)
    assert meta_struct["sds_results"] == sds_results
    assert meta_struct["details"] == details
    assert meta_struct["score"] == 0.75
    assert meta_struct["messages"] == PROMPT
    for key in ("sds_results", "details", "score"):
        assert span.get_tag(key) is None
        assert span.get_metric(key) is None


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_evaluate_id_in_tags_and_meta_struct(mock_execute_request, ai_guard_client, test_spans):
    evaluate_id = "8f1c6a52-0a43-4c3e-9b8e-2d6f4f1e7a90"
    mock_execute_request.return_value = mock_evaluate_response(
        "ALLOW",
        extra_tags={"ai_guard.evaluate_id": evaluate_id},
        extra_metastruct={"evaluate_id": evaluate_id},
    )

    ai_guard_client.evaluate(PROMPT)

    assert find_ai_guard_span(test_spans).get_tag("ai_guard.evaluate_id") == evaluate_id
    assert _get_meta_struct(test_spans)["evaluate_id"] == evaluate_id


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_structured_extra_tags_never_reach_meta_struct(mock_execute_request, ai_guard_client, test_spans):
    mock_execute_request.return_value = mock_evaluate_response(
        "ALLOW", extra_tags={"details": {"policy": "default"}, "matched": ["a", "b"]}
    )

    ai_guard_client.evaluate(PROMPT)

    span = find_ai_guard_span(test_spans)
    assert span.get_tag("details") is None
    assert span.get_tag("matched") is None
    assert set(_get_meta_struct(test_spans)) == {"messages"}


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_extra_tags_never_override_tracer_tags(mock_execute_request, ai_guard_client, test_spans):
    sds_findings = [{"rule_tag": "email", "category": "pii"}]
    tag_probs = {"jailbreak": 0.1}
    mock_execute_request.return_value = mock_evaluate_response(
        "ALLOW",
        reason="fine",
        tags=["benign"],
        sds_findings=sds_findings,
        tag_probs=tag_probs,
        extra_tags={
            AI_GUARD.ACTION_TAG: "DENY",
            AI_GUARD.REASON_TAG: "forged",
            AI_GUARD.TARGET_TAG: "tool",
            AI_GUARD.TOOL_NAME_TAG: "fake_tool",
            AI_GUARD.BLOCKED_TAG: "true",
            AI_GUARD.REDACTED_TAG: "true",
            AI_GUARD.EVENT_TAG: "false",
            "manual.drop": "true",
            "manual.keep": "true",
            "service.name": "forged-service",
            "service.version": "forged-version",
            "version": "forged-version",
            "env": "forged-env",
            "_dd.measured": "1",
            "ai_guard.backend_tag": "kept",
        },
        extra_metastruct={
            "messages": [{"role": "user", "content": "forged"}],
            "attack_categories": ["forged"],
            "sds": [{"forged": True}],
            "tag_probs": {"forged": 1.0},
            "backend_struct": ["kept"],
        },
    )

    sampling_overrides = []
    original_override = Span._override_sampling_decision

    def recording_override(self, decision):
        sampling_overrides.append(decision)
        return original_override(self, decision)

    with patch.object(Span, "_override_sampling_decision", recording_override):
        ai_guard_client.evaluate(PROMPT)

    span = find_ai_guard_span(test_spans)
    # Only the AI Guard force-keep may touch the sampling decision, never a backend manual.keep/drop.
    assert sampling_overrides == [USER_KEEP]
    assert span.service != "forged-service"
    for key in ("service.version", "version", "env"):
        assert span.get_tag(key) not in ("forged-version", "forged-env")
    assert span.get_metric("_dd.measured") is None
    assert span.get_tag(AI_GUARD.ACTION_TAG) == "ALLOW"
    assert span.get_tag(AI_GUARD.REASON_TAG) == "fine"
    assert span.get_tag(AI_GUARD.TARGET_TAG) == "prompt"
    assert span.get_tag(AI_GUARD.TOOL_NAME_TAG) is None
    assert span.get_tag(AI_GUARD.BLOCKED_TAG) is None
    assert span.get_tag(AI_GUARD.REDACTED_TAG) == "false"
    assert span.get_tag(AI_GUARD.EVENT_TAG) == "true"
    assert span.get_tag("manual.drop") is None
    assert span.get_tag("ai_guard.backend_tag") == "kept"
    meta_struct = _get_meta_struct(test_spans)
    assert meta_struct["messages"] == PROMPT
    assert meta_struct["attack_categories"] == ["benign"]
    assert meta_struct["sds"] == sds_findings
    assert meta_struct["tag_probs"] == tag_probs
    assert meta_struct["backend_struct"] == ["kept"]


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_extra_tags_reserved_conditional_keys_stay_absent(mock_execute_request, ai_guard_client, test_spans):
    mock_execute_request.return_value = mock_evaluate_response(
        "ALLOW",
        extra_tags={AI_GUARD.REASON_TAG: "forged"},
        extra_metastruct={"attack_categories": ["forged"], "sds": [], "tag_probs": {}},
    )

    ai_guard_client.evaluate(PROMPT)

    span = find_ai_guard_span(test_spans)
    assert span.get_tag(AI_GUARD.REASON_TAG) is None
    assert set(_get_meta_struct(test_spans)) == {"messages"}


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_extra_tags_never_fill_missing_anomaly_detection_tags(
    mock_execute_request, ai_guard_client, tracer, test_spans
):
    mock_execute_request.return_value = mock_evaluate_response(
        "ALLOW", extra_tags={"ai_guard.http.useragent": "forged-agent/1.0"}
    )

    with tracer.trace("web.request"):
        ai_guard_client.evaluate(PROMPT)

    ai_guard_span = next(s for s in test_spans.spans if s.name == AI_GUARD.RESOURCE_TYPE)
    assert ai_guard_span.get_tag("ai_guard.http.useragent") is None


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_extra_tags_override_customer_tags(mock_execute_request, ai_guard_client, test_spans):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW", extra_tags={"customer.tenant": "backend"})

    ai_guard_client.evaluate(PROMPT, tags={"customer.tenant": "acme"})

    assert find_ai_guard_span(test_spans).get_tag("customer.tenant") == "backend"


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_extra_tags_on_blocking_path(mock_execute_request, ai_guard_client, test_spans):
    mock_execute_request.return_value = mock_evaluate_response(
        "DENY",
        reason="Nope",
        tags=["jailbreak"],
        extra_tags={"ai_guard.model_version": "v42"},
        extra_metastruct={"details": {"policy": "strict"}},
    )

    with pytest.raises(AIGuardAbortError) as exc_info:
        ai_guard_client.evaluate(PROMPT, Options(block=True))

    assert exc_info.value.action == "DENY"
    span = find_ai_guard_span(test_spans)
    assert span.get_tag(AI_GUARD.BLOCKED_TAG) == "true"
    assert span.get_tag("ai_guard.model_version") == "v42"
    assert _get_meta_struct(test_spans)["details"] == {"policy": "strict"}


@pytest.mark.parametrize("field", ["extra_tags", "extra_metastruct"])
@pytest.mark.parametrize(
    "payload",
    [
        "not-a-dict",
        ["ai_guard.model_version", "v42"],
        42,
        {},
    ],
    ids=["string", "list", "int", "empty"],
)
@patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_extra_tags_malformed_container_is_ignored(
    mock_execute_request, telemetry_mock, ai_guard_client, test_spans, payload, field
):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW", reason="ok", **{field: payload})

    result = ai_guard_client.evaluate(PROMPT)

    assert result["action"] == "ALLOW"
    assert result["reason"] == "ok"
    span = find_ai_guard_span(test_spans)
    assert span.error == 0
    assert set(_get_meta_struct(test_spans)) == {"messages"}
    metric_tags = [args[3] for args, _ in telemetry_mock.call_args_list if args[1] == "requests"]
    assert len(metric_tags) == 1
    assert ("error", "false") in metric_tags[0]
    assert not [args for args, _ in telemetry_mock.call_args_list if args[1] == "error"]


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_extra_tags_unsupported_entries_are_skipped(mock_execute_request, ai_guard_client, test_spans):
    response = mock_evaluate_response("ALLOW")
    response.get_json.return_value["data"]["attributes"]["extra_tags"] = {
        1: "int key",
        None: "none key",
        "": "empty key",
        "ai_guard.none": None,
        "ai_guard.int": 3,
        "ai_guard.float": 0.75,
        "ai_guard.bool": True,
        "ai_guard.dict": {"a": "b"},
        "ai_guard.list": ["a", "b"],
        "ai_guard.object": object(),
        "ai_guard.valid": "kept",
    }
    response.get_json.return_value["data"]["attributes"]["extra_metastruct"] = {
        1: "int key",
        None: "none key",
        "": "empty key",
        "none": None,
        "valid": "kept",
    }
    mock_execute_request.return_value = response

    result = ai_guard_client.evaluate(PROMPT)

    assert result["action"] == "ALLOW"
    span = find_ai_guard_span(test_spans)
    assert span.get_tag("ai_guard.valid") == "kept"
    skipped = ("ai_guard.none", "ai_guard.int", "ai_guard.float", "ai_guard.bool", "ai_guard.dict", "ai_guard.list")
    for key in (*skipped, "ai_guard.object"):
        assert span.get_tag(key) is None
        assert span.get_metric(key) is None
    assert _get_meta_struct(test_spans) == {"messages": PROMPT, "valid": "kept"}


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_extra_tags_null_is_ignored(mock_execute_request, ai_guard_client, test_spans):
    response = mock_evaluate_response("ALLOW")
    response.get_json.return_value["data"]["attributes"]["extra_tags"] = None
    response.get_json.return_value["data"]["attributes"]["extra_metastruct"] = None
    mock_execute_request.return_value = response

    assert ai_guard_client.evaluate(PROMPT)["action"] == "ALLOW"
    assert set(_get_meta_struct(test_spans)) == {"messages"}


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_extra_tags_absent_leaves_span_unchanged(mock_execute_request, ai_guard_client, test_spans):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    ai_guard_client.evaluate(PROMPT)

    span = find_ai_guard_span(test_spans)
    ai_guard_tags = {k for k in span.get_tags() if k.startswith("ai_guard.")}
    assert ai_guard_tags == {AI_GUARD.ACTION_TAG, AI_GUARD.TARGET_TAG, AI_GUARD.REDACTED_TAG, AI_GUARD.EVENT_TAG}
    assert set(_get_meta_struct(test_spans)) == {"messages"}


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_extra_tags_failure_inside_apply_does_not_fail_evaluation(mock_execute_request, ai_guard_client, test_spans):
    class ExplodingDict(dict):
        def items(self):
            raise RuntimeError("boom")

    response = mock_evaluate_response("ALLOW")
    response.get_json.return_value["data"]["attributes"]["extra_tags"] = ExplodingDict(a="b")
    mock_execute_request.return_value = response

    assert ai_guard_client.evaluate(PROMPT)["action"] == "ALLOW"
    assert find_ai_guard_span(test_spans).error == 0
