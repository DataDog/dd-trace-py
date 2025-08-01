import sys
from typing import Any
from typing import Dict
from typing import Optional

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations.vllm import VLLMIntegration
from ddtrace.trace import Pin


log = get_logger(__name__)


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
    log.debug("[VLLM DEBUG] _extract_model_name called with instance: %s", type(instance))
    log.debug("[VLLM DEBUG] instance attributes: %s", [attr for attr in dir(instance) if not attr.startswith('_')][:10])
    
    # For high-level LLM instances (vllm.LLM)
    if hasattr(instance, "llm_engine"):
        log.debug("[VLLM DEBUG] Found llm_engine attribute")
        engine = instance.llm_engine
        if hasattr(engine, "model_config") and hasattr(engine.model_config, "model"):
            model = engine.model_config.model
            log.debug("[VLLM DEBUG] Extracted model from llm_engine.model_config.model: %s", model)
            return model
        elif hasattr(engine, "engine_config") and hasattr(engine.engine_config, "model_config"):
            model = engine.engine_config.model_config.model
            log.debug("[VLLM DEBUG] Extracted model from llm_engine.engine_config.model_config.model: %s", model)
            return model
    
    # For engine instances directly
    if hasattr(instance, "model_config") and hasattr(instance.model_config, "model"):
        model = instance.model_config.model
        log.debug("[VLLM DEBUG] Extracted model from model_config.model: %s", model)
        return model
    elif hasattr(instance, "engine_config") and hasattr(instance.engine_config, "model_config"):
        model = instance.engine_config.model_config.model
        log.debug("[VLLM DEBUG] Extracted model from engine_config.model_config.model: %s", model)
        return model
    elif hasattr(instance, "model"):
        model = instance.model
        log.debug("[VLLM DEBUG] Extracted model from model attribute: %s", model)
        return model
    
    log.debug("[VLLM DEBUG] No model found, returning 'unknown'")
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
    log.debug("[VLLM DEBUG] traced_async_llm_engine_generate called with instance: %s", type(instance))
    integration = vllm._datadog_integration
    provider, model = _get_provider_and_model(instance, kwargs)
    log.debug("[VLLM DEBUG] async extracted model: %s, provider: %s", model, provider)
    log.debug("[VLLM DEBUG] async args: %d, kwargs keys: %s", len(args), list(kwargs.keys()))
    
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
    log.debug("[VLLM DEBUG] traced_llm_engine_step called with instance: %s", type(instance))
    integration = vllm._datadog_integration
    
    # Get model info from the engine instance
    provider, model = _get_provider_and_model(instance, kwargs)
    log.debug("[VLLM DEBUG] step extracted model: %s, provider: %s", model, provider)
    
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
def traced_llm_generate(vllm, pin, func, instance, args, kwargs):
    """Trace LLM.generate() - the main offline inference API with rich input/output."""
    log.debug("[VLLM DEBUG] traced_llm_generate called with instance: %s", type(instance))
    integration = vllm._datadog_integration
    provider, model = _get_provider_and_model(instance, kwargs)
    log.debug("[VLLM DEBUG] extracted model: %s, provider: %s", model, provider)
    
    span = integration.trace(
        pin,
        "llm_request",
        provider=provider,
        model=model,
        submit_to_llmobs=True,
    )

    # Set span tags matching vLLM semantics  
    span.set_tag_str("vllm.request.model", model)
    if provider:
        span.set_tag_str("vllm.request.provider", provider)
    
    # Extract prompts and sampling params from high-level API
    prompts = args[0] if len(args) > 0 else kwargs.get('prompts')
    sampling_params = args[1] if len(args) > 1 else kwargs.get('sampling_params')
    
    # Add request-specific tags
    if prompts is not None:
        if isinstance(prompts, (list, tuple)):
            span.set_tag("vllm.request.num_prompts", len(prompts))
            if len(prompts) > 0:
                span.set_tag("vllm.request.first_prompt_length", len(str(prompts[0])))
        else:
            span.set_tag("vllm.request.prompt_length", len(str(prompts)))
    
    if sampling_params is not None:
        if hasattr(sampling_params, 'temperature') and sampling_params.temperature is not None:
            span.set_tag("vllm.request.temperature", sampling_params.temperature)
        if hasattr(sampling_params, 'max_tokens') and sampling_params.max_tokens is not None:
            span.set_tag("vllm.request.max_tokens", sampling_params.max_tokens)
        if hasattr(sampling_params, 'top_p') and sampling_params.top_p is not None:
            span.set_tag("vllm.request.top_p", sampling_params.top_p)

    # Add model configuration from instance
    if hasattr(instance, 'llm_engine') and hasattr(instance.llm_engine, 'model_config'):
        model_config = instance.llm_engine.model_config
        if hasattr(model_config, 'max_model_len'):
            span.set_tag("vllm.model.max_model_len", model_config.max_model_len)
        if hasattr(model_config, 'dtype'):
            span.set_tag("vllm.model.dtype", str(model_config.dtype))

    try:
        # Call the original generate method - returns list[RequestOutput]
        outputs = func(*args, **kwargs)
        
        # Extract rich output data from RequestOutput objects
        if outputs:
            total_prompt_tokens = 0
            total_completion_tokens = 0
            total_requests = len(outputs)
            finished_count = 0
            
            for output in outputs:
                if hasattr(output, 'prompt_token_ids') and output.prompt_token_ids:
                    total_prompt_tokens += len(output.prompt_token_ids)
                
                if hasattr(output, 'outputs') and output.outputs:
                    for completion in output.outputs:
                        if hasattr(completion, 'token_ids') and completion.token_ids:
                            total_completion_tokens += len(completion.token_ids)
                        if hasattr(completion, 'finished') and completion.finished():
                            finished_count += 1
            
            # Set usage metrics
            if total_prompt_tokens > 0:
                span.set_tag("vllm.usage.prompt_tokens", total_prompt_tokens)
            if total_completion_tokens > 0:
                span.set_tag("vllm.usage.completion_tokens", total_completion_tokens)
            if total_prompt_tokens > 0 or total_completion_tokens > 0:
                span.set_tag("vllm.usage.total_tokens", total_prompt_tokens + total_completion_tokens)
            
            span.set_tag("vllm.response.num_outputs", total_requests)
            span.set_tag("vllm.response.finished_count", finished_count)
        
        # Set LLMObs tags with rich data
        kwargs["instance"] = instance
        kwargs["prompts"] = prompts  # Add original prompts for LLMObs
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=outputs, operation="llm")
        span.finish()
        return outputs
    except Exception:
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise


