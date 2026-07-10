from typing import Optional

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


def record_trace_writer_metric(name: str, count: int, tags: Optional[tuple[tuple[str, str], ...]] = None) -> None:
    """Record a TRACERS-namespace count metric emitted by the trace writer pipeline.

    Used for span/trace_api metrics such as ``spans_enqueued_for_serialization``, ``spans_dropped``
    (tagged with a ``reason``) and ``trace_api.requests``/``responses``/``errors``. Non-positive
    counts are ignored so callers can pass raw span counts without guarding.
    """
    if count <= 0:
        return
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, name, count, tags=tags)


def record_span_pointer_calculation(context: str, span_pointer_count: int) -> None:
    telemetry_writer.add_count_metric(
        namespace=TELEMETRY_NAMESPACE.TRACERS,
        name="span_pointer_calculation",
        value=1,
        tags=(("context", context), ("count", _span_pointer_count_to_tag(span_pointer_count))),
    )


def _span_pointer_count_to_tag(span_pointer_count: int) -> str:
    if span_pointer_count < 0:
        # this shouldn't be possible, but let's make sure
        return "negative"

    elif span_pointer_count <= 5:
        return str(span_pointer_count)

    elif span_pointer_count <= 10:
        return "6-10"

    elif span_pointer_count <= 20:
        return "11-20"

    elif span_pointer_count <= 50:
        return "21-50"

    elif span_pointer_count <= 100:
        return "51-100"

    else:
        return "101+"


def record_span_pointer_calculation_issue(
    context: str, additional_tags: Optional[tuple[tuple[str, str], ...]] = None
) -> None:
    tags: tuple[tuple[str, str], ...] = (("context", context),)
    if additional_tags:
        tags += additional_tags

    telemetry_writer.add_count_metric(
        namespace=TELEMETRY_NAMESPACE.TRACERS,
        name="span_pointer_calculation.issue",
        value=1,
        tags=tags,
    )
