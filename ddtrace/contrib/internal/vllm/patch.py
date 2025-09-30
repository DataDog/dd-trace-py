from __future__ import annotations

import time
from typing import Optional

import vllm
import vllm.envs as envs
from vllm.outputs import PoolingRequestOutput
from vllm.outputs import RequestOutput
from vllm.pooling_params import PoolingParams
from vllm.sequence import RequestMetrics
from vllm.sequence import SequenceGroup

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.llmobs._integrations.vllm import VLLMIntegration

from .data_extractors import RequestData
from .data_extractors import extract_embedding_shape_info
from .data_extractors import extract_lora_name
from .data_extractors import extract_model_name
from .data_extractors import extract_offline_data
from .data_extractors import extract_offline_pooling_data
from .data_extractors import extract_v0_data
from .data_extractors import extract_v1_streaming_data
from .data_extractors import select_prompt_for_span
from .span_utils import cache_headers_for_pooling_params
from .span_utils import create_vllm_span
from .span_utils import inject_trace_headers
from .span_utils import set_latency_metrics


config._add("vllm", {})


# ---------- integration / tracer plumbing -----------------------------------
def is_v1() -> bool:
    return envs.VLLM_USE_V1


def _tag_if_enabled(integration, span, data: RequestData, *, operation: Optional[str] = None) -> None:
    if integration and integration.llmobs_enabled:
        op = operation or "completion"
        kwargs_to_pass = {"request_data": data}
        integration.llmobs_set_tags(span, args=[], kwargs=kwargs_to_pass, response=None, operation=op)


# ---------- v0: ENGINE-SIDE tracing -----------------------------------------
@with_traced_module
def traced_llmengine_process_model_outputs(vllm, pin, func, instance, args, kwargs):
    """Trace LLMEngine._process_model_outputs calls (V0 engine)."""
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
    """Trace AsyncLLM.generate calls (V1 engine)."""
    prompt_arg = args[0] if args else kwargs.get("prompt")
    sampling_params = args[1] if len(args) > 1 else kwargs.get("sampling_params")
    lora_request = args[3] if len(args) > 3 else kwargs.get("lora_request")

    tracer = pin.tracer
    integration = vllm._datadog_integration

    span = create_vllm_span(
        tracer=tracer,
        integration=integration,
        model_name=extract_model_name(instance),
    )

    async def execute():
        res = func(*args, **kwargs)
        if hasattr(res, "__await__"):
            res = await res

        # Consume generator until finished to collect all outputs
        outputs: list[RequestOutput] = []
        async for out in res:
            outputs.append(out)
            if getattr(out, "finished", False):
                break

        # Extract data and tag span
        data = extract_v1_streaming_data(outputs)
        data.lora_name = extract_lora_name(lora_request)
        data.sampling_params = sampling_params
        if not data.prompt:
            tokenizer = (
                instance.tokenizer.get_lora_tokenizer(lora_request) if getattr(instance, "tokenizer", None) else None
            )
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

        # Yield buffered outputs
        for out in outputs:
            yield out

    return execute()


# ---------- OFFLINE tracing (V1 LLM.* methods) ------------------------------
@with_traced_module
def traced_llm_generate(vllm, pin, func, instance, args, kwargs):
    """Trace LLM.generate calls (V1 offline mode)."""
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
    """Trace LLM.encode calls (V1 offline mode)."""
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
    """Trace LLM._cross_encoding_score calls (V1 offline mode)."""
    tracer = pin.tracer
    integration = vllm._datadog_integration

    # tokenizer argument is not used here
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
    """Trace AsyncLLM.encode calls (V1 engine)."""
    tracer = pin.tracer
    integration = vllm._datadog_integration

    span = create_vllm_span(
        tracer=tracer,
        integration=integration,
        model_name=extract_model_name(instance),
    )

    async def execute():
        res = func(*args, **kwargs)
        if hasattr(res, "__await__"):
            res = await res

        # Consume generator until finished to collect all outputs
        outputs: list[PoolingRequestOutput] = []
        async for out in res:
            outputs.append(out)
            if getattr(out, "finished", False):
                break

        # Extract embedding data and tag span
        data = RequestData()
        if outputs:
            data.input_tokens = len(outputs[-1].prompt_token_ids)
            data.input_ = outputs[-1].prompt_token_ids
            num_emb, emb_dim = extract_embedding_shape_info(outputs[-1].outputs.data if outputs else None)
            data.embedding_dim = emb_dim
            data.num_embeddings = num_emb or 1
        _tag_if_enabled(integration, span, data, operation="embedding")
        span.finish()

        # Yield buffered outputs
        for out in outputs:
            yield out

    return execute()


