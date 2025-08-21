from typing import Any, Mapping, Optional

from ddtrace.ext import SpanTypes
from ddtrace.llmobs._constants import INTEGRATION
from ddtrace.constants import _SPAN_MEASURED_KEY
import vllm

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
    
    # Inject trace context for span linking (no prompt data needed)
    current_span = tracer.current_span()
    if current_span:
        trace_headers = kwargs.get("trace_headers", {})
        if not isinstance(trace_headers, dict):
            trace_headers = {}
            
        # Inject current span context for proper trace linking
        HTTPPropagator.inject(current_span.context, trace_headers)
        kwargs["trace_headers"] = trace_headers
        log.debug("[VLLM DEBUG] Injected trace context for span linking")
    else:
        log.debug("[VLLM DEBUG] No current span found, skipping trace injection")

    # Call original function
    return func(*args, **kwargs)


@with_traced_module
def traced_process_model_outputs(vllm, pin, func, instance, args, kwargs):
    """
    Trace LLMEngine._process_model_outputs to create comprehensive spans for finished sequences.
    
    This function intercepts vLLM's internal model output processing to create detailed
    tracing spans that capture the full lifecycle of LLM requests. Here's how it works:
    
    1. **Request Flow Context**: vLLM processes requests through a scheduler that queues
       them for execution. The _process_model_outputs method is called when model outputs
       are ready to be processed and converted into final responses.
    
    2. **Trace Propagation**: When a request enters vLLM (via traced_generate), we inject
       trace headers into the request context. These headers travel with the request through
       vLLM's internal pipeline and are available when the request finishes.
    
    3. **Span Creation Timing**: We create spans only when sequences are finished because:
       - We have complete information about the request/response
       - We can calculate accurate token usage metrics
       - We can capture the full generated output
       - We can set proper timing information from arrival to completion
    
    4. **Data Extraction**: We capture the scheduler output data before vLLM processes it
       to ensure we have access to all sequence group information, even after internal
       mutations occur.
    
    5. **LLMObs Integration**: For each finished sequence group, we create a span with
       comprehensive metadata including model parameters, input/output data, and metrics
       that follow the LLMObs standard for observability.
    """
    # Grab ctx (SchedulerContext) from positional or keyword args BEFORE calling the original method
    ctx = kwargs.get("ctx")
    if ctx is None and args:
        ctx = args[0]

    # Capture the first queue element (scheduler_outputs lives inside) *before* it is popped by the
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

        # Get model name from the engine instance for span tagging
        model_name = getattr(getattr(instance, 'model_config', None), 'model', None)

        # Get the vLLM integration instance for LLMObs processing
        integration: VLLMIntegration = vllm._datadog_integration

        for scheduled_seq_group in scheduler_outputs.scheduled_seq_groups:
            seq_group = scheduled_seq_group.seq_group

            if not seq_group.is_finished():
                continue  # only create spans for completed requests

            # Trace headers propagated from client
            trace_headers = getattr(seq_group, "trace_headers", None)
            if not trace_headers:
                continue

            log.debug("[VLLM DEBUG] Creating vllm.request span ; trace_headers=%s", trace_headers)

            parent_ctx = HTTPPropagator.extract(trace_headers)
            if parent_ctx and parent_ctx.trace_id:
                log.debug("[VLLM DEBUG] Extracted parent context ; trace_id=%s span_id=%s", parent_ctx.trace_id, parent_ctx.span_id)
        
            if parent_ctx and parent_ctx.trace_id:
                span = pin.tracer.start_span(
                    "vllm.request",
                    child_of=parent_ctx,
                    resource="vllm.request",
                    span_type=SpanTypes.LLM if integration.llmobs_enabled else None,
                    activate=False
                )
                if integration.llmobs_enabled:
                    span._set_ctx_item(INTEGRATION, integration._integration_name)
                span.set_metric(_SPAN_MEASURED_KEY, 1)
                integration._set_base_span_tags(span, model_name=model_name)
            else:
                span = integration.trace(
                    pin,
                    "vllm.request",
                    submit_to_llmobs=True,
                    model_name=model_name
                )
            span.start_ns = int(seq_group.metrics.arrival_time * 1e9)
            
            # Set LLMObs tags
            integration._llmobs_set_tags(
                span=span,
                args=[],
                kwargs={"model_name": model_name, "engine_instance": instance},
                response=seq_group,
                operation="completion"
            )
            
            span.finish()
            log.debug("[VLLM DEBUG] Span created trace_id=%s span_id=%s", parent_ctx.trace_id, span.span_id)
    else:
        log.debug("[VLLM DEBUG] No output_data captured or malformed entry, skipping span creation")

    return result


def patch():
    """
    Patch vLLM for comprehensive tracing and observability.
    
    This function sets up tracing for vLLM by patching key entry points and internal
    methods to provide end-to-end observability of LLM requests. The patching strategy:
    
    1. **Entry Point Tracing**: We patch all major vLLM entry points (LLM.generate,
       AsyncLLMEngine.generate, etc.) to inject trace context into requests as they
       enter the vLLM pipeline.
    
    2. **Internal Processing Tracing**: We patch LLMEngine._process_model_outputs to
       create detailed spans when requests complete, capturing comprehensive metadata
       about the request/response cycle.
    
    3. **Integration Setup**: We create a VLLMIntegration instance that handles
       LLMObs-specific data extraction and span tagging following Datadog's LLM
       observability standards.
    
    The result is complete visibility into vLLM operations including:
    - Request timing and lifecycle
    - Model parameters and configuration
    - Input prompts and output generations
    - Token usage metrics
    - Error conditions and performance data
    """
    log.debug("[VLLM DEBUG] Loading vLLM integration")
    if getattr(vllm, "_datadog_patch", False):
        return

    vllm._datadog_patch = True

    Pin().onto(vllm)
    integration = VLLMIntegration(integration_config=config.vllm)
    vllm._datadog_integration = integration

    # Patch all generate methods to inject trace context
    log.debug("[VLLM DEBUG] Patching vLLM")
    wrap("vllm.entrypoints.llm", "LLM.generate", traced_generate(vllm))
    wrap("vllm.v1.engine.async_llm", "AsyncLLM.generate", traced_generate(vllm))
    wrap("vllm.engine.protocol", "EngineClient.generate", traced_generate(vllm))
    wrap("vllm.engine.multiprocessing.client", "MQLLMEngineClient.generate", traced_generate(vllm))
    wrap("vllm.engine.async_llm_engine", "AsyncLLMEngine.generate", traced_generate(vllm))
    
    # Patch _process_model_outputs to create comprehensive spans
    wrap("vllm.engine.llm_engine", "LLMEngine._process_model_outputs", traced_process_model_outputs(vllm))
    log.debug("[VLLM DEBUG] Patched vLLM")


def unpatch():
    """Remove vLLM patches."""
    if not getattr(vllm, "_datadog_patch", False):
        return
    
    vllm._datadog_patch = False

    unwrap("vllm.entrypoints.llm", "LLM.generate")
    unwrap("vllm.v1.engine.async_llm", "AsyncLLM.generate")
    unwrap("vllm.engine.protocol", "EngineClient.generate")
    unwrap("vllm.engine.multiprocessing.client", "MQLLMEngineClient.generate")
    unwrap("vllm.engine.async_llm_engine", "AsyncLLMEngine.generate")
    unwrap("vllm.engine.llm_engine", "LLMEngine._process_model_outputs")

    delattr(vllm, "_datadog_integration")