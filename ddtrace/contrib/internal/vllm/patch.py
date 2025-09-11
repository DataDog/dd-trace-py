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



def _safe_wrap(mod: str, name: str, wrapper) -> bool:
    """Safely wrap; tolerate missing attributes across engine versions."""
    try:
        wrap(mod, name, wrapper)
        return True
    except Exception as e:
        log.debug("[VLLM DD] safe_wrap: skip %s.%s (%s)", mod, name, e)
        return False


def _safe_unwrap(mod: str, name: str) -> bool:
    """Safely unwrap; tolerate missing attributes or not wrapped."""
    try:
        unwrap(mod, name)
        return True
    except Exception as e:
        log.debug("[VLLM DD] safe_unwrap: skip %s.%s (%s)", mod, name, e)
        return False



@with_traced_module
def traced_llmengine_do_tracing(vllm, pin, func, instance, args, kwargs):
    """V0: Create ddtrace vllm.request spans during LLMEngine.do_tracing using helpers."""
    log.debug("[VLLM DD] do_tracing: entered")
    res = func(*args, **kwargs)

    tracer = get_tracer()
    integration = get_integration()
    if not tracer:
        return res

    scheduler_outputs = args[0] if args else kwargs.get("scheduler_outputs")
    finished_before = set(args[1] if len(args) > 1 else (kwargs.get("finished_before") or []))
    groups = getattr(scheduler_outputs, "scheduled_seq_groups", []) or []
    log.debug("[VLLM DD] do_tracing: groups=%d finished_before=%d", len(groups), len(finished_before))

    for idx, scheduled_seq_group in enumerate(groups):
        if finished_before and idx in finished_before:
            log.debug("[VLLM DD] do_tracing: skip idx=%d (finished_before)", idx)
            continue
        seq_group = scheduled_seq_group.seq_group
        # For pooling requests, propagate trace headers saved on add_request
        if getattr(seq_group, "trace_headers", None) is None and hasattr(instance, "_dd_pending_trace_headers"):
            try:
                req_id = getattr(seq_group, "request_id", None)
                if req_id and str(req_id) in instance._dd_pending_trace_headers:
                    seq_group.trace_headers = instance._dd_pending_trace_headers.pop(str(req_id))
            except Exception:
                pass
        if not seq_group.is_finished():
            log.debug("[VLLM DD] do_tracing: skip idx=%d (not finished)", idx)
            continue

        metrics_obj = getattr(seq_group, "metrics", None)
        arrival_time = getattr(metrics_obj, "arrival_time", None) if metrics_obj else None

        # Create span via helper; prefer parent from trace_headers, fallback to current span
        has_headers = bool(getattr(seq_group, 'trace_headers', None))
        parent_ctx_span = None if has_headers else tracer.current_span()
        span_config = SpanConfig(
            tracer=tracer,
            integration=integration,
            parent=parent_ctx_span,
            seq_group=seq_group,
            model_name=getattr(getattr(instance, "model_config", None), "model", None),
            arrival_time=arrival_time,
        )
        span = create_vllm_span(span_config)
        if not span:
            log.debug("[VLLM DD] do_tracing: span not created for request_id=%s", getattr(seq_group, 'request_id', None))
            continue
        log.debug(
            "[VLLM DD] do_tracing: span created request_id=%s headers_keys=%s model=%s",
            getattr(seq_group, 'request_id', None),
            list((getattr(seq_group, 'trace_headers', {}) or {}).keys()),
            span_config.model_name,
        )

        # Build LLMObs kwargs
        data = RequestData(
            request_id=seq_group.request_id,
            model_name=span_config.model_name,
            prompt=(getattr(seq_group, "trace_headers", {}) or {}).get("x-datadog-captured-prompt") or getattr(seq_group, "prompt", None),
        )
        # Common metrics
        data.input_tokens = len(getattr(seq_group, "prompt_token_ids", []) or [])
        is_embedding = getattr(seq_group, "pooling_params", None) is not None

        if not is_embedding:
            # Completion: accumulate text and output tokens
            output_text_parts = []
            for seq in seq_group.get_finished_seqs():
                data.output_tokens += int(seq.get_output_len())
                if getattr(seq, "output_text", None):
                    output_text_parts.append(seq.output_text)
            data.output_text = "".join(output_text_parts)
            llmobs_kwargs = create_llmobs_kwargs(data)
            operation = "completion"
        else:
            # Embedding: set INPUT_DOCUMENTS from prompt token ids and OUTPUT_VALUE summary
            llmobs_kwargs = create_llmobs_kwargs(data)
            prompt_token_ids = getattr(seq_group, "prompt_token_ids", None) or getattr(seq_group, "encoder_prompt_token_ids", None)
            if prompt_token_ids is not None:
                llmobs_kwargs["input"] = list(prompt_token_ids)
                # ensure input_tokens reflects ids length
                llmobs_kwargs["input_tokens"] = len(prompt_token_ids)
            # embedding dimension from pooled_data if available
            embedding_dim = None
            pooled = getattr(seq_group, "pooled_data", None)
            try:
                shape = getattr(pooled, "shape", None)
                if shape is not None and hasattr(shape, "__len__") and len(shape) >= 1:
                    embedding_dim = int(shape[-1])
            except Exception:
                pass
            if embedding_dim is not None:
                llmobs_kwargs["embedding_dim"] = embedding_dim
            llmobs_kwargs["num_embeddings"] = 1
            operation = "embedding"

        if integration and integration.llmobs_enabled:
            integration.llmobs_set_tags(span, args=[], kwargs=llmobs_kwargs, response=None, operation=operation)

        # Latency metrics
        set_latency_metrics(span, metrics_obj)
        log.debug("[VLLM DD] do_tracing: span finished request_id=%s", data.request_id)
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
    request_id = args[0] if len(args) > 0 else kwargs.get("request_id")
    inputs = args[1] if len(args) > 1 else kwargs.get("inputs")
    
    captured_prompt = None
    if isinstance(inputs, str):
        captured_prompt = inputs
    elif isinstance(inputs, list) and inputs and isinstance(inputs[0], int):
        captured_prompt = inputs
    elif isinstance(inputs, dict):
        captured_prompt = inputs.get("prompt") or inputs.get("prompt_token_ids")
    
    if captured_prompt:
        # Save on current span for same-task retrieval
        if pin and pin.tracer:
            parent = pin.tracer.current_span()
            if parent:
                parent._set_ctx_item("vllm.captured_prompt", captured_prompt)
        # Persist globally by request id for cross-task retrieval
        try:
            if request_id:
                cache = getattr(vllm, "_dd_captured_prompts", None)
                if cache is None:
                    cache = {}
                    setattr(vllm, "_dd_captured_prompts", cache)
                cache[str(request_id)] = captured_prompt
        except Exception:
            pass
    
    result = func(*args, **kwargs)
    try:
        # For chat/completion, attempt to resolve final prompt from instance state
        # and store it under the request id cache for later fallback.
        if request_id and captured_prompt is None:
            state = getattr(instance, "models", None)
            # Best-effort; if not available, skip.
            cache = getattr(vllm, "_dd_captured_prompts", None)
            if cache is None:
                cache = {}
                setattr(vllm, "_dd_captured_prompts", cache)
            # Don't overwrite if we already have one
            cache.setdefault(str(request_id), inputs if isinstance(inputs, str) else None)
    except Exception:
        pass
    return result


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
    # Inject trace headers for parent linkage
    inject_trace_headers(pin, kwargs)
    # Also propagate model name via trace headers for downstream tagging
    headers = kwargs.get("trace_headers") or {}
    model_name = extract_model_name(instance)
    if model_name:
        headers["x-datadog-vllm-model"] = model_name
    # Add captured prompt from global cache if present
    try:
        req_id = args[0] if len(args) > 0 else kwargs.get("request_id")
        cache = getattr(vllm, "_dd_captured_prompts", None)
        if req_id and cache and str(req_id) in cache:
            headers.setdefault("x-datadog-captured-prompt", cache[str(req_id)])
    except Exception:
        pass
    if headers:
        kwargs["trace_headers"] = headers
    log.debug("[VLLM DD] add_request_async: request_id=%s model=%s headers_keys=%s", kwargs.get("request_id"), model_name, list(headers.keys()))
    return await func(*args, **kwargs)


