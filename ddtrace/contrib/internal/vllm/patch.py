from typing import Any, Mapping, Optional

from ddtrace import config, tracer
from ddtrace.contrib.internal.trace_utils import unwrap, wrap, with_traced_module
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.vllm import VLLMIntegration
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Pin


log = get_logger(__name__)


# Integration configuration
config._add("vllm", {})


@with_traced_module
def traced_generate(vllm, pin, func, instance, args, kwargs):
    """
    Trace generate methods across all vLLM entry points.
    """
    # Get class name for logging
    class_name = instance.__class__.__name__
    log.debug("[VLLM DEBUG] Starting generate from %s", class_name)
    
    # If we have a current span, inject trace headers
    current_span = tracer.current_span()
    if current_span:
        # Create trace headers if they don't exist
        trace_headers = kwargs.get("trace_headers", {})
        if not isinstance(trace_headers, dict):
            trace_headers = {}
            
        # Inject current span context into headers
        HTTPPropagator.inject(current_span.context, trace_headers)
        log.debug("[VLLM DEBUG] %s injected trace_headers=%s", class_name, trace_headers)
            
        # Update kwargs with trace headers
        kwargs["trace_headers"] = trace_headers
        
    # Call original function
    return func(*args, **kwargs)


@with_traced_module
def traced_process_model_outputs(vllm, pin, func, instance, args, kwargs):
    """
    Trace LLMEngine._process_model_outputs to create spans for finished sequences.
    """
    # Grab ctx (SchedulerContext) from positional or keyword args BEFORE calling the original method
    ctx = kwargs.get("ctx")
    if ctx is None and args:
        ctx = args[0]

    # Capture the first queue element ( scheduler_outputs lives inside ) *before* it is popped by the
    # original implementation. This ensures we still have access to the data after the original call
    output_data = None
    if ctx is not None and getattr(ctx, "output_queue", None):
        try:
            output_data = ctx.output_queue[0]
        except Exception:
            output_data = None

    # Call the original implementation (this may mutate ctx.output_queue)
    result = func(*args, **kwargs)

    # If we were able to snapshot an entry from the queue, inspect it now **after** core processing is done
    if output_data and len(output_data) >= 3:
        _, _, scheduler_outputs = output_data[:3]

        for scheduled_seq_group in scheduler_outputs.scheduled_seq_groups:
            seq_group = scheduled_seq_group.seq_group

            if not seq_group.is_finished():
                continue  # only finish spans for completed requests

            # Trace headers propagated from client
            trace_headers = getattr(seq_group, "trace_headers", None)
            if not trace_headers:
                continue

            log.debug("[VLLM DEBUG] Creating vllm.request span ; trace_headers=%s", trace_headers)

            parent_ctx = None
            try:
                parent_ctx = HTTPPropagator.extract(trace_headers)
            except Exception as exc:
                log.debug("[VLLM DEBUG] Failed to extract parent context: %s", exc)

            if parent_ctx and parent_ctx.trace_id:
                try:
                    span = tracer.start_span("vllm.request", child_of=parent_ctx)
                    span.start_ns = int(seq_group.metrics.arrival_time * 1e9)
                    span.finish()
                    log.debug("[VLLM DEBUG] Span created trace_id=%s span_id=%s", parent_ctx.trace_id, span.span_id)
                except Exception as exc:
                    log.debug("[VLLM DEBUG] Error while creating span: %s", exc)
    else:
        log.debug("[VLLM DEBUG] No output_data captured or malformed entry, skipping span creation")

    return result


def patch():
    """Patch vLLM for tracing."""
    try:
        import vllm
        from vllm.entrypoints.llm import LLM
        from vllm.v1.engine.async_llm import AsyncLLM
        from vllm.engine.protocol import EngineClient
        from vllm.engine.multiprocessing.client import MQLLMEngineClient
        from vllm.engine.async_llm_engine import AsyncLLMEngine
        from vllm.engine.llm_engine import LLMEngine
    except ImportError:
        return

    if getattr(vllm, "_datadog_patch", False):
        return

    vllm._datadog_patch = True
    Pin().onto(vllm)
    integration = VLLMIntegration(integration_config=config.vllm)
    vllm._datadog_integration = integration

    # Patch all generate methods
    wrap("vllm.entrypoints.llm", "LLM.generate", traced_generate(vllm))
    wrap("vllm.v1.engine.async_llm", "AsyncLLM.generate", traced_generate(vllm))
    wrap("vllm.engine.protocol", "EngineClient.generate", traced_generate(vllm))
    wrap("vllm.engine.multiprocessing.client", "MQLLMEngineClient.generate", traced_generate(vllm))
    wrap("vllm.engine.async_llm_engine", "AsyncLLMEngine.generate", traced_generate(vllm))
    
    # Patch _process_model_outputs to create spans
    wrap("vllm.engine.llm_engine", "LLMEngine._process_model_outputs", traced_process_model_outputs(vllm))


def unpatch():
    """Remove vLLM patches."""
    try:
        import vllm
        from vllm.entrypoints.llm import LLM
        from vllm.v1.engine.async_llm import AsyncLLM
        from vllm.engine.protocol import EngineClient
        from vllm.engine.multiprocessing.client import MQLLMEngineClient
        from vllm.engine.async_llm_engine import AsyncLLMEngine
        from vllm.engine.llm_engine import LLMEngine
    except ImportError:
        return

    if not getattr(vllm, "_datadog_patch", False):
        return

    unwrap(LLM, "generate")
    unwrap(AsyncLLM, "generate")
    unwrap(EngineClient, "generate")
    unwrap(MQLLMEngineClient, "generate")
    unwrap(AsyncLLMEngine, "generate")
    unwrap(LLMEngine, "_process_model_outputs")
    vllm._datadog_patch = False