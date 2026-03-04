from __future__ import annotations

from typing import Optional

from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Context
from ddtrace.trace import Span

from ._constants import METRIC_LATENCY_DECODE
from ._constants import METRIC_LATENCY_INFERENCE
from ._constants import METRIC_LATENCY_PREFILL
from ._constants import METRIC_LATENCY_QUEUE
from ._constants import METRIC_LATENCY_TTFT
from ._constants import OPERATION_ID
from .extractors import LatencyMetrics


# Mapping from LatencyMetrics attributes to APM span metric names
_APM_LATENCY_METRIC_MAP = {
    "time_to_first_token": METRIC_LATENCY_TTFT,
    "time_in_queue": METRIC_LATENCY_QUEUE,
    "time_in_model_prefill": METRIC_LATENCY_PREFILL,
    "time_in_model_decode": METRIC_LATENCY_DECODE,
    "time_in_model_inference": METRIC_LATENCY_INFERENCE,
}


def create_span(
    integration,
    model_name: Optional[str],
    trace_headers: Optional[dict[str, str]],
    arrival_time: Optional[float] = None,
):
    """Create a vLLM span with parent context from trace headers."""
    parent_ctx = None
    if trace_headers:
        parent_ctx = HTTPPropagator.extract(trace_headers)

    span = integration.trace(
        operation_id=OPERATION_ID,
        submit_to_llmobs=True,
        parent_context=parent_ctx,
        model_name=model_name,
    )

    if arrival_time:
        span.start_ns = int(arrival_time * 1e9)

    return span


def set_latency_metrics(span: Span, latency_metrics: Optional[LatencyMetrics]) -> None:
    """Set latency span tags from pre-computed metrics."""
    if not latency_metrics:
        return

    for attr, tag_name in _APM_LATENCY_METRIC_MAP.items():
        value = getattr(latency_metrics, attr, None)
        if value is not None:
            span.set_metric(tag_name, value)


def inject_trace_context(tracer, trace_headers: Optional[dict[str, str]]) -> dict[str, str]:
    """Inject current trace context into headers for propagation."""
    headers = dict(trace_headers) if trace_headers else {}

    active = tracer.context_provider.active()
    if active:
        if isinstance(active, Span):
            HTTPPropagator.inject(active.context, headers)
        elif isinstance(active, Context):
            HTTPPropagator.inject(active, headers)

    return headers
