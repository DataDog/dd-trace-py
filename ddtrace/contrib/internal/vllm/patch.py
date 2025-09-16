from __future__ import annotations

from typing import Any, Optional
import time

import vllm
import vllm.envs as envs
from vllm.outputs import RequestOutput
from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap, wrap, with_traced_module
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.vllm import VLLMIntegration
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Pin
from vllm.sequence import RequestMetrics, SequenceGroup
from vllm.core.scheduler import SchedulerOutputs

from .data_extractors import (
    RequestData,
    extract_captured_prompt,
    extract_lora_name,
    extract_model_name,
    extract_offline_data,
    extract_v0_data,
    extract_v1_streaming_data,
    _embedding_dim,
)
from .span_utils import (
    cache_headers_for_pooling_params,
    create_vllm_span,
    inject_trace_headers,
    set_latency_metrics,
)

log = get_logger(__name__)
config._add("vllm", {})


# ---------- integration / tracer plumbing -----------------------------------
def _safe_wrap(mod: str, name: str, wrapper) -> bool:
    try:
        wrap(mod, name, wrapper)
        return True
    except Exception as e:
        log.debug("[VLLM DD] safe_wrap: skip %s.%s (%s)", mod, name, e)
        return False


def _safe_unwrap(mod: str, name: str) -> bool:
    try:
        unwrap(mod, name)
        return True
    except Exception as e:
        log.debug("[VLLM DD] safe_unwrap: skip %s.%s (%s)", mod, name, e)
        return False


def is_v1() -> bool:
    return envs.VLLM_USE_V1


def _tag_if_enabled(integration, span, data: RequestData, *, operation: Optional[str] = None) -> None:
    if integration and integration.llmobs_enabled:
        op = operation or "completion"
        kwargs_to_pass = {"request_data": data}
        integration.llmobs_set_tags(span, args=[], kwargs=kwargs_to_pass, response=None, operation=op)


# ---------- v0: ENGINE-SIDE tracing -----------------------------------------
@with_traced_module
def traced_llmengine_do_tracing(vllm, pin, func, instance, args, kwargs):
    res = func(*args, **kwargs)

    tracer = pin.tracer
    integration = vllm._datadog_integration

    scheduler_outputs: SchedulerOutputs = args[0] if args else kwargs.get("scheduler_outputs")
    finished_before = set(args[1] if len(args) > 1 else (kwargs.get("finished_before") or []))
    groups = scheduler_outputs.scheduled_seq_groups or []
    log.debug("[VLLM DD] do_tracing: groups=%d finished_before=%d", len(groups), len(finished_before))

    for idx, scheduled in enumerate(groups):
        if finished_before and idx in finished_before:
            continue

        seq_group: SequenceGroup = scheduled.seq_group
        if seq_group.trace_headers is None and hasattr(instance, "_dd_pending_trace_headers"):
            req_id = seq_group.request_id
            if req_id and str(req_id) in instance._dd_pending_trace_headers:
                seq_group.trace_headers = instance._dd_pending_trace_headers.pop(str(req_id))

        if not seq_group.is_finished():
            continue

        metrics_obj: RequestMetrics = seq_group.metrics

        span = create_vllm_span(
            tracer=tracer,
            integration=integration,
            model_name=extract_model_name(instance),
            seq_group=seq_group,
            arrival_time=metrics_obj.arrival_time,
        )

        data = extract_v0_data(seq_group)

        operation = "embedding" if data.embedding_dim is not None else "completion"
        if operation == "embedding":
            data.input_ = list(seq_group.prompt_token_ids)
            data.num_embeddings = 1
        
        _tag_if_enabled(integration, span, data, operation=operation)
        set_latency_metrics(span, metrics_obj)
        span.finish()

    return res


# ---------- v1: API-SERVER-SIDE tracing -------------------------------------
@with_traced_module
def traced_asyncllm_generate(vllm, pin, func, instance, args, kwargs):
    prompt_arg = args[0] if args else kwargs.get("prompt")
    sampling_params = args[1] if len(args) > 1 else kwargs.get("sampling_params")
    request_id = args[2] if len(args) > 2 else kwargs.get("request_id")
    lora_request = args[3] if len(args) > 3 else kwargs.get("lora_request")

    tracer = pin.tracer
    integration = vllm._datadog_integration

    parent = tracer.current_span()
    span = create_vllm_span(
        tracer=tracer,
        integration=integration,
        model_name=extract_model_name(instance),
    )

    outputs: list[RequestOutput] = []
    finalized = False

    def _finalize_span():
        nonlocal finalized
        if finalized:
            return
        data = extract_v1_streaming_data(outputs)
        data.lora_name = extract_lora_name(lora_request)
        data.sampling_params = sampling_params
        data.request_id = request_id
        data.prompt = data.prompt or extract_captured_prompt(parent) or prompt_arg
        _tag_if_enabled(integration, span, data)
        span.finish()
        finalized = True

    async def execute():
        res = func(*args, **kwargs)
        if hasattr(res, "__await__"):
            res = await res

        async for out in res:
            outputs.append(out)
            if out.finished:
                _finalize_span()
            yield out

        # Safety net in case we never observed a finished output
        _finalize_span()

    return execute()


