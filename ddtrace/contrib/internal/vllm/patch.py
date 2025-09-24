from __future__ import annotations

from typing import Any, Optional
import time

import vllm
import vllm.envs as envs
from vllm.outputs import RequestOutput, PoolingRequestOutput
from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap, wrap, with_traced_module
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.vllm import VLLMIntegration
from ddtrace.trace import Pin
from vllm.sequence import RequestMetrics, SequenceGroup
from vllm.core.scheduler import SchedulerOutputs
from vllm.pooling_params import PoolingParams
from vllm.sampling_params import SamplingParams
from vllm.entrypoints.score_utils import get_score_prompt

from .data_extractors import (
    RequestData,
    extract_lora_name,
    extract_model_name,
    extract_offline_data,
    extract_offline_pooling_data,
    extract_v0_data,
    extract_v1_streaming_data,
    _embedding_dim,
    _embedding_shape_info,
    select_prompt_for_span,
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


def _safe_unwrap(mod, name: str) -> bool:
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
"""
@with_traced_module
def traced_llmengine_do_tracing(vllm, pin, func, instance, args, kwargs):
    log.debug("[VLLM DD] traced_llmengine_do_tracing: args=%s kwargs=%s", args, kwargs)
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
        # For decoder-only/completion requests with token-only prompts, decode to text
        #if operation == "completion" and not data.prompt:
        #    tokenizer = instance.get_tokenizer(seq_group.lora_request)
        #    if seq_group.prompt_token_ids:
        #        data.prompt = tokenizer.decode(seq_group.prompt_token_ids)
        if operation == "embedding":
            data.input_ = seq_group.prompt_token_ids
        
        _tag_if_enabled(integration, span, data, operation=operation)
        set_latency_metrics(span, metrics_obj)
        span.finish()

    return res
"""    


@with_traced_module
def traced_llmengine_process_model_outputs(vllm, pin, func, instance, args, kwargs):
    log.debug("[VLLM DD] traced_llmengine_process_model_outputs: args=%s kwargs=%s", args, kwargs)
    res = func(*args, **kwargs)

    tracer = pin.tracer
    integration = vllm._datadog_integration

    # ctx holds scheduler_outputs and request_outputs for this iteration
    ctx = args[0] if args else kwargs.get("ctx")
    scheduler_outputs = getattr(ctx, "scheduler_outputs", None)
    if not scheduler_outputs:
        return res

    # Ensure we only trace each finished request once
    traced_ids = getattr(instance, "_dd_traced_request_ids", None)
    if traced_ids is None:
        traced_ids = set()
        setattr(instance, "_dd_traced_request_ids", traced_ids)

    for scheduled in scheduler_outputs.scheduled_seq_groups or []:
        seq_group: SequenceGroup = scheduled.seq_group
        if not seq_group.is_finished():
            continue
        req_id = getattr(seq_group, "request_id", None)
        if req_id in traced_ids:
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
            data.input_ = seq_group.prompt_token_ids

        _tag_if_enabled(integration, span, data, operation=operation)
        set_latency_metrics(span, metrics_obj)
        span.finish()

        traced_ids.add(req_id)

    return res

# ---------- v1: API-SERVER-SIDE tracing -------------------------------------
@with_traced_module
def traced_asyncllm_generate(vllm, pin, func, instance, args, kwargs):
    prompt_arg = args[0] if args else kwargs.get("prompt")
    sampling_params = args[1] if len(args) > 1 else kwargs.get("sampling_params")
    request_id = args[2] if len(args) > 2 else kwargs.get("request_id")
    lora_request = args[3] if len(args) > 3 else kwargs.get("lora_request")
    log.debug("[VLLM DD] traced_asyncllm_generate: args=%s kwargs=%s", args, kwargs)

    tracer = pin.tracer
    integration = vllm._datadog_integration

    span = create_vllm_span(
        tracer=tracer,
        integration=integration,
        model_name=extract_model_name(instance),
    )
    log.debug("[VLLM DD] traced_asyncllm_generate: span=%s", span)

    async def execute():
        log.debug("[VLLM DD] traced_asyncllm_generate: executing (drain-until-finished)")
        res = func(*args, **kwargs)
        if hasattr(res, "__await__"):
            res = await res

        outputs: list[RequestOutput] = []
        async for out in res:
            outputs.append(out)
            if getattr(out, "finished", False):
                break

        data = extract_v1_streaming_data(outputs)
        data.lora_name = extract_lora_name(lora_request)
        data.sampling_params = sampling_params
        data.request_id = request_id
        if not data.prompt:
            tokenizer = instance.tokenizer.get_lora_tokenizer(lora_request) if getattr(instance, "tokenizer", None) else None
            text, token_ids, input_tokens = select_prompt_for_span(
                prompt_arg,
                is_embedding=False,
                tokenizer=tokenizer,
            )
            data.prompt = text
            data.input_tokens = input_tokens
            data.input_ = token_ids
        _tag_if_enabled(integration, span, data)
        span.finish()

        for out in outputs:
            yield out

    return execute()

"""Offline tracing wrappers for vLLM LLM entrypoints (V1 only)."""


@with_traced_module
def traced_llm_generate(vllm, pin, func, instance, args, kwargs):
    if not is_v1():
        return func(*args, **kwargs)

    tracer = pin.tracer
    integration = vllm._datadog_integration

    prompt_arg = args[0] if args else kwargs.get("prompts")
    sampling_params = args[1] if len(args) > 1 else kwargs.get("sampling_params")
    lora_request = args[3] if len(args) > 3 else kwargs.get("lora_request")

    start_time = time.time()
    outputs = func(*args, **kwargs)

    if not isinstance(prompt_arg, list):
        prompt_arg = [prompt_arg]
        
    prompts = []
    for prompt in prompt_arg:
        tokenizer = instance.get_tokenizer(lora_request)
        text, _, _ = select_prompt_for_span(
            prompt,
            is_embedding=False,
            tokenizer=tokenizer,
        )
        prompts.append(text)

    for output, prompt in zip(outputs or [], prompts):
        data = extract_offline_data(output, prompt)
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


@with_traced_module
def traced_llm_encode(vllm, pin, func, instance, args, kwargs):
    if not is_v1():
        return func(*args, **kwargs)

    tracer = pin.tracer
    integration = vllm._datadog_integration

    prompts = args[0] if args else kwargs.get("prompts")

    start_time = time.time()
    outputs = func(*args, **kwargs)

    for output in outputs or []:
        data = extract_offline_pooling_data(output, prompts)

        span = create_vllm_span(
            tracer=tracer,
            integration=integration,
            model_name=extract_model_name(instance.llm_engine),
            arrival_time=start_time,
        )
        _tag_if_enabled(integration, span, data, operation="embedding")
        span.finish()

    return outputs


@with_traced_module
def traced_llm_cross_encoding_score(vllm, pin, func, instance, args, kwargs):
    if not is_v1():
        return func(*args, **kwargs)

    tracer = pin.tracer
    integration = vllm._datadog_integration

    tokenizer = args[0] if args else kwargs.get("tokenizer")
    data_1 = args[1] if len(args) > 1 else kwargs.get("data_1")
    data_2 = args[2] if len(args) > 2 else kwargs.get("data_2")

    start_time = time.time()
    outputs = func(*args, **kwargs)

    # Align pairs for 1->N
    if len(data_1) == 1:
        data_1 = data_1 * len(data_2)

    for q, d, out in zip(data_1, data_2, outputs or []):
        data = RequestData()
        token_ids = out.prompt_token_ids
        if token_ids:
            data.input_ = token_ids
            data.input_tokens = len(token_ids)
        data.num_embeddings = 1
        data.embedding_dim = 1

        span = create_vllm_span(
            tracer=tracer,
            integration=integration,
            model_name=extract_model_name(instance.llm_engine),
            arrival_time=start_time,
        )
        _tag_if_enabled(integration, span, data, operation="embedding")
        span.finish()

    return outputs



@with_traced_module
def traced_asyncllm_encode(vllm, pin, func, instance, args, kwargs):
    tracer = pin.tracer
    integration = vllm._datadog_integration
    log.debug("[VLLM DD] traced_asyncllm_encode: args=%s kwargs=%s", args, kwargs)

    span = create_vllm_span(
        tracer=tracer,
        integration=integration,
        model_name=extract_model_name(instance),
    )
    async def execute():
        res = func(*args, **kwargs)
        if hasattr(res, "__await__"):
            res = await res

        outputs: list[PoolingRequestOutput] = []
        async for out in res:
            outputs.append(out)
            if getattr(out, "finished", False):
                break

        data = RequestData()
        if outputs:
            data.input_tokens = len(outputs[-1].prompt_token_ids)
            data.input_ = outputs[-1].prompt_token_ids
            num_emb, emb_dim = _embedding_shape_info(outputs[-1].outputs.data if outputs else None)
            data.embedding_dim = emb_dim
            data.num_embeddings = num_emb or 1
        _tag_if_enabled(integration, span, data, operation="embedding")
        span.finish()

        for out in outputs:
            yield out

    return execute()


# ---------- v0 request injection (single/multiprocess) ----------------------
@with_traced_module
async def traced_v0_engine_add_request_async(vllm, pin, func, instance, args, kwargs):
    # trace headers injection is not supported for V1 engines
    if is_v1():
        return await func(*args, **kwargs)

    lora_request = args[3] if len(args) > 3 else kwargs.get("lora_request")
    tokenizer = await instance.get_tokenizer_async(lora_request)

    modified_args = inject_trace_headers(
        pin=pin,
        integration=vllm._datadog_integration,
        args=args,
        kwargs=kwargs,
        request_id_arg_pos=0,
        prompt_arg_pos=1,
        params_arg_pos=2,
        trace_headers_arg_pos=5,
        tokenizer=tokenizer,
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

    lora_request = args[3] if len(args) > 3 else kwargs.get("lora_request")
    tokenizer = instance.get_tokenizer(lora_request)

    modified_args = inject_trace_headers(
        pin=pin,
        integration=vllm._datadog_integration,
        args=args,
        kwargs=kwargs,
        request_id_arg_pos=0,
        prompt_arg_pos=1,
        params_arg_pos=2,
        trace_headers_arg_pos=6,
        tokenizer=tokenizer,
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
    log.debug("[VLLM DD] traced_mq_client_process_request: args=%s kwargs=%s", args, kwargs)
    lora_request = args[3] if len(args) > 3 else kwargs.get("lora_request")
    tokenizer = await instance.get_tokenizer(lora_request)

    modified_args = inject_trace_headers(
        pin=pin,
        integration=vllm._datadog_integration,
        args=args,
        kwargs=kwargs,
        request_id_arg_pos=2,
        prompt_arg_pos=0,
        params_arg_pos=1,
        trace_headers_arg_pos=4,
        tokenizer=tokenizer,
    )
    if modified_args is not None:
        args = modified_args

    tracer = pin.tracer
    integration = vllm._datadog_integration

    prompt_arg = args[0] if args else kwargs.get("prompt")
    params = args[1] if len(args) > 1 else kwargs.get("params")
    request_id = args[2] if len(args) > 2 else kwargs.get("request_id")

    span = create_vllm_span(
        tracer=tracer,
        integration=integration,
        model_name=extract_model_name(instance),
    )

    res = func(*args, **kwargs)

    # Drain until finished, tag and finish span, then yield buffered outputs
    outputs: list[RequestOutput] = []
    async for out in res:
        outputs.append(out)
        if getattr(out, "finished", False):
            break

    if isinstance(params, PoolingParams):
        data = RequestData()
        if outputs:
            data.input_tokens = len(outputs[-1].prompt_token_ids)
            data.input_ = outputs[-1].prompt_token_ids
            num_emb, emb_dim = _embedding_shape_info(getattr(outputs[-1].outputs, "data", None))
            data.embedding_dim = emb_dim
            data.num_embeddings = num_emb or 1
        _tag_if_enabled(integration, span, data, operation="embedding")
    else:
        data = extract_v1_streaming_data(outputs)
        data.lora_name = extract_lora_name(lora_request)
        data.request_id = request_id
        if not data.prompt:
            text, token_ids, input_tokens = select_prompt_for_span(
                prompt_arg,
                is_embedding=False,
                tokenizer=tokenizer,
            )
            data.prompt = text
            data.input_tokens = input_tokens
            data.input_ = token_ids
        _tag_if_enabled(integration, span, data)

    span.finish()

    for out in outputs:
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
    _safe_wrap("vllm.entrypoints.llm", "LLM.generate", traced_llm_generate(vllm))
    _safe_wrap("vllm.entrypoints.llm", "LLM.encode", traced_llm_encode(vllm))
    _safe_wrap("vllm.entrypoints.llm", "LLM._cross_encoding_score", traced_llm_cross_encoding_score(vllm))

    _safe_wrap("vllm.engine.async_llm_engine", "_AsyncLLMEngine.add_request_async", traced_v0_engine_add_request_async(vllm))
    _safe_wrap("vllm.engine.llm_engine", "LLMEngine.add_request", traced_v0_engine_add_request(vllm))
    _safe_wrap("vllm.engine.multiprocessing.client", "MQLLMEngineClient._process_request", traced_mq_client_process_request(vllm))
    _safe_wrap("vllm.engine.llm_engine", "LLMEngine._process_model_outputs", traced_llmengine_process_model_outputs(vllm))
    log.debug("[VLLM DEBUG] patch: wrappers applied")


def unpatch():
    if not getattr(vllm, "_datadog_patch", False):
        return

    vllm._datadog_patch = False

    _safe_unwrap(vllm.v1.engine.async_llm.AsyncLLM, "generate")
    _safe_unwrap(vllm.v1.engine.async_llm.AsyncLLM, "encode")
    _safe_unwrap(vllm.entrypoints.llm.LLM, "generate")
    _safe_unwrap(vllm.entrypoints.llm.LLM, "encode")
    _safe_unwrap(vllm.entrypoints.llm.LLM, "_cross_encoding_score")
    
    _safe_unwrap(vllm.engine.async_llm_engine._AsyncLLMEngine, "add_request_async")
    _safe_unwrap(vllm.engine.llm_engine.LLMEngine, "add_request")
    _safe_unwrap(vllm.engine.multiprocessing.client.MQLLMEngineClient, "_process_request")
    _safe_unwrap(vllm.engine.llm_engine.LLMEngine, "_process_model_outputs")

    delattr(vllm, "_datadog_integration")