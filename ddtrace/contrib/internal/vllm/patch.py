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
    
    # Extract provider from model path if available
    if "/" in model_name:
        # For models like "meta-llama/Llama-2-7b-hf", provider would be "meta-llama"
        provider = model_name.split("/")[0]
    else:
        provider = "vllm"
    
    return provider, model_name


# Temporarily removed LLM and AsyncLLMEngine methods to focus on core llm_request tracing
# TODO: Add back LLM.generate, LLM.encode, AsyncLLMEngine.generate, AsyncLLMEngine.encode once core works


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


@with_traced_module 
async def traced_async_engine_step_async(vllm, pin, func, instance, args, kwargs):
    """Trace _AsyncLLMEngine.step_async() - asynchronous llm_request processing."""
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
        result = await func(*args, **kwargs)
        
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
    
    # Focus on the critical llm_request operation - patch both sync and async methods
    
    # 1. Patch synchronous LLMEngine.step() for direct LLMEngine usage
    try:
        wrap("vllm.engine.llm_engine", "LLMEngine.step", traced_llm_engine_step(vllm))
    except (AttributeError, ImportError):
        pass  # LLMEngine.step for sync usage
    
    # 2. Patch asynchronous _AsyncLLMEngine.step_async() for AsyncLLMEngine usage  
    try:
        wrap("vllm.engine.async_llm_engine", "_AsyncLLMEngine.step_async", traced_async_engine_step_async(vllm))
    except (AttributeError, ImportError):
        pass  # _AsyncLLMEngine.step_async for async usage
    
    # TODO: Investigate other method patches once core llm_request tracing is working:
    # - vllm.LLM.generate (sync)
    # - vllm.LLM.encode (sync) 
    # - vllm.AsyncLLMEngine.generate (async)
    # - vllm.AsyncLLMEngine.encode (async)


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
    
    # 1. Unpatch synchronous LLMEngine.step()
    try:
        from vllm.engine.llm_engine import LLMEngine
        unwrap(LLMEngine, "step")
    except (AttributeError, ImportError):
        pass
    
    # 2. Unpatch asynchronous _AsyncLLMEngine.step_async()
    try:
        from vllm.engine.async_llm_engine import _AsyncLLMEngine
        unwrap(_AsyncLLMEngine, "step_async")
    except (AttributeError, ImportError):
        pass
    
    if hasattr(vllm, "_datadog_integration"):
        delattr(vllm, "_datadog_integration") 