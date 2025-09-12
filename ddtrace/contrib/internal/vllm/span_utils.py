"""Span creation and LLMObs context utilities for vLLM integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INTEGRATION
from ddtrace.propagation.http import HTTPPropagator
from vllm.sequence import RequestMetrics

log = get_logger(__name__)


@dataclass(frozen=True)
class SpanConfig:
    tracer: Any
    integration: Any
    parent: Optional[Any] = None
    seq_group: Optional[Any] = None
    model_name: Optional[str] = None
    arrival_time: Optional[float] = None


def _parent_ctx_from_config(cfg: SpanConfig) -> Optional[Any]:
    if cfg.parent:
        return cfg.parent.context

    if cfg.seq_group and cfg.seq_group.trace_headers:
        log.debug("[VLLM DD] create_vllm_span: found trace_headers")
        return HTTPPropagator.extract(cfg.seq_group.trace_headers)

    log.debug("[VLLM DD] create_vllm_span: creating root span")
    return None


def _resolve_model_name(cfg: SpanConfig) -> Optional[str]:
    if cfg.model_name:
        return cfg.model_name

    if cfg.seq_group and cfg.seq_group.trace_headers:
        hdrs = cfg.seq_group.trace_headers
        if hdrs and "x-datadog-vllm-model" in hdrs:
            return hdrs["x-datadog-vllm-model"]

    return None


def create_vllm_span(cfg: SpanConfig) -> Optional[Any]:
    if not cfg.tracer:
        return None

    span = cfg.tracer.start_span(
        "vllm.request",
        child_of=_parent_ctx_from_config(cfg),
        resource="vllm.request",
        span_type=SpanTypes.LLM,
        activate=False,
    )

    if cfg.integration:
        span._set_ctx_item(INTEGRATION, cfg.integration._integration_name)

    span.set_metric(_SPAN_MEASURED_KEY, 1)

    if cfg.arrival_time:
        # ns precision expected by ddtrace core
        span.start_ns = int(cfg.arrival_time * 1e9)

    model_name = _resolve_model_name(cfg)
    if model_name and cfg.integration:
        cfg.integration._set_base_span_tags(span, model_name=model_name)
        log.debug("[VLLM DD] create_vllm_span: tagged model_name=%s", model_name)

    req_id = cfg.seq_group.request_id if cfg.seq_group else None
    if req_id:
        span.set_tag_str("vllm.request.id", str(req_id))

    return span


def set_latency_metrics(span: Any, metrics: RequestMetrics, start_time: Optional[float] = None) -> None:
    if not metrics:
        if start_time:
            # last-ditch e2e for callers that tracked their own clock
            import time as _t
            span.set_metric("vllm.latency.e2e", float(_t.time() - start_time))
        return

    arrival = metrics.arrival_time
    first_token = metrics.first_token_time
    finished = metrics.finished_time
    queue = metrics.time_in_queue

    if arrival and first_token:
        span.set_metric("vllm.latency.ttft", float(first_token - arrival))
    if arrival and finished:
        span.set_metric("vllm.latency.e2e", float(finished - arrival))
    elif start_time:
        import time as _t
        span.set_metric("vllm.latency.e2e", float(_t.time() - start_time))
    if queue:
        span.set_metric("vllm.latency.queue", float(queue))


def inject_trace_headers(pin: Any, kwargs: Dict[str, Any]) -> None:
    if kwargs.get("trace_headers") is not None or not pin or not pin.tracer:
        return

    parent = pin.tracer.current_span()
    if not parent:
        return

    headers: Dict[str, str] = {}
    HTTPPropagator.inject(parent.context, headers)

    captured = parent._get_ctx_item("vllm.captured_prompt")
    if isinstance(captured, str) and captured:
        headers["x-datadog-captured-prompt"] = captured

    if headers:
        kwargs["trace_headers"] = headers


def apply_llmobs_context(span: Any, integration: Any, kwargs: dict, operation: str = "completion") -> None:
    if not span or not integration:
        return

    if operation == "embedding":
        ctx = integration._build_embedding_context(kwargs)
    else:
        ctx = integration._build_completion_context(kwargs)

    span._set_ctx_items(ctx)
    if integration and integration.llmobs_enabled:
        integration._set_supplemental_tags(span, kwargs)
    log.debug("[VLLM DD] apply_llmobs_context: op=%s", operation)