@with_traced_module
def traced_llm_chat(vllm, pin, func, instance, args, kwargs):
    """Trace LLM.chat() - chat completions with message structure."""
    integration = vllm._datadog_integration
    provider, model = _get_provider_and_model(instance, kwargs)
    
    span = integration.trace(
        pin,
        "llm_request",
        provider=provider,
        model=model,
        submit_to_llmobs=True,
    )

    # Set span tags
    span.set_tag_str("vllm.request.model", model)
    if provider:
        span.set_tag_str("vllm.request.provider", provider)
    span.set_tag("vllm.request.type", "chat")
    
    # Extract messages from chat API
    messages = args[0] if len(args) > 0 else kwargs.get('messages')
    if messages is not None:
        if isinstance(messages, list) and len(messages) > 0:
            if isinstance(messages[0], list):  # list of conversations
                span.set_tag("vllm.request.num_conversations", len(messages))
                span.set_tag("vllm.request.num_messages", len(messages[0]))
            else:  # single conversation
                span.set_tag("vllm.request.num_messages", len(messages))

    try:
        outputs = func(*args, **kwargs)
        
        # Add chat-specific response metrics
        if outputs:
            span.set_tag("vllm.response.num_outputs", len(outputs))
        
        kwargs["instance"] = instance
        kwargs["messages"] = messages  # Add original messages for LLMObs
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=outputs, operation="llm")
        span.finish()
        return outputs
    except Exception:
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise


