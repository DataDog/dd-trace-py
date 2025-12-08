from __future__ import annotations

from typing import Optional

from ddtrace._trace.pin import Pin
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Context
from ddtrace.trace import Span


def create_span(
    pin: Pin,
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
        pin=pin,
        operation_id="vllm.request",
        submit_to_llmobs=True,
        parent_context=parent_ctx,
        model_name=model_name,
    )

    if arrival_time:
        span.start_ns = int(arrival_time * 1e9)

    return span


def set_latency_metrics(span, stats):
    """Set latency metrics from RequestStateStats."""
    if not stats:
        return

    if stats.first_token_latency:
        span.set_metric("vllm.latency.ttft", float(stats.first_token_latency))

    queued = stats.queued_ts
    scheduled = stats.scheduled_ts
    first_token = stats.first_token_ts
    last_token = stats.last_token_ts

    if queued and scheduled:
        span.set_metric("vllm.latency.queue", float(scheduled - queued))

    if scheduled and first_token:
        span.set_metric("vllm.latency.prefill", float(first_token - scheduled))

    if first_token and last_token and last_token > first_token:
        span.set_metric("vllm.latency.decode", float(last_token - first_token))

    if scheduled and last_token:
        span.set_metric("vllm.latency.inference", float(last_token - scheduled))


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