@with_traced_module
def traced_v0_engine_add_request(vllm, pin, func, instance, args, kwargs):
    """Inject trace headers for v0 sync add_request (offline)."""
    headers = kwargs.get("trace_headers") or {}
    inject_trace_headers(pin, kwargs)
    # merge any prior headers after inject to preserve trace context
    if headers:
        merged = kwargs.get("trace_headers") or {}
        merged.update(headers)
        kwargs["trace_headers"] = merged
    # add model name
    model_name = extract_model_name(instance)
    if model_name:
        kwargs.setdefault("trace_headers", {})["x-datadog-vllm-model"] = model_name
    # attach prompt if clearly available
    if len(args) > 1 and isinstance(args[1], str):
        kwargs["trace_headers"]["x-datadog-captured-prompt"] = args[1]
    # store headers per request_id for pooling path fix-up
    try:
        req_id = args[0] if len(args) > 0 else kwargs.get("request_id")
        if req_id:
            pending = getattr(instance, "_dd_pending_trace_headers", None)
            if pending is None:
                pending = {}
                setattr(instance, "_dd_pending_trace_headers", pending)
            pending[str(req_id)] = dict(kwargs.get("trace_headers") or {})
    except Exception:
        pass
    log.debug("[VLLM DD] add_request(sync): request_id=%s model=%s headers_keys=%s",
              args[0] if len(args) > 0 else kwargs.get("request_id"),
              model_name,
              list((kwargs.get("trace_headers") or {}).keys()))
    return func(*args, **kwargs)

