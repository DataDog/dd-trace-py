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
    """Extract model name from vLLM LLMEngine instance."""
    # For LLMEngine: instance.model_config.model  
    if hasattr(instance, "model_config") and hasattr(instance.model_config, "model"):
        return instance.model_config.model
    
    return "unknown"


@with_traced_module 
def traced_do_tracing(vllm, pin, func, instance, args, kwargs):
    """
    Trace LLMEngine.do_tracing() - the universal entry point for vLLM's native tracing.
    
    This method is called by:
    - LLMEngine.step() (for direct engine usage and high-level LLM.generate())
    - _AsyncLLMEngine.step_async() (for AsyncLLMEngine servers)
    
    We override vLLM's native tracing to create Datadog spans instead.
    This gives us universal coverage across all vLLM usage patterns.
    """
    # Get arguments: scheduler_outputs, finished_before=None
    scheduler_outputs = args[0] if args else None
    finished_before = args[1] if len(args) > 1 else kwargs.get('finished_before')
    
    if scheduler_outputs is None:
        return
    
    integration = vllm._datadog_integration
    model_name = _extract_model_name(instance)
    
    # Process finished sequence groups (following vLLM's native logic)
    for idx, scheduled_seq_group in enumerate(scheduler_outputs.scheduled_seq_groups):
        # Skip double tracing when using async output proc (following vLLM logic)
        if finished_before and idx in finished_before:
            continue
        
        seq_group = scheduled_seq_group.seq_group
        if seq_group.is_finished():
            log.debug("[VLLM DEBUG] Creating span for finished seq_group: %s", seq_group.request_id)
            _create_span_for_finished_seq_group(integration, pin, seq_group, model_name, instance)


def _create_span_for_finished_seq_group(integration, pin, seq_group, model_name: str, engine_instance: Any):
    """Create a span for a finished vLLM sequence group, following vLLM's native tracing pattern."""
    
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
        # Set custom start time from arrival_time (EXACTLY like vLLM native tracing)
        if hasattr(seq_group, "metrics") and seq_group.metrics:
            if hasattr(seq_group.metrics, "arrival_time") and seq_group.metrics.arrival_time:
                # Convert arrival_time to nanoseconds (following vLLM pattern)
                arrival_time_ns = int(seq_group.metrics.arrival_time * 1e9)
                span.start_ns = arrival_time_ns
                log.debug("[VLLM DEBUG] Set span start time to arrival_time: %s", arrival_time_ns)
        
        # Set basic span attributes
        span.set_tag_str("vllm.request.model", model_name)
        span.set_tag_str("vllm.request.id", seq_group.request_id)
        
        # Set usage metrics following vLLM's native tracing pattern
        if hasattr(seq_group, "metrics") and seq_group.metrics:
            metrics_obj = seq_group.metrics
            
            # Timing metrics (following vLLM's native tracing)
            if hasattr(metrics_obj, "arrival_time") and hasattr(metrics_obj, "first_token_time"):
                if metrics_obj.first_token_time and metrics_obj.arrival_time:
                    ttft = metrics_obj.first_token_time - metrics_obj.arrival_time
                    span.set_tag("vllm.latency.time_to_first_token", ttft)
            
            if hasattr(metrics_obj, "arrival_time") and hasattr(metrics_obj, "finished_time"):
                if metrics_obj.finished_time and metrics_obj.arrival_time:
                    e2e_time = metrics_obj.finished_time - metrics_obj.arrival_time  
                    span.set_tag("vllm.latency.end_to_end", e2e_time)
        
        # Set token counts
        if hasattr(seq_group, "prompt_token_ids"):
            span.set_tag("vllm.usage.prompt_tokens", len(seq_group.prompt_token_ids))
        
        # Get completion tokens from finished sequences
        if hasattr(seq_group, "get_finished_seqs"):
            finished_seqs = seq_group.get_finished_seqs()
            completion_tokens = sum(seq.get_output_len() for seq in finished_seqs)
            span.set_tag("vllm.usage.completion_tokens", completion_tokens)
        
        # Use integration to set LLMObs tags (following Anthropic pattern)
        # Create a synthetic request_output-like object for the integration
        synthetic_request_output = type('RequestOutput', (), {
            'request_id': seq_group.request_id,
            'finished': True,
            'prompt': getattr(seq_group, 'prompt', ''),
            'prompt_token_ids': getattr(seq_group, 'prompt_token_ids', []),
            'outputs': [],  # We'll populate this if possible
            'metrics': getattr(seq_group, 'metrics', None)
        })()
        
        # Try to get actual outputs if available
        if hasattr(seq_group, 'get_finished_seqs'):
            finished_seqs = seq_group.get_finished_seqs()
            synthetic_outputs = []
            for seq in finished_seqs:
                output_obj = type('CompletionOutput', (), {
                    'text': getattr(seq, 'output_text', ''),
                    'token_ids': getattr(seq, 'output_token_ids', []),
                    'finish_reason': getattr(seq, 'finish_reason', None)
                })()
                synthetic_outputs.append(output_obj)
            synthetic_request_output.outputs = synthetic_outputs
        
        integration.llmobs_set_tags(
            span, 
            args=[], 
            kwargs={"engine_instance": engine_instance},
            response=synthetic_request_output,
            operation="vllm_request"
        )
        
        log.debug("[VLLM DEBUG] Successfully created span for seq_group %s", seq_group.request_id)
        
    except Exception as e:
        log.debug("[VLLM DEBUG] Error creating span: %s", e)
        span.set_exc_info(*sys.exc_info())
    finally:
        span.finish()


def patch():
    """Patch vLLM methods for tracing - ONLY do_tracing() for universal coverage."""
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

    # Patch ONLY do_tracing() - this is the universal method called by all usage patterns
    try:
        wrap("vllm.engine.llm_engine", "LLMEngine.do_tracing", traced_do_tracing(vllm))
        log.debug("[VLLM DEBUG] Successfully patched LLMEngine.do_tracing")
    except (AttributeError, ImportError) as e:
        log.debug("[VLLM DEBUG] Failed to patch LLMEngine.do_tracing: %s", e)
    
    log.debug("[VLLM DEBUG] Finished vLLM patch() function")


def unpatch():
    """Remove vLLM patches."""
    try:
        import vllm
    except ImportError:
        return

    if not getattr(vllm, "_datadog_patch", False):
        return

    unwrap(vllm.engine.llm_engine.LLMEngine, "do_tracing")
    vllm._datadog_patch = False