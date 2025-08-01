import sys
from typing import Any

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.vllm import VLLMIntegration
from ddtrace.trace import Pin


log = get_logger(__name__)


def get_version() -> str:
    try:
        import vllm
        return getattr(vllm, "__version__", "")
    except ImportError:
        return ""


# Integration configuration
config._add("vllm", {})


def _extract_model_name(instance: Any) -> str:
    """Extract model name from vLLM instance."""
    log.debug("[VLLM DEBUG] _extract_model_name called with instance: %s", type(instance))
    
    # For AsyncLLMEngine: instance.engine.model_config.model
    if hasattr(instance, "engine") and hasattr(instance.engine, "model_config"):
        model = instance.engine.model_config.model
        log.debug("[VLLM DEBUG] Extracted model from AsyncLLMEngine: %s", model)
        return model
    
    # For LLMEngine: instance.model_config.model  
    if hasattr(instance, "model_config") and hasattr(instance.model_config, "model"):
        model = instance.model_config.model
        log.debug("[VLLM DEBUG] Extracted model from LLMEngine: %s", model)
        return model
    
    # For high-level LLM instances: instance.llm_engine.model_config.model
    if hasattr(instance, "llm_engine") and hasattr(instance.llm_engine, "model_config"):
        model = instance.llm_engine.model_config.model
        log.debug("[VLLM DEBUG] Extracted model from LLM: %s", model)
        return model
    
    log.debug("[VLLM DEBUG] No model found, returning 'unknown'")
    return "unknown"


@with_traced_module 
def traced_llm_engine_step(vllm, pin, func, instance, args, kwargs):
    """
    Trace LLMEngine.step() - the core request processing method.
    
    This is called by:
    - High-level LLM.generate() 
    - Direct LLMEngine usage
    - Ray Data batch processing
    
    We create spans for finished requests, similar to vLLM's native tracing.
    """
    log.debug("[VLLM DEBUG] traced_llm_engine_step called with instance: %s", type(instance))
    
    # Call the original step function first
    request_outputs = func(*args, **kwargs)
    
    # Process any finished requests and create spans for them
    _process_request_outputs(vllm, pin, request_outputs, instance)
    
    return request_outputs


@with_traced_module 
async def traced_async_llm_engine_step_async(vllm, pin, func, instance, args, kwargs):
    """
    Trace _AsyncLLMEngine.step_async() - the async request processing method.
    
    This is called by AsyncLLMEngine background loops for server usage.
    
    We create spans for finished requests, similar to vLLM's native tracing.
    """
    log.debug("[VLLM DEBUG] traced_async_llm_engine_step_async called with instance: %s", type(instance))
    
    # Call the original step_async function first
    request_outputs = await func(*args, **kwargs)
    
    # Process any finished requests and create spans for them
    _process_request_outputs(vllm, pin, request_outputs, instance)
    
    return request_outputs


def _process_request_outputs(vllm, pin, request_outputs, instance):
    """Process request outputs and create spans for finished requests."""
    if request_outputs:
        integration = vllm._datadog_integration
        model_name = _extract_model_name(instance)
        log.debug("[VLLM DEBUG] Processing %d request outputs, model: %s", len(request_outputs), model_name)
        
        for request_output in request_outputs:
            if request_output.finished:
                log.debug("[VLLM DEBUG] Creating span for finished request: %s", request_output.request_id)
                _create_span_for_finished_request(integration, pin, request_output, model_name, instance)


