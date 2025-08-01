import sys
from typing import Any
from typing import Dict
from typing import Optional

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations.vllm import VLLMIntegration
from ddtrace.trace import Pin


def get_version() -> str:
    try:
        import vllm
        return getattr(vllm, "__version__", "")
    except ImportError:
        return ""


def _supported_versions():
    return {"vllm": ">=0.3.0"}


config._add("vllm", {})


def _extract_model_name(instance: Any) -> str:
    """Extract model name from vLLM instance."""
    # Try to get model name from various attributes
    if hasattr(instance, "model_config") and hasattr(instance.model_config, "model"):
        return instance.model_config.model
    elif hasattr(instance, "engine_config") and hasattr(instance.engine_config, "model_config"):
        return instance.engine_config.model_config.model
    elif hasattr(instance, "model"):
        return instance.model
    return "unknown"


def _get_provider_and_model(instance: Any, kwargs: Dict[str, Any]) -> tuple[str, str]:
    """Extract provider and model information from vLLM instance."""
    model_name = _extract_model_name(instance)
    
    # Extract provider from model path only if clearly indicated
    if "/" in model_name:
        # For models like "meta-llama/Llama-2-7b-hf", provider would be "meta-llama"
        provider = model_name.split("/")[0]
    else:
        # For standalone models like "gpt2", "gpt2-medium", we don't know the provider
        provider = ""
    
    return provider, model_name


@with_traced_module
def traced_async_llm_engine_generate(vllm, pin, func, instance, args, kwargs):
    """Trace AsyncLLMEngine.generate() - the main async request entry point."""
    integration = vllm._datadog_integration
    provider, model = _get_provider_and_model(instance, kwargs)
    
    span = integration.trace(
        pin,
        "llm_request",
        provider=provider,
        model=model,
        submit_to_llmobs=True,
    )

    # Set span tags matching vLLM semantics
    span.set_tag_str("vllm.request.model", model)
    if provider:  # Only set provider if we know it
        span.set_tag_str("vllm.request.provider", provider)
    
    # Add request-specific tags matching vLLM's native tracing
    if len(args) > 0:  # prompt is first arg
        span.set_tag("vllm.request.prompt_length", len(str(args[0])))
    
    if len(args) > 1:  # sampling_params is second arg
        sampling_params = args[1]
        if hasattr(sampling_params, 'max_tokens') and sampling_params.max_tokens is not None:
            span.set_tag("vllm.request.max_tokens", sampling_params.max_tokens)
        if hasattr(sampling_params, 'temperature') and sampling_params.temperature is not None:
            span.set_tag("vllm.request.temperature", sampling_params.temperature)
        if hasattr(sampling_params, 'top_p') and sampling_params.top_p is not None:
            span.set_tag("vllm.request.top_p", sampling_params.top_p)
        if hasattr(sampling_params, 'n') and sampling_params.n is not None:
            span.set_tag("vllm.request.n", sampling_params.n)
    
    # Add model configuration from instance
    if hasattr(instance, 'model_config'):
        model_config = instance.model_config
        if hasattr(model_config, 'max_model_len'):
            span.set_tag("vllm.model.max_model_len", model_config.max_model_len)
        if hasattr(model_config, 'dtype'):
            span.set_tag("vllm.model.dtype", str(model_config.dtype))
    
    try:
        # Call the original generate method - returns async generator
        result = func(*args, **kwargs)
        
        # AsyncLLMEngine.generate() always returns an async generator
        # Wrap it to capture final result and add tracing
        async def traced_async_generator():
            final_result = None
            token_count = 0
            try:
                async for item in result:
                    final_result = item
                    token_count += 1
                    yield item
            except Exception as e:
                span.set_exc_info(*sys.exc_info())
                raise
            finally:
                # Add completion metrics matching vLLM's native tracing
                span.set_tag("vllm.response.token_count", token_count)
                
                if final_result:
                    if hasattr(final_result, 'finished'):
                        span.set_tag("vllm.response.finished", final_result.finished)
                    
                    # Add token usage metrics
                    if hasattr(final_result, 'prompt_token_ids'):
                        span.set_tag("vllm.usage.prompt_tokens", len(final_result.prompt_token_ids))
                    
                    if hasattr(final_result, 'outputs') and final_result.outputs:
                        total_output_tokens = sum(len(output.token_ids) if hasattr(output, 'token_ids') else 0 
                                                for output in final_result.outputs)
                        span.set_tag("vllm.usage.completion_tokens", total_output_tokens)
                        
                        # Calculate total tokens
                        prompt_tokens = len(final_result.prompt_token_ids) if hasattr(final_result, 'prompt_token_ids') else 0
                        span.set_tag("vllm.usage.total_tokens", prompt_tokens + total_output_tokens)
                
                kwargs["instance"] = instance
                integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=final_result, operation="llm")
                span.finish()
        
        return traced_async_generator()
    except Exception:
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise


