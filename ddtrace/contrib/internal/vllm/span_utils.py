"""Span creation and LLMObs context utilities for vLLM integration."""

from __future__ import annotations

from typing import Any
from typing import Dict
from typing import Optional

from vllm.pooling_params import PoolingParams
from vllm.sequence import RequestMetrics
from vllm.sequence import SequenceGroup

from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.contrib.internal.vllm.data_extractors import select_prompt_for_span
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INTEGRATION
from ddtrace.llmobs._integrations.vllm import VLLMIntegration
from ddtrace.propagation.http import HTTPPropagator
from ddtrace._trace.pin import Pin
from ddtrace.trace import Context
from ddtrace.trace import Span
from ddtrace.trace import Tracer


log = get_logger(__name__)


def _parent_ctx(tracer: Tracer, seq_group: Optional[SequenceGroup]) -> Optional[Context]:
    """Determine parent context for a vLLM span.

    Prefers injected trace headers from seq_group (for v0 engine propagation),
    falls back to active context from tracer (for v1 async scenarios).
    """
    if seq_group and seq_group.trace_headers:
        return HTTPPropagator.extract(seq_group.trace_headers)

    active = tracer.context_provider.active()
    if isinstance(active, Span):
        return active.context
    elif isinstance(active, Context):
        return active
    return None


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
    params_arg_pos: Optional[int] = None,
    tokenizer: Optional[Any] = None,
) -> Optional[tuple]:
    """
    Comprehensive trace headers injection that handles:
    1. Trace context propagation
    2. Prompt injection from integration cache or direct args
    3. Both args and kwargs handling for different vLLM APIs
    """
    if not pin or not pin.tracer:
        return

    parent = pin.tracer.context_provider.active()

    # Determine if we're working with args or kwargs for trace_headers
    headers_in_args = trace_headers_arg_pos is not None and len(args) > trace_headers_arg_pos
    existing_headers = {}
    if headers_in_args:
        existing_headers = args[trace_headers_arg_pos] or {}
    else:
        existing_headers = kwargs.get("trace_headers") or {}

    # Start with existing headers and inject parent context
    headers = dict(existing_headers)
    if isinstance(parent, Span):
        HTTPPropagator.inject(parent.context, headers)
    elif isinstance(parent, Context):
        HTTPPropagator.inject(parent, headers)

    # Extract prompt directly from the provided prompt argument
    if prompt_arg_pos is not None:
        prompt_arg = args[prompt_arg_pos] if len(args) > prompt_arg_pos else kwargs.get("prompt")
        # Determine operation type
        is_embedding = False
        params_obj = None
        if params_arg_pos is not None:
            params_obj = args[params_arg_pos] if len(args) > params_arg_pos else kwargs.get("params")
        is_embedding = isinstance(params_obj, PoolingParams)
        # Decode for completion when possible
        text, token_ids, _ = select_prompt_for_span(prompt_arg, is_embedding=is_embedding, tokenizer=tokenizer)
        if text:
            headers["x-datadog-captured-prompt"] = str(text)
        elif token_ids:
            headers["x-datadog-captured-prompt"] = str(token_ids)

    # Update the appropriate location
    if headers_in_args and headers:
        args_list = list(args)
        args_list[trace_headers_arg_pos] = headers
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