def _create_span_for_finished_request(integration, pin, request_output, model_name: str, engine_instance: Any):
    """Create a span for a finished vLLM request, similar to vLLM's native tracing."""
    
    # Extract provider (empty for now, could be enhanced)
    provider = "vllm"
    
    # Create the span with LLM Observability integration
    span = integration.trace(
        pin,
        "vllm.request",  # Use vllm.request to match vLLM's native span name
        provider=provider,
        model=model_name,
        submit_to_llmobs=True,
    )
    
    if span is None:
        return
    
    try:
        # Set basic span attributes
        span.set_tag_str("vllm.request.model", model_name)
        span.set_tag_str("vllm.request.id", request_output.request_id)
        
        # Set additional vLLM-specific tags
        if request_output.outputs:
            total_output_length = sum(len(output.text) for output in request_output.outputs)
            span.set_tag("vllm.response.output_length", total_output_length)
            span.set_tag("vllm.response.num_outputs", len(request_output.outputs))
        
        # Set usage metrics similar to vLLM's native tracing
        if hasattr(request_output, "metrics") and request_output.metrics:
            metrics_obj = request_output.metrics
            
            # Timing metrics
            if hasattr(metrics_obj, "arrival_time") and hasattr(metrics_obj, "first_token_time"):
                if metrics_obj.first_token_time and metrics_obj.arrival_time:
                    ttft = metrics_obj.first_token_time - metrics_obj.arrival_time
                    span.set_tag("vllm.latency.time_to_first_token", ttft)
            
            if hasattr(metrics_obj, "arrival_time") and hasattr(metrics_obj, "finished_time"):
                if metrics_obj.finished_time and metrics_obj.arrival_time:
                    e2e_time = metrics_obj.finished_time - metrics_obj.arrival_time  
                    span.set_tag("vllm.latency.end_to_end", e2e_time)
        
        # Use integration to set LLMObs tags (following Anthropic pattern)
        integration.llmobs_set_tags(
            span, 
            args=[], 
            kwargs={"engine_instance": engine_instance},
            response=request_output,
            operation="vllm_request"
        )
        
        log.debug("[VLLM DEBUG] Successfully created span for request %s", request_output.request_id)
        
    except Exception as e:
        log.debug("[VLLM DEBUG] Error creating span: %s", e)
        span.set_exc_info(*sys.exc_info())
    finally:
        span.finish()


# Extraction functions removed - now handled by VLLMIntegration class


def patch():
    """Patch vLLM methods for tracing - focusing on LLMEngine.step() for universal coverage."""
    log.debug("[VLLM DEBUG] Starting vLLM patch() function")
    try:
        import vllm
        log.debug("[VLLM DEBUG] Successfully imported vllm")
    except ImportError:
        log.debug("[VLLM DEBUG] Failed to import vllm")
        return

    if getattr(vllm, "_datadog_patch", False):
        log.debug("[VLLM DEBUG] vllm already patched, skipping")
        return

    vllm._datadog_patch = True
    Pin().onto(vllm)
    integration = VLLMIntegration(integration_config=config.vllm)
    vllm._datadog_integration = integration
    log.debug("[VLLM DEBUG] Set up vLLM integration")

    # Patch LLMEngine.step() - for direct engine usage and high-level LLM class
    try:
        wrap("vllm.engine.llm_engine", "LLMEngine.step", traced_llm_engine_step(vllm))
        log.debug("[VLLM DEBUG] Successfully patched LLMEngine.step")
    except (AttributeError, ImportError) as e:
        log.debug("[VLLM DEBUG] Failed to patch LLMEngine.step: %s", e)

    # Patch _AsyncLLMEngine.step_async() - for AsyncLLMEngine server usage  
    try:
        wrap("vllm.engine.async_llm_engine", "_AsyncLLMEngine.step_async", traced_async_llm_engine_step_async(vllm))
        log.debug("[VLLM DEBUG] Successfully patched _AsyncLLMEngine.step_async")
    except (AttributeError, ImportError) as e:
        log.debug("[VLLM DEBUG] Failed to patch _AsyncLLMEngine.step_async: %s", e)
    
    log.debug("[VLLM DEBUG] Finished vLLM patch() function")


def unpatch():
    """Remove vLLM patches."""
    try:
        import vllm
    except ImportError:
        return

    if not getattr(vllm, "_datadog_patch", False):
        return

    unwrap(vllm.engine.llm_engine.LLMEngine, "step")
    unwrap(vllm.engine.async_llm_engine._AsyncLLMEngine, "step_async")
    vllm._datadog_patch = False