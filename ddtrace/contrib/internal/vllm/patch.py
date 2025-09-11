from typing import Optional, Any
import time

import vllm
from vllm.outputs import PoolingRequestOutput, RequestOutput
from vllm.transformers_utils.tokenizer import decode_tokens

from ddtrace import config
from ddtrace import tracer as dd_tracer
from ddtrace.contrib.trace_utils import wrap, unwrap, with_traced_module
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.vllm import VLLMIntegration
from ddtrace.trace import Pin

from .span_utils import (
    SpanConfig,
    create_vllm_span,
    set_latency_metrics,
    inject_trace_headers,
    apply_llmobs_context,
)
from .data_extractors import (
    RequestData, extract_v0_data, extract_v1_streaming_data, extract_offline_data,
    extract_captured_prompt, extract_model_name, extract_lora_name
)

log = get_logger(__name__)
config._add("vllm", {})


def get_integration() -> Optional[VLLMIntegration]:
    """Get vLLM integration instance."""
    return getattr(vllm, "_datadog_integration", None)


def get_tracer():
    """Get tracer from Pin or fallback to global tracer."""
    pin = Pin.get_from(vllm)
    if pin and pin.tracer:
        return pin.tracer
    return dd_tracer


def create_llmobs_kwargs(data: RequestData) -> dict:
    """Create standardized kwargs for LLMObs tagging."""
    return {
        "model_name": data.model_name,
        "prompt": data.prompt,
        "output_text": data.output_text,
        "input_tokens": data.input_tokens,
        "output_tokens": data.output_tokens,
        "sampling_params": data.sampling_params,
        "request_id": data.request_id,
        "finish_reason": data.finish_reason,
        "stop_reason": data.stop_reason,
        "num_cached_tokens": data.num_cached_tokens,
        "lora_name": data.lora_name,
        "embedding_dim": data.embedding_dim,
    }




@with_traced_module
def traced_v0_requestoutputfactory_create(vllm, pin, func, instance, args, kwargs):
    """V0: Create span when finished RequestOutput is created."""
    log.debug("[VLLM DEBUG] V0.create: entered")
    res = func(*args, **kwargs)
    
    if not res or not getattr(res, "finished", False):
        return res
    
    tracer = get_tracer()
    integration = get_integration()
    
    if not tracer:
        return res
    
    seq_group = args[0] if args else kwargs.get("seq_group")
    parent = tracer.current_span()
    
    
    # Extract data
    data = extract_v0_data(res, seq_group)
    
    # Get metrics for arrival time
    metrics_obj = getattr(seq_group, "metrics", None) if seq_group else None
    arrival_time = getattr(metrics_obj, "arrival_time", None) if metrics_obj else None
    
    # Create span
    span_config = SpanConfig(
        tracer=tracer,
        integration=integration,
        parent=parent,
        seq_group=seq_group,
        arrival_time=arrival_time
    )
    span = create_vllm_span(span_config)
    
    if not span:
        return res
    
    # Set LLMObs tags
    if integration and integration.llmobs_enabled:
        integration.llmobs_set_tags(
            span=span,
            args=[],
            kwargs=create_llmobs_kwargs(data),
            response=None,
        )
    
    # Set latency metrics
    set_latency_metrics(span, metrics_obj)
    
    span.finish()
    return res


@with_traced_module
def traced_asyncllm_generate(vllm, pin, func, instance, args, kwargs):
    """V1: Wrap AsyncLLM.generate with single span per request."""
    log.debug("[VLLM DEBUG] V1.generate: entered")
    prompt_arg = args[0] if args else kwargs.get("prompt")
    sampling_params = args[1] if len(args) > 1 else kwargs.get("sampling_params")
    request_id = args[2] if len(args) > 2 else kwargs.get("request_id")
    lora_request = args[3] if len(args) > 3 else kwargs.get("lora_request")
    
    tracer = get_tracer()
    integration = get_integration()
    
    if not tracer:
        return func(*args, **kwargs)
    
    parent = tracer.current_span()
    
    span_config = SpanConfig(
            tracer=tracer,
            integration=integration,
            parent=parent,
            model_name=extract_model_name(instance)
        )
    span = create_vllm_span(span_config)
    
    # Track streaming data
    outputs = []
    start_time = time.time()
    ttft_time = None
    finalized = False
    
    async def stream_wrapper():
        nonlocal ttft_time, finalized
        
        res = func(*args, **kwargs)
        if hasattr(res, "__await__"):
            res = await res
        
        async for out in res:
            outputs.append(out)
            
            # Calculate TTFT on first token
            if ttft_time is None:
                for comp in getattr(out, "outputs", None) or []:
                    token_ids = getattr(comp, "token_ids", None)
                    if token_ids and (isinstance(token_ids, int) or len(token_ids) > 0):
                        ttft_time = time.time() - start_time
                        break
            
            # Finalize when finished chunk observed to avoid unfinished spans
            if getattr(out, "finished", False) and not finalized:
                data = extract_v1_streaming_data(outputs)
                data.model_name = extract_model_name(instance)
                data.lora_name = extract_lora_name(lora_request)
                data.sampling_params = sampling_params
                data.request_id = request_id
                if not data.prompt:
                    data.prompt = extract_captured_prompt(parent) or prompt_arg
                if integration and integration.llmobs_enabled:
                    integration.llmobs_set_tags(
                        span=span,
                        args=[],
                        kwargs=create_llmobs_kwargs(data),
                        response=None,
                    )
                if ttft_time:
                    span.set_metric("vllm.latency.ttft", float(ttft_time))
                span.finish()
                finalized = True
                yield out
                return
            
            yield out
    
    async def finalize_span():
        # Extract accumulated data
        data = extract_v1_streaming_data(outputs)
        data.model_name = extract_model_name(instance)
        data.lora_name = extract_lora_name(lora_request)
        data.sampling_params = sampling_params
        data.request_id = request_id
        
        # Use captured prompt as fallback
        if not data.prompt:
            data.prompt = extract_captured_prompt(parent) or prompt_arg
        
        # Set LLMObs tags
        if integration and integration.llmobs_enabled:
            integration.llmobs_set_tags(
                span=span,
                args=[],
                kwargs=create_llmobs_kwargs(data),
                response=None,
            )
        
        # Set latency metrics
        if ttft_time:
            span.set_metric("vllm.latency.ttft", float(ttft_time))
        
        span.finish()
    
    async def execute():
        nonlocal finalized
        try:
            async for out in stream_wrapper():
                yield out
        except Exception as e:
            span.set_exc_info(type(e), e, e.__traceback__)
            raise
        finally:
            if not finalized:
                await finalize_span()
    
    return execute()


