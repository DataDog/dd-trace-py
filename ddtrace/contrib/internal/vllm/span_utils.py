"""Span creation and LLMObs context utilities for vLLM integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INTEGRATION
from ddtrace.propagation.http import HTTPPropagator
from vllm.sequence import RequestMetrics, SequenceGroup
from ddtrace.trace import Context, Pin, Span, Tracer
from ddtrace.llmobs._integrations.vllm import VLLMIntegration
from vllm.pooling_params import PoolingParams

log = get_logger(__name__)


def _parent_ctx(tracer: Tracer, seq_group: Optional[SequenceGroup]) -> Optional[Context]:
    # Prefer injected trace headers from seq_group if present (e.g., MQ or v0 engine header propagation)
    if seq_group and seq_group.trace_headers:
        log.debug("[VLLM DD] create_vllm_span: found trace_headers")
        return HTTPPropagator.extract(seq_group.trace_headers)

    # Fall back to the tracer's current span if any
    cur = tracer.current_span()
    return cur.context if cur else None


def create_vllm_span(
    tracer: Tracer,
    integration: VLLMIntegration,
    model_name: str,
    seq_group: Optional[SequenceGroup] = None,
    arrival_time: Optional[float] = None,
) -> Span:
    span = tracer.start_span(
        "vllm.request",
        child_of=_parent_ctx(tracer, seq_group),
        resource="vllm.request",
        span_type=SpanTypes.LLM,
        activate=False,
    )

    span._set_ctx_item(INTEGRATION, integration._integration_name)
    span.set_metric(_SPAN_MEASURED_KEY, 1)

    if arrival_time:
        # ns precision expected by ddtrace core
        span.start_ns = int(arrival_time * 1e9)

    integration._set_base_span_tags(span, model_name=model_name)
    log.debug("[VLLM DD] create_vllm_span: tagged model_name=%s", model_name)

    return span


def set_latency_metrics(span: Span, metrics: RequestMetrics) -> None:
    if not metrics:
        return

    arrival = metrics.arrival_time
    first_token = metrics.first_token_time
    queue = metrics.time_in_queue

    if arrival and first_token:
        span.set_metric("vllm.latency.ttft", float(first_token - arrival))

    if queue:
        span.set_metric("vllm.latency.queue", float(queue))


def inject_trace_headers(
    pin: Pin,
    integration: Optional[VLLMIntegration],
    args: tuple,
    kwargs: Dict[str, Any],
    request_id_arg_pos: Optional[int] = None,
    prompt_arg_pos: Optional[int] = None,
    trace_headers_arg_pos: Optional[int] = None,
) -> Optional[tuple]:
    """
    Comprehensive trace headers injection that handles:
    1. Trace context propagation
    2. Prompt injection from integration cache or direct args
    3. Both args and kwargs handling for different vLLM APIs
    """
    if not pin or not pin.tracer:
        return

    parent = pin.tracer.current_span()
    if not parent:
        return

    # Determine if we're working with args or kwargs for trace_headers
    headers_in_args = trace_headers_arg_pos is not None and len(args) > trace_headers_arg_pos
    existing_headers = {}
    
    if headers_in_args:
        existing_headers = args[trace_headers_arg_pos] or {}
    else:
        existing_headers = kwargs.get("trace_headers") or {}

    # Start with existing headers
    headers = dict(existing_headers)
    HTTPPropagator.inject(parent.context, headers)
    
    # Try to get prompt from various sources
    prompt_to_inject = None
    
    # 1. From parent span context
    captured = parent._get_ctx_item("vllm.captured_prompt")
    if isinstance(captured, str) and captured:
        prompt_to_inject = captured
    
    # 2. From integration cache using request_id
    if not prompt_to_inject and integration and request_id_arg_pos is not None:
        req_id = None
        if len(args) > request_id_arg_pos:
            req_id = args[request_id_arg_pos]
        else:
            req_id = kwargs.get("request_id")
        
        if req_id:
            cached_prompt = integration.get_captured_prompt(str(req_id))
            if cached_prompt:
                prompt_to_inject = cached_prompt
    
    # 3. From direct prompt argument (for sync add_request)
    if not prompt_to_inject and prompt_arg_pos is not None and len(args) > prompt_arg_pos:
        if isinstance(args[prompt_arg_pos], str):
            prompt_to_inject = args[prompt_arg_pos]
    
    # Add prompt to headers if found
    if prompt_to_inject:
        headers["x-datadog-captured-prompt"] = prompt_to_inject
    
    # Update the appropriate location
    if headers_in_args and headers:
        # Modify args tuple
        args_list = list(args)
        args_list[trace_headers_arg_pos] = headers
        # Return modified args for caller to use
        return tuple(args_list)
    elif headers:
        kwargs["trace_headers"] = headers


def cache_headers_for_pooling_params(
    instance: Any,
    args: tuple,
    kwargs: Dict[str, Any],
    params_arg_pos: int = 2,
) -> None:
    """Cache trace headers for PoolingParams requests since vLLM v0 doesn't propagate them."""
    params = args[params_arg_pos] if len(args) > params_arg_pos else kwargs.get("params")
    if isinstance(params, PoolingParams):
        req_id = args[0] if args else kwargs.get("request_id")
        if req_id and kwargs.get("trace_headers"):
            pending = getattr(instance, "_dd_pending_trace_headers", {})
            pending[req_id] = dict(kwargs["trace_headers"])
            setattr(instance, "_dd_pending_trace_headers", pending)