@with_traced_module
async def traced_mq_client_process_request(vllm, pin, func, instance, args, kwargs):
    """Inject W3C trace headers for multiprocess client requests."""
    if pin and pin.tracer:
        parent = pin.tracer.current_span()
        if parent:
            from ddtrace.propagation.http import HTTPPropagator
            arg_has = len(args) > 4 and args[4] is not None
            kw_has = ("trace_headers" in kwargs and kwargs["trace_headers"] is not None)
            # Start with existing mapping or a new dict
            existing = args[4] if arg_has else (kwargs.get("trace_headers") or {})
            # Inject parent context if none existed
            if not arg_has and not kw_has:
                headers = {}
                HTTPPropagator.inject(parent.context, headers)
                if len(args) > 4:
                    args = list(args)
                    args[4] = headers
                    args = tuple(args)
                    existing = args[4]
                else:
                    kwargs["trace_headers"] = headers
                    existing = kwargs["trace_headers"]
            # Merge captured prompt and model into mapping
            # request_id arg index 2
            req_id = args[2] if len(args) > 2 else kwargs.get("request_id")
            cache = getattr(vllm, "_dd_captured_prompts", None)
            if req_id and cache and str(req_id) in cache:
                existing.setdefault("x-datadog-captured-prompt", cache[str(req_id)])
            model_name = getattr(getattr(instance, "model_config", None), "model", None)
            if model_name:
                existing.setdefault("x-datadog-vllm-model", model_name)
            log.debug("[VLLM DD] MQ._process_request inject: arg_has=%s kw_has=%s keys=%s", arg_has, kw_has, list(existing.keys()))
    
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
    _safe_wrap("vllm.v1.engine.async_llm", "AsyncLLM.generate", traced_asyncllm_generate(vllm))
    _safe_wrap("vllm.v1.engine.async_llm", "AsyncLLM.encode", traced_asyncllm_encode(vllm))
    _safe_wrap("vllm.entrypoints.openai.speech_to_text", "OpenAISpeechToText._preprocess_speech_to_text", traced_openai_stt_preprocess(vllm))
    _safe_wrap("vllm.entrypoints.openai.serving_engine", "OpenAIServing._log_inputs", traced_openaiserving_log_inputs(vllm))
    _safe_wrap("vllm.engine.async_llm_engine", "_AsyncLLMEngine.add_request_async", traced_v0_engine_add_request_async(vllm))
    _safe_wrap("vllm.engine.llm_engine", "LLMEngine.add_request", traced_v0_engine_add_request(vllm))
    _safe_wrap("vllm.engine.multiprocessing.client", "MQLLMEngineClient._process_request", traced_mq_client_process_request(vllm))
    _safe_wrap("vllm.engine.llm_engine", "LLMEngine.do_tracing", traced_llmengine_do_tracing(vllm))
    log.debug("[VLLM DEBUG] patch: wrappers applied")


def unpatch():
    """Remove vLLM integration patches."""
    if not getattr(vllm, "_datadog_patch", False):
        return
    
    vllm._datadog_patch = False
    
    # Remove patches
    _safe_unwrap("vllm.v1.engine.async_llm", "AsyncLLM.generate")
    _safe_unwrap("vllm.v1.engine.async_llm", "AsyncLLM.encode")
    _safe_unwrap("vllm.entrypoints.openai.speech_to_text", "OpenAISpeechToText._preprocess_speech_to_text")
    _safe_unwrap("vllm.entrypoints.openai.serving_engine", "OpenAIServing._log_inputs")
    _safe_unwrap("vllm.engine.async_llm_engine", "_AsyncLLMEngine.add_request_async")
    _safe_unwrap("vllm.engine.llm_engine", "LLMEngine.add_request")
    _safe_unwrap("vllm.engine.multiprocessing.client", "MQLLMEngineClient._process_request")
    
    # Clear integration
    if hasattr(vllm, "_datadog_integration"):
        delattr(vllm, "_datadog_integration")
