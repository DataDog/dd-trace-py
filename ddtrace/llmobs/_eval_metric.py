from dataclasses import dataclass
from dataclasses import field
import json
import time
from typing import Any
from typing import Callable
from typing import Optional
from typing import TypedDict

from ddtrace.internal.compat import ensure_text
from ddtrace.llmobs._utils import resolve_ml_app
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs.types import FeedbackSubmitter
from ddtrace.llmobs.types import JSONType
from ddtrace.version import __version__


class LLMObsEvaluationMetricEvent(TypedDict, total=False):
    event_kind: str
    join_on: dict[str, dict[str, str]]
    span_id: str
    trace_id: str
    session_id: str
    feedback_join_key: str
    submitter: FeedbackSubmitter
    metric_type: str
    label: str
    categorical_value: str
    numerical_value: float
    score_value: float
    boolean_value: bool
    json_value: dict[str, JSONType]
    text_value: str
    ml_app: str
    timestamp_ms: int
    tags: list[str]
    assessment: str
    reasoning: str
    eval_scope: str
    metadata: dict[str, Any]


@dataclass
class _SubmissionTelemetryContext:
    """Mutable validation state consumed by the public submission methods' telemetry finalizers."""

    metric_type: str
    error: Optional[str] = None
    join_on: dict[str, Any] = field(default_factory=dict)
    target_type: str = "other"


# AIDEV-NOTE: _resolve_agent_service and LLMObsSubmitEvaluationError intentionally remain owned by
# _llmobs. Callers inject them here to preserve their existing state and import paths without a cycle.
def _build_evaluation_metric_event(
    *,
    label: str,
    metric_type: str,
    value: Any,
    span: Any,
    span_with_tag_value: Any,
    tags: Any,
    ml_app: Optional[str],
    timestamp_ms: Any,
    metadata: Any,
    assessment: Any,
    reasoning: Any,
    eval_scope: str,
    agent_service: Optional[str],
    otel_trace_enabled: bool,
    resolve_agent_service: Callable[[Optional[str], Optional[str]], Optional[str]],
    submission_error_cls: type[Exception],
    telemetry_context: _SubmissionTelemetryContext,
) -> LLMObsEvaluationMetricEvent:
    join_on = telemetry_context.join_on
    has_exactly_one_joining_key = (span is not None) ^ (span_with_tag_value is not None)

    if not has_exactly_one_joining_key:
        telemetry_context.error = "provided_both_span_and_tag_joining_key"
        raise ValueError(
            "Exactly one of `span` or `span_with_tag_value` must be specified to submit an evaluation metric."
        )

    if span is not None:
        if (
            not isinstance(span, dict)
            or not isinstance(span.get("span_id"), str)
            or not isinstance(span.get("trace_id"), str)
        ):
            telemetry_context.error = "invalid_span"
            raise TypeError(
                "`span` must be a dictionary containing both span_id and trace_id keys. "
                "LLMObs.export_span() can be used to generate this dictionary from a given span."
            )
        join_on["span"] = span
    elif span_with_tag_value is not None:
        if (
            not isinstance(span_with_tag_value, dict)
            or not isinstance(span_with_tag_value.get("tag_key"), str)
            or not isinstance(span_with_tag_value.get("tag_value"), str)
        ):
            telemetry_context.error = "invalid_joining_key"
            raise TypeError(
                "`span_with_tag_value` must be a dict with keys 'tag_key' and 'tag_value' containing string values"
            )
        join_on["tag"] = {
            "key": span_with_tag_value.get("tag_key"),
            "value": span_with_tag_value.get("tag_value"),
        }

    if eval_scope not in ("span", "trace"):
        telemetry_context.error = "invalid_eval_scope"
        raise ValueError("eval_scope must be one of 'span' or 'trace'.")

    timestamp_ms = timestamp_ms if timestamp_ms else int(time.time() * 1000)

    if not isinstance(timestamp_ms, int) or timestamp_ms < 0:
        telemetry_context.error = "invalid_timestamp"
        raise ValueError("timestamp_ms must be a non-negative integer. Evaluation metric data will not be sent")

    if not label:
        telemetry_context.error = "invalid_metric_label"
        raise ValueError("label must be the specified name of the evaluation metric.")

    if "." in label:
        telemetry_context.error = "invalid_label_value"
        raise ValueError("label value must not contain a '.'.")

    metric_type = metric_type.lower()
    telemetry_context.metric_type = metric_type
    if metric_type not in ("categorical", "score", "boolean", "json"):
        telemetry_context.error = "invalid_metric_type"
        raise ValueError("metric_type must be one of 'categorical', 'score', 'boolean', or 'json'.")

    if metric_type == "categorical" and not isinstance(value, str):
        telemetry_context.error = "invalid_metric_value"
        raise TypeError("value must be a string for a categorical metric.")
    if metric_type == "score" and not isinstance(value, (int, float)):
        telemetry_context.error = "invalid_metric_value"
        raise TypeError("value must be an integer or float for a score metric.")
    if metric_type == "boolean" and not isinstance(value, bool):
        telemetry_context.error = "invalid_metric_value"
        raise TypeError("value must be a boolean for a boolean metric.")
    if metric_type == "json" and not isinstance(value, dict):
        telemetry_context.error = "invalid_metric_value"
        raise TypeError("value must be a dict for a json metric.")

    if tags is not None and not isinstance(tags, dict):
        raise submission_error_cls("tags must be a dictionary of string key-value pairs.")

    ml_app = resolve_ml_app(resolve_agent_service(agent_service, ml_app))

    evaluation_tags = {
        "ddtrace.version": __version__,
        "ml_app": ml_app,
    }

    if tags:
        for key, tag_value in tags.items():
            try:
                evaluation_tags[ensure_text(key)] = ensure_text(tag_value)
            except TypeError:
                telemetry_context.error = "invalid_tags"
                raise submission_error_cls("Failed to parse tags. Tags for evaluation metrics must be strings.")

    # Auto-add source:otel when OTel tracing is enabled so the backend waits for span conversion.
    if otel_trace_enabled:
        evaluation_tags["source"] = "otel"

    evaluation_metric: LLMObsEvaluationMetricEvent = {
        "event_kind": "evaluation",
        "join_on": join_on,
        "label": str(label),
        "metric_type": metric_type,
        "timestamp_ms": timestamp_ms,
        "{}_value".format(metric_type): value,  # type: ignore
        "ml_app": ml_app,
        "tags": ["{}:{}".format(key, tag_value) for key, tag_value in evaluation_tags.items()],
        "eval_scope": eval_scope,
    }

    if assessment:
        if not isinstance(assessment, str) or assessment not in (
            "pass",
            "fail",
        ):
            telemetry_context.error = "invalid_assessment"
            raise submission_error_cls("Failed to parse assessment. assessment must be either 'pass' or 'fail'.")
        else:
            evaluation_metric["assessment"] = assessment
    if reasoning:
        if not isinstance(reasoning, str):
            telemetry_context.error = "invalid_reasoning"
            raise submission_error_cls("Failed to parse reasoning. reasoning must be a string.")
        else:
            evaluation_metric["reasoning"] = reasoning

    if metadata:
        if not isinstance(metadata, dict):
            telemetry_context.error = "invalid_metadata"
            raise submission_error_cls("metadata must be json serializable dictionary.")
        else:
            serialized_metadata = safe_json(metadata)  # type: ignore[no-untyped-call]
            if serialized_metadata and isinstance(serialized_metadata, str):
                evaluation_metric["metadata"] = json.loads(serialized_metadata)

    return evaluation_metric