@with_traced_module
def traced_asyncllm_encode(vllm, pin, func, instance, args, kwargs):
    """V1: Wrap AsyncLLM.encode for embedding requests."""
    tracer = get_tracer()
    integration = get_integration()
    
    if not tracer:
        return func(*args, **kwargs)
    
    parent = tracer.current_span()
    span_config = SpanConfig(
        tracer=tracer,
        integration=integration,
        parent=parent,
        model_name=extract_model_name(instance)
    )
    span = create_vllm_span(span_config)
    
    if not span:
        return func(*args, **kwargs)
    
    start_time = time.time()
    outputs = []
    full_prompt_token_ids = None
    
    async def execute():
        nonlocal full_prompt_token_ids
        
        res = func(*args, **kwargs)
        if hasattr(res, "__await__"):
            res = await res
        
        async for out in res:
            outputs.append(out)
            if full_prompt_token_ids is None:
                ids = getattr(out, "prompt_token_ids", None)
                if ids:
                    full_prompt_token_ids = list(ids)
            yield out
        
        data = RequestData()
        data.model_name = extract_model_name(instance)
        
        for o in outputs:
            prompt_token_ids = getattr(o, "prompt_token_ids", None)
            if prompt_token_ids and not data.input_tokens:
                data.input_tokens = len(prompt_token_ids)
            
            out_obj = getattr(o, "outputs", None)
            if out_obj and hasattr(out_obj, "data"):
                data_attr = out_obj.data
                if hasattr(data_attr, "shape") and len(data_attr.shape) >= 1:
                    data.embedding_dim = int(data_attr.shape[-1])
        
        if integration:
            llmobs_kwargs = create_llmobs_kwargs(data)
            if full_prompt_token_ids:
                llmobs_kwargs["input"] = full_prompt_token_ids
                llmobs_kwargs["num_embeddings"] = 1
                if not llmobs_kwargs.get("input_tokens"):
                    llmobs_kwargs["input_tokens"] = len(full_prompt_token_ids)
            
            apply_llmobs_context(span, integration, llmobs_kwargs, operation="embedding")
            if integration.llmobs_enabled:
                integration.llmobs_set_tags(span, args=[], kwargs=llmobs_kwargs, response=None, operation="embedding")
        
        span.finish()
    
    return execute()


@with_traced_module
def traced_llm_generate(vllm, pin, func, instance, args, kwargs):
    """Offline LLM entrypoint: wrap LLM.generate."""
    tracer = get_tracer()
    integration = get_integration()
    
    if not tracer:
        return func(*args, **kwargs)
    
    prompts = args[0] if args else kwargs.get("prompts")
    sampling_params = args[1] if len(args) > 1 else kwargs.get("sampling_params")
    
    parent = tracer.current_span()
    start_time = time.time()
    
    outputs = func(*args, **kwargs)
    model_name = extract_model_name(getattr(instance, "llm_engine", None))
    
    # Create span for each output
    for output in outputs or []:
        data = extract_offline_data(output, prompts, model_name)
        data.sampling_params = sampling_params
        
        span_config = SpanConfig(
            tracer=tracer,
            integration=integration,
            parent=parent,
            model_name=model_name
        )
        span = create_vllm_span(span_config)
        
        if span:
            if integration and integration.llmobs_enabled:
                integration.llmobs_set_tags(
                    span=span,
                    args=[],
                    kwargs=create_llmobs_kwargs(data),
                    response=None,
                )
            
            span.finish()
    
    return outputs




