"""Span creation and management utilities for vLLM integration."""

from typing import Optional, Dict, Any
import time

from ddtrace.ext import SpanTypes
from ddtrace.llmobs._constants import INTEGRATION
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.internal.logger import get_logger

log = get_logger(__name__)


class SpanConfig:
    """Configuration for creating vLLM spans."""
    
    def __init__(
        self,
        tracer,
        integration,
        parent=None,
        seq_group=None,
        model_name: Optional[str] = None,
        arrival_time: Optional[float] = None
    ):
        self.tracer = tracer
        self.integration = integration
        self.parent = parent
        self.seq_group = seq_group
        self.model_name = model_name
        self.arrival_time = arrival_time


def create_vllm_span(config: SpanConfig) -> Optional[object]:
    """Create a standardized vLLM span with proper configuration."""
    if not config.tracer:
        return None
    
    # Determine parent context
    parent_ctx = None
    if config.parent:
        parent_ctx = config.parent.context
    elif config.seq_group and hasattr(config.seq_group, 'trace_headers'):
        trace_headers = getattr(config.seq_group, 'trace_headers', {})
        if trace_headers:
            log.debug("[VLLM DD] create_vllm_span: found trace_headers keys=%s", list(trace_headers.keys()))
            # Accept either W3C headers (traceparent/tracestate) or Datadog headers (x-datadog-trace-id/parent-id)
            parent_ctx = HTTPPropagator.extract(trace_headers)
    else:
        log.debug("[VLLM DD] create_vllm_span: no parent or trace_headers; creating root span")
    
    # Create span
    span = config.tracer.start_span(
        "vllm.request",
        child_of=parent_ctx,
        resource="vllm.request",
        span_type=SpanTypes.LLM,
        activate=False,
    )
    
    # Set integration context
    if config.integration:
        span._set_ctx_item(INTEGRATION, config.integration._integration_name)
    
    span.set_metric(_SPAN_MEASURED_KEY, 1)
    
    # Set arrival time if available
    if config.arrival_time:
        span.start_ns = int(float(config.arrival_time) * 1e9)
    
    # Set base model tags
    if config.integration:
        model_name = config.model_name
        # Fallback: adopt model name propagated via trace headers
        if not model_name and config.seq_group and hasattr(config.seq_group, 'trace_headers'):
            hdrs = getattr(config.seq_group, 'trace_headers') or {}
            model_name = hdrs.get('x-datadog-vllm-model')
        # Final fallback: global model name set at server init
        if not model_name:
            try:
                import vllm as _v
                model_name = getattr(_v, '_dd_model_name', None)
            except Exception:
                model_name = None
        if model_name:
            config.integration._set_base_span_tags(span, model_name=model_name)
            log.debug("[VLLM DD] create_vllm_span: tagged model_name=%s", model_name)
        else:
            log.debug("[VLLM DD] create_vllm_span: no model_name to tag")
    # Attach request_id to span for correlation
    try:
        req_id = getattr(config.seq_group, 'request_id', None)
        if req_id:
            span.set_tag_str('vllm.request.id', str(req_id))
    except Exception:
        pass
    
    return span




def set_latency_metrics(span, metrics_obj, start_time: Optional[float] = None):
    """Set latency metrics on span from vLLM metrics object."""
    if not metrics_obj:
        return
    
    arrival = getattr(metrics_obj, "arrival_time", None)
    first_token_time = getattr(metrics_obj, "first_token_time", None)
    finished_time = getattr(metrics_obj, "finished_time", None)
    time_in_queue = getattr(metrics_obj, "time_in_queue", None)
    
    if arrival and first_token_time:
        span.set_metric("vllm.latency.ttft", float(first_token_time - arrival))
    
    if arrival and finished_time:
        span.set_metric("vllm.latency.e2e", float(finished_time - arrival))
    elif start_time:
        span.set_metric("vllm.latency.e2e", float(time.time() - start_time))
    
    if time_in_queue:
        span.set_metric("vllm.latency.queue", float(time_in_queue))


def inject_trace_headers(pin, kwargs: Dict[str, Any]):
    """Inject W3C trace headers into kwargs if not already present."""
    if kwargs.get("trace_headers") is not None:
        return
    
    parent = pin.tracer.current_span()
    if not parent:
        return
    
    headers: Dict[str, str] = {}
    HTTPPropagator.inject(parent.context, headers)
    # Also propagate captured prompt for later LLMObs input reconstruction
    captured_prompt = parent._get_ctx_item("vllm.captured_prompt")
    if isinstance(captured_prompt, str) and captured_prompt:
        headers["x-datadog-captured-prompt"] = captured_prompt
    if headers:
        kwargs["trace_headers"] = headers


def apply_llmobs_context(span, integration, kwargs: dict, operation: str = "completion"):
    """Apply LLMObs context to span using integration builders.
    
    Uses integration context builders to avoid duplication.
    """
    if not span or not integration:
        return
    if operation == "embedding" and hasattr(integration, "_build_embedding_context"):
        context = integration._build_embedding_context(kwargs)
    elif hasattr(integration, "_build_completion_context"):
        context = integration._build_completion_context(kwargs)
    else:
        return
    span._set_ctx_items(context)
    if hasattr(integration, "_set_supplemental_tags"):
        integration._set_supplemental_tags(span, kwargs)
    log.debug("[VLLM DD] apply_llmobs_context: op=%s ctx_keys=%s", operation, list(context.keys()))