@with_traced_module  
def traced_llm_encode(vllm, pin, func, instance, args, kwargs):
    """Trace LLM.encode() - embeddings with pooling."""
    integration = vllm._datadog_integration
    provider, model = _get_provider_and_model(instance, kwargs)
    
    span = integration.trace(
        pin,
        "embedding_request", 
        provider=provider,
        model=model,
        submit_to_llmobs=True,
    )

    # Set span tags
    span.set_tag_str("vllm.request.model", model)
    if provider:
        span.set_tag_str("vllm.request.provider", provider)
    span.set_tag("vllm.request.type", "embedding")
    
    # Extract prompts for embedding
    prompts = args[0] if len(args) > 0 else kwargs.get('prompts')
    if prompts is not None:
        if isinstance(prompts, (list, tuple)):
            span.set_tag("vllm.request.num_prompts", len(prompts))
        else:
            span.set_tag("vllm.request.num_prompts", 1)

    try:
        outputs = func(*args, **kwargs)
        
        if outputs:
            span.set_tag("vllm.response.num_embeddings", len(outputs))
            # Try to extract embedding dimensions from first output
            if hasattr(outputs[0], 'outputs') and outputs[0].outputs:
                if hasattr(outputs[0].outputs, 'embedding') and outputs[0].outputs.embedding:
                    span.set_tag("vllm.response.embedding_dim", len(outputs[0].outputs.embedding))
        
        kwargs["instance"] = instance
        kwargs["prompts"] = prompts  # Add original prompts for LLMObs
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=outputs, operation="embedding")
        span.finish()
        return outputs
    except Exception:
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise


# Removed traced_async_engine_step_async - replaced with AsyncLLMEngine.generate() patching for proper trace context


def patch():
    """Patch vLLM methods for tracing - focusing on high-level APIs for rich input/output."""
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

    # Patch high-level APIs for rich input/output data
    
    # 1. vLLM.LLM.generate() - offline inference with rich prompts and RequestOutput
    try:
        wrap("vllm.entrypoints.llm", "LLM.generate", traced_llm_generate(vllm))
        log.debug("[VLLM DEBUG] Successfully patched LLM.generate")
    except (AttributeError, ImportError) as e:
        log.debug("[VLLM DEBUG] Failed to patch LLM.generate: %s", e)
    
    # 2. vLLM.LLM.chat() - chat completions with message structure  
    try:
        wrap("vllm.entrypoints.llm", "LLM.chat", traced_llm_chat(vllm))
        log.debug("[VLLM DEBUG] Successfully patched LLM.chat")
    except (AttributeError, ImportError) as e:
        log.debug("[VLLM DEBUG] Failed to patch LLM.chat: %s", e)
    
    # 3. vLLM.LLM.encode() - embeddings
    try:
        wrap("vllm.entrypoints.llm", "LLM.encode", traced_llm_encode(vllm))
        log.debug("[VLLM DEBUG] Successfully patched LLM.encode")
    except (AttributeError, ImportError) as e:
        log.debug("[VLLM DEBUG] Failed to patch LLM.encode: %s", e)

    # 4. AsyncLLMEngine.generate() - for server/async usage (keep for trace context)
    try:
        wrap("vllm.engine.async_llm_engine", "AsyncLLMEngine.generate", traced_async_llm_engine_generate(vllm))
        log.debug("[VLLM DEBUG] Successfully patched AsyncLLMEngine.generate")
    except (AttributeError, ImportError) as e:
        log.debug("[VLLM DEBUG] Failed to patch AsyncLLMEngine.generate: %s", e)

    # 5. Keep LLMEngine.step() for low-level direct usage  
    try:
        wrap("vllm.engine.llm_engine", "LLMEngine.step", traced_llm_engine_step(vllm))
        log.debug("[VLLM DEBUG] Successfully patched LLMEngine.step")
    except (AttributeError, ImportError) as e:
        log.debug("[VLLM DEBUG] Failed to patch LLMEngine.step: %s", e)
    
    log.debug("[VLLM DEBUG] Finished vLLM patch() function")


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