@with_traced_module
def traced_openaiserving_log_inputs(vllm, pin, func, instance, args, kwargs):
    """Capture raw prompt for OpenAI entrypoints."""
    inputs = args[1] if len(args) > 1 else kwargs.get("inputs")
    
    captured_prompt = None
    if isinstance(inputs, str):
        captured_prompt = inputs
    elif isinstance(inputs, list) and inputs and isinstance(inputs[0], int):
        captured_prompt = inputs
    elif isinstance(inputs, dict):
        captured_prompt = inputs.get("prompt") or inputs.get("prompt_token_ids")
    
    if captured_prompt and pin and pin.tracer:
        parent = pin.tracer.current_span()
        if parent:
            parent._set_ctx_item("vllm.captured_prompt", captured_prompt)
    
    return func(*args, **kwargs)


@with_traced_module
async def traced_openai_stt_preprocess(vllm, pin, func, instance, args, kwargs):
    """Capture decoder prompt from STT preprocessing."""
    res = await func(*args, **kwargs)
    
    prompts, _ = res
    decoder_prompt = None
    
    if isinstance(prompts, list):
        for p in prompts:
            if isinstance(p, dict):
                decoder_prompt = p.get("decoder_prompt")
                if isinstance(decoder_prompt, str):
                    break
            elif isinstance(p, str):
                decoder_prompt = p
                break
    
    if decoder_prompt and pin and pin.tracer:
        parent = pin.tracer.current_span()
        if parent:
            parent._set_ctx_item("vllm.captured_prompt", decoder_prompt)
    
    return res


@with_traced_module
async def traced_v0_engine_add_request_async(vllm, pin, func, instance, args, kwargs):
    """Inject W3C trace headers for v0 engine async requests."""
    inject_trace_headers(pin, kwargs)
    return await func(*args, **kwargs)


@with_traced_module
async def traced_mq_client_process_request(vllm, pin, func, instance, args, kwargs):
    """Inject W3C trace headers for multiprocess client requests."""
    if pin and pin.tracer:
        parent = pin.tracer.current_span()
        if parent:
            headers = {}
            from ddtrace.propagation.http import HTTPPropagator
            HTTPPropagator.inject(parent.context, headers)
            if headers:
                # Check if trace_headers is passed as positional arg (index 4)
                if len(args) > 4 and args[4] is None:
                    # Replace None with headers in args
                    args = list(args)
                    args[4] = headers
                    args = tuple(args)
                elif "trace_headers" not in kwargs:
                    kwargs["trace_headers"] = headers
    
    res = func(*args, **kwargs)
    async for out in res:
        yield out


def patch():
    """Apply vLLM integration patches."""
    if getattr(vllm, "_datadog_patch", False):
        return
    
    vllm._datadog_patch = True
    
    Pin().onto(vllm)
    
    # Create integration
    integration = VLLMIntegration(integration_config=config.vllm)
    vllm._datadog_integration = integration
    
    log.debug("[VLLM DEBUG] patch: applying wrappers")
    # Apply patches
    wrap("vllm.outputs", "RequestOutputFactory.create", traced_v0_requestoutputfactory_create(vllm))
    wrap("vllm.v1.engine.async_llm", "AsyncLLM.generate", traced_asyncllm_generate(vllm))
    wrap("vllm.v1.engine.async_llm", "AsyncLLM.encode", traced_asyncllm_encode(vllm))
    wrap("vllm.entrypoints.openai.speech_to_text", "OpenAISpeechToText._preprocess_speech_to_text", traced_openai_stt_preprocess(vllm))
    wrap("vllm.entrypoints.openai.serving_engine", "OpenAIServing._log_inputs", traced_openaiserving_log_inputs(vllm))
    wrap("vllm.engine.async_llm_engine", "_AsyncLLMEngine.add_request_async", traced_v0_engine_add_request_async(vllm))
    wrap("vllm.engine.multiprocessing.client", "MQLLMEngineClient._process_request", traced_mq_client_process_request(vllm))
    wrap("vllm.entrypoints.llm", "LLM.generate", traced_llm_generate(vllm))
    log.debug("[VLLM DEBUG] patch: wrappers applied")


def unpatch():
    """Remove vLLM integration patches."""
    if not getattr(vllm, "_datadog_patch", False):
        return
    
    vllm._datadog_patch = False
    
    # Remove patches
    unwrap("vllm.outputs", "RequestOutputFactory.create")
    unwrap("vllm.v1.engine.async_llm", "AsyncLLM.generate")
    unwrap("vllm.v1.engine.async_llm", "AsyncLLM.encode")
    unwrap("vllm.entrypoints.openai.speech_to_text", "OpenAISpeechToText._preprocess_speech_to_text")
    unwrap("vllm.entrypoints.openai.serving_engine", "OpenAIServing._log_inputs")
    unwrap("vllm.engine.async_llm_engine", "_AsyncLLMEngine.add_request_async")
    unwrap("vllm.engine.multiprocessing.client", "MQLLMEngineClient._process_request")
    
    # Clear integration
    if hasattr(vllm, "_datadog_integration"):
        delattr(vllm, "_datadog_integration")