@with_traced_module
def traced_asyncllm_encode(vllm, pin, func, instance, args, kwargs):
    tracer = pin.tracer
    integration = vllm._datadog_integration

    span = create_vllm_span(
        tracer=tracer,
        integration=integration,
        model_name=extract_model_name(instance),
    )

    outputs: list[RequestOutput] = []
    full_prompt_token_ids = None

    async def execute():
        nonlocal full_prompt_token_ids
        res = func(*args, **kwargs)
        if hasattr(res, "__await__"):
            res = await res

        async for out in res:
            outputs.append(out)
            if full_prompt_token_ids is None:
                ids = out.prompt_token_ids
                if ids:
                    full_prompt_token_ids = list(ids)
            yield out

        data = RequestData()
        for o in outputs:
            pt = o.prompt_token_ids
            if pt and not data.input_tokens:
                data.input_tokens = len(pt)
            dim = _embedding_dim(o.outputs.data) if o.outputs else None
            if dim is not None:
                data.embedding_dim = dim

        if full_prompt_token_ids:
            data.input_ = full_prompt_token_ids
            data.num_embeddings = 1
            if not data.input_tokens:
                data.input_tokens = len(full_prompt_token_ids)

        _tag_if_enabled(integration, span, data, operation="embedding")
        span.finish()

    return execute()


# ---------- offline LLM.generate --------------------------------------------
@with_traced_module
def traced_llm_generate(vllm, pin, func, instance, args, kwargs):
    if not is_v1():
        return func(*args, **kwargs)

    tracer = pin.tracer
    integration = vllm._datadog_integration

    prompts = args[0] if args else kwargs.get("prompts")
    sampling_params = args[1] if len(args) > 1 else kwargs.get("sampling_params")

    start_time = time.time()
    outputs = func(*args, **kwargs)

    for output in outputs or []:
        data = extract_offline_data(output, prompts)
        data.sampling_params = sampling_params

        span = create_vllm_span(
            tracer=tracer,
            integration=integration,
            model_name=extract_model_name(instance.llm_engine),
            arrival_time=start_time,
        )
        _tag_if_enabled(integration, span, data)
        span.finish()

    return outputs


# ---------- OpenAI entrypoints: prompt capture & STT ------------------------
@with_traced_module
def traced_openaiserving_log_inputs(vllm, pin, func, instance, args, kwargs):
    request_id = args[0] if len(args) > 0 else kwargs.get("request_id")
    inputs = args[1] if len(args) > 1 else kwargs.get("inputs")

    captured = None
    if isinstance(inputs, str):
        captured = inputs
    elif isinstance(inputs, list) and inputs and isinstance(inputs[0], int):
        captured = inputs
    elif isinstance(inputs, dict):
        captured = inputs.get("prompt") or inputs.get("prompt_token_ids")

    if captured and pin and pin.tracer:
        parent = pin.tracer.current_span()
        if parent:
            parent._set_ctx_item("vllm.captured_prompt", captured)

    if request_id and captured:
        integration = vllm._datadog_integration
        integration.capture_prompt(str(request_id), str(captured))

    return func(*args, **kwargs)


@with_traced_module
async def traced_openai_stt_preprocess(vllm, pin, func, instance, args, kwargs):
    prompts, other = await func(*args, **kwargs)

    decoder_prompt = None
    if isinstance(prompts, list):
        for p in prompts:
            if isinstance(p, dict) and isinstance(p.get("decoder_prompt"), str):
                decoder_prompt = p["decoder_prompt"]
                break
            if isinstance(p, str):
                decoder_prompt = p
                break

    if decoder_prompt and pin and pin.tracer:
        parent = pin.tracer.current_span()
        if parent:
            parent._set_ctx_item("vllm.captured_prompt", decoder_prompt)

    return prompts, other