@with_traced_module 
def traced_llm_engine_step(vllm, pin, func, instance, args, kwargs):
    """Trace LLMEngine.step() - synchronous llm_request processing."""
    integration = vllm._datadog_integration
    
    # Get model info from the engine instance
    provider, model = _get_provider_and_model(instance, kwargs)
    
    span = integration.trace(
        pin,
        "llm_request", 
        provider=provider,
        model=model,
        submit_to_llmobs=True,
    )
    
    # Add engine-specific tags
    if hasattr(instance, 'scheduler_config'):
        span.set_tag("vllm.scheduler.policy", getattr(instance.scheduler_config, 'policy', 'unknown'))
    
    try:
        result = func(*args, **kwargs)
        
        # Extract information from completed requests in the result
        if result and isinstance(result, list):
            span.set_tag("vllm.step.completed_requests", len(result))
            
            # Get metrics from the first completed request if available
            if result and hasattr(result[0], 'metrics'):
                metrics = result[0].metrics
                if hasattr(metrics, 'arrival_time') and hasattr(metrics, 'finished_time'):
                    latency = metrics.finished_time - metrics.arrival_time
                    span.set_tag("vllm.request.e2e_latency", latency)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        kwargs["instance"] = instance
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="llm")
        span.finish()
    
    return result


# Removed traced_async_engine_step_async - replaced with AsyncLLMEngine.generate() patching for proper trace context


def patch():
    """Patch vLLM methods for tracing - focusing on core llm_request operation first."""
    try:
        import vllm
    except ImportError:
        return
    
    if getattr(vllm, "_datadog_patch", False):
        return
    
    vllm._datadog_patch = True
    Pin().onto(vllm)
    integration = VLLMIntegration(integration_config=config.vllm)
    vllm._datadog_integration = integration
    
    # Focus on the main request entry points to get proper trace context
    
    # 1. Patch AsyncLLMEngine.generate() - the main async request entry point
    try:
        wrap("vllm.engine.async_llm_engine", "AsyncLLMEngine.generate", traced_async_llm_engine_generate(vllm))
    except (AttributeError, ImportError):
        pass  # AsyncLLMEngine.generate for most common usage
    
    # 2. Keep synchronous LLMEngine.step() for direct LLMEngine usage (less common)
    try:
        wrap("vllm.engine.llm_engine", "LLMEngine.step", traced_llm_engine_step(vllm))
    except (AttributeError, ImportError):
        pass  # LLMEngine.step for sync usage
    
    # TODO: Consider adding other method patches for completeness:
    # - vllm.LLM.generate (sync offline usage)
    # - vllm.LLM.encode (sync embedding usage) 
    # - vllm.AsyncLLMEngine.encode (async embedding usage)


def unpatch():
    """Remove vLLM patches."""
    try:
        import vllm
    except ImportError:
        return
    
    if not getattr(vllm, "_datadog_patch", False):
        return
    
    vllm._datadog_patch = False
    
    # Unpatch both sync and async llm_request methods
    
    # 1. Unpatch AsyncLLMEngine.generate() - main async entry point
    try:
        from vllm.engine.async_llm_engine import AsyncLLMEngine
        unwrap(AsyncLLMEngine, "generate")
    except (AttributeError, ImportError):
        pass
    
    # 2. Unpatch synchronous LLMEngine.step()
    try:
        from vllm.engine.llm_engine import LLMEngine
        unwrap(LLMEngine, "step")
    except (AttributeError, ImportError):
        pass
    
    if hasattr(vllm, "_datadog_integration"):
        delattr(vllm, "_datadog_integration") 