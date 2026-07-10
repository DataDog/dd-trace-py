from typing import Optional

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


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


def record_writer_spans_enqueued(count: int) -> None:
    telemetry_writer.add_count_metric(
        namespace=TELEMETRY_NAMESPACE.TRACERS, name="spans_enqueued_for_serialization", value=count
    )


def record_spans_dropped(count: int, reason: str) -> None:
    """Record spans that are known to be dropped (never delivered to the intake).

    ``reason`` identifies the stage/cause of the drop:
      - ``tp_drop``: dropped by a trace processor in the SpanAggregator
      - ``overfull_buffer``: the encoder buffer could not fit the trace
      - ``serialization_error``: the trace could not be encoded/compressed
      - ``api_error``: the payload could not be delivered to the intake

    Callers must only invoke this once a span is *definitively* dropped (e.g. after all
    HTTP retries are exhausted), never speculatively on an attempt that may still succeed.
    """
    if count <= 0:
        return
    telemetry_writer.add_count_metric(
        namespace=TELEMETRY_NAMESPACE.TRACERS,
        name="spans_dropped",
        value=count,
        tags=(("reason", reason),),
    )


def record_writer_trace_api_request() -> None:
    telemetry_writer.add_count_metric(namespace=TELEMETRY_NAMESPACE.TRACERS, name="trace_api.requests", value=1)


def record_writer_trace_api_response(status_code: int) -> None:
    telemetry_writer.add_count_metric(
        namespace=TELEMETRY_NAMESPACE.TRACERS,
        name="trace_api.responses",
        value=1,
        tags=(("status_code", str(status_code)),),
    )


def record_writer_trace_api_error(error_type: str) -> None:
    telemetry_writer.add_count_metric(
        namespace=TELEMETRY_NAMESPACE.TRACERS,
        name="trace_api.errors",
        value=1,
        tags=(("type", error_type),),
    )