# ---------- v0 request injection (single/multiprocess) ----------------------
@with_traced_module
async def traced_v0_engine_add_request_async(vllm, pin, func, instance, args, kwargs):
    # trace headers injection is not supported for V1 engines
    if is_v1():
        return await func(*args, **kwargs)

    modified_args = inject_trace_headers(
        pin=pin,
        integration=vllm._datadog_integration,
        args=args,
        kwargs=kwargs,
        request_id_arg_pos=0,
        prompt_arg_pos=1,
        trace_headers_arg_pos=5,
    )
    if modified_args is not None:
        args = modified_args

    cache_headers_for_pooling_params(instance, args, kwargs)

    log.debug(
        "[VLLM DD] add_request_async(v0): request_id=%s",
        kwargs.get("request_id"),
    )
    return await func(*args, **kwargs)


@with_traced_module
def traced_v0_engine_add_request(vllm, pin, func, instance, args, kwargs):
    # trace headers injection is not supported for V1 engines
    if is_v1():
        return func(*args, **kwargs)

    modified_args = inject_trace_headers(
        pin=pin,
        integration=vllm._datadog_integration,
        args=args,
        kwargs=kwargs,
        request_id_arg_pos=0,
        prompt_arg_pos=1,
        trace_headers_arg_pos=6,
    )
    if modified_args is not None:
        args = modified_args

    cache_headers_for_pooling_params(instance, args, kwargs)

    log.debug(
        "[VLLM DD] add_request(sync)(v0): request_id=%s",
        kwargs.get("request_id"),
    )
    return func(*args, **kwargs)


@with_traced_module
async def traced_mq_client_process_request(vllm, pin, func, instance, args, kwargs):
    modified_args = inject_trace_headers(
        pin=pin,
        integration=vllm._datadog_integration,
        args=args,
        kwargs=kwargs,
        request_id_arg_pos=2,
        trace_headers_arg_pos=4,
    )
    if modified_args is not None:
        args = modified_args

    hdrs = args[4] if len(args) > 4 else kwargs.get("trace_headers")
    log.debug("[VLLM DD] MQ._process_request inject: keys=%s", list(hdrs.keys()) if hdrs else [])

    res = func(*args, **kwargs)
    async for out in res:
        yield out


# ---------- patch/unpatch ----------------------------------------------------
def patch():
    if getattr(vllm, "_datadog_patch", False):
        return

    vllm._datadog_patch = True
    Pin().onto(vllm)

    integration = VLLMIntegration(integration_config=config.vllm)
    vllm._datadog_integration = integration

    log.debug("[VLLM DEBUG] patch: applying wrappers")
    _safe_wrap("vllm.v1.engine.async_llm", "AsyncLLM.generate", traced_asyncllm_generate(vllm))
    _safe_wrap("vllm.v1.engine.async_llm", "AsyncLLM.encode", traced_asyncllm_encode(vllm))
    _safe_wrap("vllm.entrypoints.openai.speech_to_text", "OpenAISpeechToText._preprocess_speech_to_text", traced_openai_stt_preprocess(vllm))
    _safe_wrap("vllm.entrypoints.openai.serving_engine", "OpenAIServing._log_inputs", traced_openaiserving_log_inputs(vllm))
    _safe_wrap("vllm.entrypoints.llm", "LLM.generate", traced_llm_generate(vllm))
    _safe_wrap("vllm.engine.async_llm_engine", "_AsyncLLMEngine.add_request_async", traced_v0_engine_add_request_async(vllm))
    _safe_wrap("vllm.engine.llm_engine", "LLMEngine.add_request", traced_v0_engine_add_request(vllm))
    _safe_wrap("vllm.engine.multiprocessing.client", "MQLLMEngineClient._process_request", traced_mq_client_process_request(vllm))
    _safe_wrap("vllm.engine.llm_engine", "LLMEngine.do_tracing", traced_llmengine_do_tracing(vllm))
    log.debug("[VLLM DEBUG] patch: wrappers applied")


def unpatch():
    if not getattr(vllm, "_datadog_patch", False):
        return

    vllm._datadog_patch = False

    _safe_unwrap("vllm.v1.engine.async_llm", "AsyncLLM.generate")
    _safe_unwrap("vllm.v1.engine.async_llm", "AsyncLLM.encode")
    _safe_unwrap("vllm.entrypoints.openai.speech_to_text", "OpenAISpeechToText._preprocess_speech_to_text")
    _safe_unwrap("vllm.entrypoints.openai.serving_engine", "OpenAIServing._log_inputs")
    _safe_unwrap("vllm.entrypoints.llm", "LLM.generate")
    _safe_unwrap("vllm.engine.async_llm_engine", "_AsyncLLMEngine.add_request_async")
    _safe_unwrap("vllm.engine.llm_engine", "LLMEngine.add_request")
    _safe_unwrap("vllm.engine.multiprocessing.client", "MQLLMEngineClient._process_request")
    _safe_unwrap("vllm.engine.llm_engine", "LLMEngine.do_tracing")

    delattr(vllm, "_datadog_integration")