# ---------- v0: TRACE HEADER INJECTION --------------------------------------
@with_traced_module
async def traced_v0_engine_add_request_async(vllm, pin, func, instance, args, kwargs):
    """Inject trace headers into V0 AsyncLLMEngine.add_request_async calls."""
    lora_request = args[3] if len(args) > 3 else kwargs.get("lora_request")
    tokenizer = await instance.get_tokenizer_async(lora_request)

    modified_args = inject_trace_headers(
        pin=pin,
        args=args,
        kwargs=kwargs,
        prompt_arg_pos=1,
        params_arg_pos=2,
        trace_headers_arg_pos=5,
        tokenizer=tokenizer,
    )
    if modified_args is not None:
        args = modified_args

    cache_headers_for_pooling_params(instance, args, kwargs)
    return await func(*args, **kwargs)


@with_traced_module
def traced_v0_engine_add_request(vllm, pin, func, instance, args, kwargs):
    """Inject trace headers into V0 LLMEngine.add_request calls."""
    lora_request = args[3] if len(args) > 3 else kwargs.get("lora_request")
    tokenizer = instance.get_tokenizer(lora_request)

    modified_args = inject_trace_headers(
        pin=pin,
        args=args,
        kwargs=kwargs,
        prompt_arg_pos=1,
        params_arg_pos=2,
        trace_headers_arg_pos=6,
        tokenizer=tokenizer,
    )
    if modified_args is not None:
        args = modified_args

    cache_headers_for_pooling_params(instance, args, kwargs)
    return func(*args, **kwargs)


@with_traced_module
async def traced_mq_client_process_request(vllm, pin, func, instance, args, kwargs):
    """Trace MQLLMEngineClient._process_request calls (V0 multiprocess mode)."""
    lora_request = args[3] if len(args) > 3 else kwargs.get("lora_request")
    tokenizer = await instance.get_tokenizer(lora_request)

    modified_args = inject_trace_headers(
        pin=pin,
        args=args,
        kwargs=kwargs,
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

    span = create_vllm_span(
        tracer=tracer,
        integration=integration,
        model_name=extract_model_name(instance),
    )

    res = func(*args, **kwargs)

    # Consume generator until finished to collect all outputs
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
            num_emb, emb_dim = extract_embedding_shape_info(getattr(outputs[-1].outputs, "data", None))
            data.embedding_dim = emb_dim
            data.num_embeddings = num_emb or 1
        _tag_if_enabled(integration, span, data, operation="embedding")
    else:
        data = extract_v1_streaming_data(outputs)
        data.lora_name = extract_lora_name(lora_request)
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

    # Yield buffered outputs
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

    if is_v1():
        # Async API (AsyncLLM)
        wrap("vllm.v1.engine.async_llm", "AsyncLLM.generate", traced_asyncllm_generate(vllm))
        wrap("vllm.v1.engine.async_llm", "AsyncLLM.encode", traced_asyncllm_encode(vllm))
        # Offline API (LLM)
        wrap("vllm.entrypoints.llm", "LLM.generate", traced_llm_generate(vllm))
        wrap("vllm.entrypoints.llm", "LLM.encode", traced_llm_encode(vllm))
        wrap("vllm.entrypoints.llm", "LLM._cross_encoding_score", traced_llm_cross_encoding_score(vllm))
    else:
        wrap(
            "vllm.engine.async_llm_engine",
            "_AsyncLLMEngine.add_request_async",
            traced_v0_engine_add_request_async(vllm),
        )
        wrap("vllm.engine.llm_engine", "LLMEngine.add_request", traced_v0_engine_add_request(vllm))
        wrap("vllm.engine.llm_engine", "LLMEngine._process_model_outputs", traced_llmengine_process_model_outputs(vllm))
        wrap(
            "vllm.engine.multiprocessing.client",
            "MQLLMEngineClient._process_request",
            traced_mq_client_process_request(vllm),
        )


def unpatch():
    """Remove Datadog tracing from vLLM library."""
    if not getattr(vllm, "_datadog_patch", False):
        return

    vllm._datadog_patch = False

    if is_v1():
        # Async API (AsyncLLM)
        unwrap(vllm.v1.engine.async_llm.AsyncLLM, "generate")
        unwrap(vllm.v1.engine.async_llm.AsyncLLM, "encode")
        # Offline API (LLM)
        unwrap(vllm.entrypoints.llm.LLM, "generate")
        unwrap(vllm.entrypoints.llm.LLM, "encode")
        unwrap(vllm.entrypoints.llm.LLM, "_cross_encoding_score")
    else:
        unwrap(vllm.engine.async_llm_engine._AsyncLLMEngine, "add_request_async")
        unwrap(vllm.engine.llm_engine.LLMEngine, "add_request")
        unwrap(vllm.engine.llm_engine.LLMEngine, "_process_model_outputs")
        unwrap(vllm.engine.multiprocessing.client.MQLLMEngineClient, "_process_request")

    delattr(vllm, "_datadog_integration")