def _build_feedback_metric_event(
    *,
    label: str,
    metric_type: str,
    value: Any,
    submitter: Any,
    span: Any,
    span_id: Any,
    trace_id: Any,
    session_id: Any,
    feedback_join_key: Any,
    tags: Any,
    ml_app: Optional[str],
    timestamp_ms: Any,
    assessment: Any,
    reasoning: Any,
    agent_service: Optional[str],
    resolve_agent_service: Callable[[Optional[str], Optional[str]], Optional[str]],
    submission_error_cls: type[Exception],
    telemetry_context: _SubmissionTelemetryContext,
) -> LLMObsEvaluationMetricEvent:
    targets = {
        "span": span,
        "span_id": span_id,
        "trace_id": trace_id,
        "session_id": session_id,
        "feedback_join_key": feedback_join_key,
    }
    provided_targets = [name for name, target in targets.items() if target is not None]
    if len(provided_targets) != 1:
        telemetry_context.error = "invalid_target_count"
        raise ValueError(
            "Exactly one of `span`, `span_id`, `trace_id`, `session_id`, or "
            "`feedback_join_key` must be specified to submit feedback."
        )

    target_name = provided_targets[0]
    target_value: str
    if target_name == "span":
        telemetry_context.target_type = "span_id"
        if not isinstance(span, dict) or not isinstance(span.get("span_id"), str):
            telemetry_context.error = "invalid_span"
            raise TypeError(
                "`span` must be a dictionary containing a string span_id. "
                "LLMObs.export_span() can be used to generate this dictionary from a given span."
            )
        if not span["span_id"]:
            telemetry_context.error = "invalid_span"
            raise ValueError("`span` must contain a non-empty string span_id.")
        # AIDEV-NOTE: LLMObs.export_span() also returns trace_id, while callers may supply
        # dictionaries with additional fields. Feedback targets must contain exactly one
        # top-level identifier, so span= intentionally emits only span_id and is wire-equivalent
        # to passing span_id= directly.
        target_value = span["span_id"]
    else:
        telemetry_context.target_type = target_name
        direct_target = targets[target_name]
        if not isinstance(direct_target, str):
            telemetry_context.error = "invalid_{}".format(target_name)
            raise TypeError("`{}` must be a non-empty string.".format(target_name))
        if not direct_target:
            telemetry_context.error = "invalid_{}".format(target_name)
            raise ValueError("`{}` must be a non-empty string.".format(target_name))
        target_value = direct_target

    if not isinstance(submitter, dict) or not isinstance(submitter.get("id"), str):
        telemetry_context.error = "invalid_submitter"
        raise TypeError("`submitter` must be a dictionary containing a non-empty string id.")
    if not submitter["id"]:
        telemetry_context.error = "invalid_submitter"
        raise ValueError("`submitter` must contain a non-empty string id.")
    if "type" in submitter and not isinstance(submitter["type"], str):
        telemetry_context.error = "invalid_submitter"
        raise TypeError("`submitter.type` must be a string.")

    feedback_submitter = FeedbackSubmitter(id=submitter["id"])
    if "type" in submitter:
        feedback_submitter["type"] = submitter["type"]

    timestamp_ms = timestamp_ms if timestamp_ms else int(time.time() * 1000)
    if not isinstance(timestamp_ms, int) or timestamp_ms < 0:
        telemetry_context.error = "invalid_timestamp"
        raise ValueError("timestamp_ms must be a non-negative integer. Feedback data will not be sent")

    if not label:
        telemetry_context.error = "invalid_metric_label"
        raise ValueError("label must be the specified name of the feedback metric.")
    if "." in label:
        telemetry_context.error = "invalid_label_value"
        raise ValueError("label value must not contain a '.'.")

    metric_type = metric_type.lower()
    telemetry_context.metric_type = metric_type
    if metric_type not in ("categorical", "score", "boolean", "json", "text"):
        telemetry_context.error = "invalid_metric_type"
        raise ValueError("metric_type must be one of 'categorical', 'score', 'boolean', 'json', or 'text'.")

    if metric_type == "categorical" and not isinstance(value, str):
        telemetry_context.error = "invalid_metric_value"
        raise TypeError("value must be a string for a categorical metric.")
    if metric_type == "score" and not isinstance(value, (int, float)):
        telemetry_context.error = "invalid_metric_value"
        raise TypeError("value must be an integer or float for a score metric.")
    if metric_type == "boolean" and not isinstance(value, bool):
        telemetry_context.error = "invalid_metric_value"
        raise TypeError("value must be a boolean for a boolean metric.")
    if metric_type == "json" and not isinstance(value, dict):
        telemetry_context.error = "invalid_metric_value"
        raise TypeError("value must be a dict for a json metric.")
    if metric_type == "text" and not isinstance(value, str):
        telemetry_context.error = "invalid_metric_value"
        raise TypeError("value must be a string for a text metric.")

    if tags is not None and not isinstance(tags, dict):
        telemetry_context.error = "invalid_tags"
        raise submission_error_cls("tags must be a dictionary of string key-value pairs.")

    ml_app = resolve_ml_app(resolve_agent_service(agent_service, ml_app))
    feedback_tags = {
        "ddtrace.version": __version__,
        "ml_app": ml_app,
    }
    if tags:
        for key, tag_value in tags.items():
            try:
                feedback_tags[ensure_text(key)] = ensure_text(tag_value)
            except TypeError:
                telemetry_context.error = "invalid_tags"
                raise submission_error_cls("Failed to parse tags. Tags for feedback metrics must be strings.")

    feedback_metric: LLMObsEvaluationMetricEvent = {
        "event_kind": "feedback",
        "label": str(label),
        "metric_type": metric_type,
        "timestamp_ms": timestamp_ms,
        "{}_value".format(metric_type): value,  # type: ignore
        "ml_app": ml_app,
        "tags": ["{}:{}".format(key, tag_value) for key, tag_value in feedback_tags.items()],
        "submitter": feedback_submitter,
    }
    if telemetry_context.target_type == "span_id":
        feedback_metric["span_id"] = target_value
    elif telemetry_context.target_type == "trace_id":
        feedback_metric["trace_id"] = target_value
    elif telemetry_context.target_type == "session_id":
        feedback_metric["session_id"] = target_value
    else:
        feedback_metric["feedback_join_key"] = target_value

    if assessment:
        if not isinstance(assessment, str) or assessment not in ("pass", "fail"):
            telemetry_context.error = "invalid_assessment"
            raise submission_error_cls("Failed to parse assessment. assessment must be either 'pass' or 'fail'.")
        feedback_metric["assessment"] = assessment
    if reasoning:
        if not isinstance(reasoning, str):
            telemetry_context.error = "invalid_reasoning"
            raise submission_error_cls("Failed to parse reasoning. reasoning must be a string.")
        feedback_metric["reasoning"] = reasoning

    return feedback_metric
