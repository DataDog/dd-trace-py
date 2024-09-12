from typing import List
from typing import Optional
from typing import Tuple

from ddtrace import Span
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import PARENT_ID_KEY
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._utils import _get_ml_app
from ddtrace.llmobs.telemetry.constants import LLMOBS_TELEMETRY
from ddtrace.llmobs.telemetry.constants import LLMOBS_TELEMETRY_METRIC_NAMESPACE as _NAMESPACE


def _get_span_tags(span: Span, selected_keys: Optional[List[str]] = None) -> Tuple[Tuple[str, str], ...]:
    all_tags = {
        "integration_name": lambda: span.get_tag("component"),
        "ml_app": lambda: _get_ml_app(span),
        "model_name": lambda: span.get_tag(MODEL_NAME),
        "model_provider": lambda: span.get_tag(MODEL_PROVIDER),
        "span_kind": lambda: span.get_tag(SPAN_KIND),
        "is_root_span": lambda: str((span.get_tag(PARENT_ID_KEY) == "undefined")),
        "has_session_id": lambda: str((span.get_tag(SESSION_ID) is not None)),
    }

    if selected_keys is None:
        selected_keys = all_tags.keys()

    return tuple((key, all_tags[key]()) for key in selected_keys if key in all_tags)


def record_init_time(ml_app: str, duration: float, agentless: bool, integrations_enabled: bool) -> None:
    tags = (
        ("ml_app", ml_app),
        ("endpoint_type", "agentless" if agentless else "agent"),
        ("integrations_enabled", str(integrations_enabled)),
    )
    telemetry_writer.add_distribution_metric(_NAMESPACE, LLMOBS_TELEMETRY.INIT_TIME, duration, tags)


def record_span_created(span: Span) -> None:
    telemetry_writer.add_count_metric(_NAMESPACE, LLMOBS_TELEMETRY.SPANS_CREATED, 1.0, _get_span_tags(span))


def record_span_finished(span: Span) -> None:
    telemetry_writer.add_count_metric(_NAMESPACE, LLMOBS_TELEMETRY.SPANS_FINISHED, 1.0, _get_span_tags(span))


def record_annotation_usage(span: Span) -> None:
    tags = _get_span_tags(span, ["ml_app", "integration_name", "span_kind"])
    telemetry_writer.add_count_metric(_NAMESPACE, LLMOBS_TELEMETRY.ANNOTATIONS, 1.0, tags)


def record_export_span_usage(ml_app: str) -> None:
    tags = (("ml_app", ml_app),) if ml_app != "" else None
    telemetry_writer.add_count_metric(_NAMESPACE, LLMOBS_TELEMETRY.EXPORT_SPAN, 1.0, tags)


def record_submit_evaluations_usage(ml_app: str, metric_type: str) -> None:
    tags = (
        ("ml_app", ml_app),
        ("metric_type", metric_type),
    )
    telemetry_writer.add_count_metric(_NAMESPACE, LLMOBS_TELEMETRY.SUBMIT_EVALUATIONS, 1.0, tags)


def record_user_flush_usage(ml_app: str) -> None:
    tags = (("ml_app", ml_app),) if ml_app != "" else None
    telemetry_writer.add_count_metric(_NAMESPACE, LLMOBS_TELEMETRY.USER_FLUSH, 1.0, tags)


def record_inject_distributed_headers_usage(ml_app: str) -> None:
    tags = (("ml_app", ml_app),) if ml_app != "" else None
    telemetry_writer.add_count_metric(_NAMESPACE, LLMOBS_TELEMETRY.INJECT_DISTRIBUTED_HEADERS, 1.0, tags)


def record_activate_distributed_headers_usage(ml_app: str) -> None:
    tags = (("ml_app", ml_app),) if ml_app != "" else None
    telemetry_writer.add_count_metric(_NAMESPACE, LLMOBS_TELEMETRY.ACTIVATE_DISTRIBUTED_HEADERS, 1.0, tags)
