from typing import Any, Optional
import time

from ddtrace.ext import SpanTypes
from ddtrace.llmobs._constants import (
    INTEGRATION,
)
from ddtrace.constants import _SPAN_MEASURED_KEY
import vllm
from vllm.outputs import PoolingRequestOutput, RequestOutput
from vllm.transformers_utils.tokenizer import decode_tokens

from ddtrace import config
from ddtrace import tracer as dd_tracer
from ddtrace.contrib.trace_utils import wrap, unwrap, with_traced_module, iswrapped
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.vllm import VLLMIntegration
from ddtrace.trace import Pin


log = get_logger(__name__)


# Integration configuration
config._add("vllm", {})


 


 


def _get_pin_tracer():
    pin = Pin.get_from(vllm)
    if pin and pin.tracer:
        return pin.tracer
    # Fallback to global tracer when Pin has no tracer attached
    try:
        return dd_tracer
    except Exception:
        return None


@with_traced_module
def traced_v0_requestoutputfactory_create(vllm, pin, func, instance, args, kwargs):
    """V0: Create a vllm.request span when a finished RequestOutput is created."""
    integration: VLLMIntegration = getattr(vllm, "_datadog_integration", None)
    try:
        log.debug("[VLLM DEBUG] V0.create: entered; args_len=%s kwargs_keys=%s", len(args), list(kwargs.keys()))
    except Exception:
        pass
    res = func(*args, **kwargs)
    if res is None or not getattr(res, "finished", False):
        try:
            log.debug("[VLLM DEBUG] V0.create: no span; res is None (%s) or finished=%s", res is None, getattr(res, "finished", None))
        except Exception:
            pass
        return res

    tracer = _get_pin_tracer()
    if tracer is None:
        try:
            log.debug("[VLLM DEBUG] V0.create: tracer unavailable; skipping span")
        except Exception:
            pass
        return res

    seq_group = args[0] if args else kwargs.get("seq_group")
    parent = tracer.current_span()
    under_batch = bool(parent and parent._get_ctx_item("vllm.embedding_batch"))
    # If we are under an embedding batch aggregator, suppress inner v0 spans and aggregate instead
    if under_batch:
        try:
            log.debug("[VLLM DEBUG] V0.create: under embedding batch aggregator; aggregating and suppressing span")
        except Exception:
            pass
        try:
            agg = parent._get_ctx_item("vllm.embedding_agg") if parent else None
            if isinstance(agg, dict):
                if isinstance(res, PoolingRequestOutput):
                    pts = getattr(res, "prompt_token_ids", None) or []
                    if pts:
                        agg.setdefault("merged_prompt_token_ids", [])
                        agg["merged_prompt_token_ids"].extend(list(pts))
                        agg["input_tokens"] = int(agg.get("input_tokens", 0)) + len(pts)
                    try:
                        data = getattr(getattr(res, "outputs", None), "data", None)
                        if data is not None and hasattr(data, "shape") and len(data.shape) >= 1:
                            if agg.get("embedding_dim") is None:
                                agg["embedding_dim"] = int(data.shape[-1])
                    except Exception:
                        pass
            # Do not create a span here
            return res
        except Exception:
            # If aggregation fails, fall through to normal span creation
            pass

    span = tracer.start_span(
        "vllm.request",
        child_of=parent.context if parent else None,
        resource="vllm.request",
        span_type=SpanTypes.LLM if (integration and integration.llmobs_enabled) else None,
        activate=False,
    )
    if integration and integration.llmobs_enabled:
        span._set_ctx_item(INTEGRATION, integration._integration_name)
    span.set_metric(_SPAN_MEASURED_KEY, 1)
    try:
        log.debug("[VLLM DEBUG] V0.create: span started; req_id=%s", getattr(res, "request_id", None))
    except Exception:
        pass
    try:
        # Prepare fields
        op = "embedding" if isinstance(res, PoolingRequestOutput) else "completion"
        request_id = getattr(res, "request_id", None)
        prompt = getattr(res, "prompt", None)
        prompt_token_ids = getattr(res, "prompt_token_ids", None) or []
        input_tokens = len(prompt_token_ids)
        output_tokens = 0
        output_text = ""
        finish_reason = None
        stop_reason = None
        sampling_params = getattr(seq_group, "sampling_params", None)
        lora_req = getattr(seq_group, "lora_request", None)
        lora_name = getattr(lora_req, "name", None) if lora_req is not None else None
        embedding_dim = None
        nct = getattr(res, "num_cached_tokens", None)

        if op == "completion":
            for comp in getattr(res, "outputs", None) or []:
                txt = getattr(comp, "text", None)
                if txt:
                    output_text += txt
                tids = getattr(comp, "token_ids", None)
                if tids is not None:
                    output_tokens += (len(tids) if not isinstance(tids, int) else 1)
                fr = getattr(comp, "finish_reason", None)
                if fr is not None:
                    finish_reason = fr
                sr = getattr(comp, "stop_reason", None)
                if sr is not None:
                    stop_reason = sr
        else:
            try:
                data = getattr(getattr(res, "outputs", None), "data", None)
                if data is not None and hasattr(data, "shape") and len(data.shape) >= 1:
                    embedding_dim = int(data.shape[-1])
            except Exception:
                embedding_dim = None

        if integration and integration.llmobs_enabled:
            integration.llmobs_set_tags(
                span=span,
                args=[],
                kwargs={
                    "model_name": None,  # not available here without engine handle
                    "prompt": prompt,
                    "output_text": output_text,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "sampling_params": sampling_params,
                    "request_id": request_id,
                    "finish_reason": finish_reason,
                    "stop_reason": stop_reason,
                    "operation": op,
                    "lora_name": lora_name,
                    "embedding_dim": embedding_dim,
                    "num_cached_tokens": nct,
                },
                response=None,
                operation=op,
            )
        # Engine-side latency metrics (v0): derive from seq_group.metrics
        try:
            metrics = getattr(seq_group, "metrics", None)
            if metrics is not None:
                arrival = getattr(metrics, "arrival_time", None)
                first_token_time = getattr(metrics, "first_token_time", None)
                finished_time = getattr(metrics, "finished_time", None)
                time_in_queue = getattr(metrics, "time_in_queue", None)
                if arrival is not None and first_token_time is not None:
                    span.set_metric("vllm.latency.ttft", float(first_token_time - arrival))
                if arrival is not None and finished_time is not None:
                    span.set_metric("vllm.latency.e2e", float(finished_time - arrival))
                if time_in_queue is not None:
                    span.set_metric("vllm.latency.queue", float(time_in_queue))
        except Exception:
            pass
    finally:
        try:
            log.debug(
                "[VLLM DEBUG] V0.create: span finishing; op=%s input_tokens=%s output_tokens=%s nct=%s",
                op, input_tokens, output_tokens, nct,
            )
        except Exception:
            pass
        span.finish()
    return res


@with_traced_module
def traced_asyncllm_generate(vllm, pin, func, instance, args, kwargs):
    """V1: Wrap AsyncLLM.generate to emit a single span per request."""
    prompt_arg = args[0] if args else kwargs.get("prompt")
    sampling_params = args[1] if len(args) > 1 else kwargs.get("sampling_params")
    request_id = args[2] if len(args) > 2 else kwargs.get("request_id")
    lora_request = args[3] if len(args) > 3 else kwargs.get("lora_request")

    integration: VLLMIntegration = getattr(vllm, "_datadog_integration", None)
    tracer = _get_pin_tracer()
    parent = tracer.current_span() if tracer else None
    under_batch = bool(parent and parent._get_ctx_item("vllm.embedding_batch"))
    try:
        log.debug(
            "[VLLM DEBUG] V1.generate: entered; tracer=%s under_batch=%s has_parent=%s",
            bool(tracer), under_batch, bool(parent),
        )
    except Exception:
        pass
    span = None if under_batch else (
        tracer.start_span(
            "vllm.request",
            child_of=parent.context if parent else None,
            resource="vllm.request",
            span_type=SpanTypes.LLM if (integration and integration.llmobs_enabled) else None,
            activate=False,
        ) if tracer else None
    )
    if tracer is None:
        try:
            log.debug("[VLLM DEBUG] V1.generate: tracer unavailable; span suppressed")
        except Exception:
            pass
    if under_batch:
        try:
            log.debug("[VLLM DEBUG] V1.generate: under embedding batch aggregator; span suppressed")
        except Exception:
            pass
    if span is not None:
        if integration and integration.llmobs_enabled:
            span._set_ctx_item(INTEGRATION, integration._integration_name)
        span.set_metric(_SPAN_MEASURED_KEY, 1)
        try:
            log.debug("[VLLM DEBUG] V1.generate: span started; request_id=%s", request_id)
        except Exception:
            pass

    stored_prompt: Optional[Any] = None
    input_tokens: int = 0
    output_tokens: int = 0
    output_text: str = ""
    finish_reason: Optional[str] = None
    stop_reason: Optional[Any] = None
    num_cached_tokens: Optional[int] = None
    t0 = time.time()
    ttft_sec: Optional[float] = None
    span_closed: bool = False

    async def _iter():
        nonlocal stored_prompt, input_tokens, output_tokens, output_text
        nonlocal finish_reason, stop_reason, num_cached_tokens, ttft_sec
        nonlocal span, span_closed
        try:
            try:
                log.debug("[VLLM DEBUG] V1.generate: calling inner func")
            except Exception:
                pass
            res = func(*args, **kwargs)
            if hasattr(res, "__await__"):
                res = await res
            async for out in res:
                try:
                    log.debug("[VLLM DEBUG] V1.generate: received chunk; finished=%s", getattr(out, "finished", None))
                except Exception:
                    pass
                if stored_prompt is None:
                    stored_prompt = getattr(out, "prompt", None)
                if not input_tokens:
                    pts = getattr(out, "prompt_token_ids", None)
                    if pts is not None:
                        input_tokens = len(pts)
                if num_cached_tokens is None:
                    nct = getattr(out, "num_cached_tokens", None)
                    if nct is not None:
                        num_cached_tokens = nct
                for comp in getattr(out, "outputs", None) or []:
                    txt = getattr(comp, "text", None)
                    if txt:
                        output_text += txt
                    tids = getattr(comp, "token_ids", None)
                    if tids is not None:
                        output_tokens += (len(tids) if not isinstance(tids, int) else 1)
                        if ttft_sec is None and (isinstance(tids, int) or (isinstance(tids, list) and len(tids) > 0)):
                            ttft_sec = time.time() - t0
                            try:
                                log.debug("[VLLM DEBUG] V1.generate: ttft computed=%.6f", ttft_sec)
                            except Exception:
                                pass
                    fr = getattr(comp, "finish_reason", None)
                    if fr is not None:
                        finish_reason = fr
                    sr = getattr(comp, "stop_reason", None)
                    if sr is not None:
                        stop_reason = sr
                # If this is the final chunk, eagerly finish span BEFORE yielding
                # so the caller can break immediately without losing the span.
                if not span_closed and getattr(out, "finished", False) and span is not None:
                    try:
                        model_name = getattr(getattr(instance, "model_config", None), "model", None)
                        lora_name = getattr(lora_request, "lora_name", None) or getattr(lora_request, "name", None)
                        if integration:
                            try:
                                integration._set_base_span_tags(span, model_name=model_name)
                            except Exception:
                                pass
                        parent_prompt = None
                        if parent is not None:
                            parent_prompt = parent._get_ctx_item("vllm.captured_prompt")
                        if integration and integration.llmobs_enabled:
                            integration.llmobs_set_tags(
                                span=span,
                                args=[],
                                kwargs={
                                    "model_name": model_name,
                                    "prompt": stored_prompt if isinstance(stored_prompt, str) else (parent_prompt if parent_prompt is not None else prompt_arg),
                                    "output_text": output_text,
                                    "input_tokens": input_tokens,
                                    "output_tokens": output_tokens,
                                    "sampling_params": sampling_params,
                                    "request_id": request_id,
                                    "finish_reason": finish_reason,
                                    "stop_reason": stop_reason,
                                    "num_cached_tokens": num_cached_tokens,
                                    "operation": "completion",
                                    "lora_name": lora_name,
                                },
                                response=None,
                                operation="completion",
                            )
                        # Latency metrics
                        try:
                            if ttft_sec is not None:
                                span.set_metric("vllm.latency.ttft", float(ttft_sec))
                            span.set_metric("vllm.latency.e2e", float(time.time() - t0))
                            log.debug(
                                "[VLLM DEBUG] V1.generate: eager finish (pre-yield); input_tokens=%s output_tokens=%s ttft=%s",
                                input_tokens, output_tokens, ttft_sec,
                            )
                        except Exception:
                            pass
                    finally:
                        span.finish()
                        span_closed = True
                        span = None
                yield out
        except Exception as e:
            if span is not None:
                try:
                    span.set_exc_info(type(e), e, e.__traceback__)
                    log.debug("[VLLM DEBUG] V1.generate: exception set on span: %s", type(e).__name__)
                except Exception:
                    pass
            raise
        finally:
            if span is not None and not span_closed:
                try:
                    model_name = getattr(getattr(instance, "model_config", None), "model", None)
                    lora_name = getattr(lora_request, "lora_name", None) or getattr(lora_request, "name", None)
                    if integration:
                        try:
                            integration._set_base_span_tags(span, model_name=model_name)
                        except Exception:
                            pass
                    # Prefer a clean stored_prompt. If not available, fallback to prompt captured at OpenAI serving layer
                    try:
                        parent_prompt = None
                        if stored_prompt is None or not isinstance(stored_prompt, str):
                            parent_prompt = parent._get_ctx_item("vllm.captured_prompt") if parent else None
                    except Exception:
                        parent_prompt = None
                    if integration and integration.llmobs_enabled:
                        integration.llmobs_set_tags(
                            span=span,
                            args=[],
                            kwargs={
                                "model_name": model_name,
                                "prompt": stored_prompt if isinstance(stored_prompt, str) else (parent_prompt if parent_prompt is not None else prompt_arg),
                                "output_text": output_text,
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "sampling_params": sampling_params,
                                "request_id": request_id,
                                "finish_reason": finish_reason,
                                "stop_reason": stop_reason,
                                "num_cached_tokens": num_cached_tokens,
                                "operation": "completion",
                                "lora_name": lora_name,
                            },
                            response=None,
                            operation="completion",
                        )
                    # API-server-side latency metrics (v1): local wall-clock
                    try:
                        if ttft_sec is not None:
                            span.set_metric("vllm.latency.ttft", float(ttft_sec))
                        span.set_metric("vllm.latency.e2e", float(time.time() - t0))
                        log.debug(
                            "[VLLM DEBUG] V1.generate: finishing span; input_tokens=%s output_tokens=%s ttft=%s",
                            input_tokens, output_tokens, ttft_sec,
                        )
                    except Exception:
                        pass
                finally:
                    span.finish()
    return _iter()


@with_traced_module
def traced_llm_generate(vllm, pin, func, instance, args, kwargs):
    """Offline LLM entrypoint: wrap vllm.entrypoints.llm.LLM.generate.

    Emits one vllm.request span per RequestOutput returned.
    This covers offline usage where users call `LLM.generate([...])` directly.
    """
    integration: VLLMIntegration = getattr(vllm, "_datadog_integration", None)
    tracer = _get_pin_tracer()
    if tracer is None:
        try:
            log.debug("[VLLM DEBUG] LLM.generate: tracer unavailable; bypassing wrapper")
        except Exception:
            pass
        return func(*args, **kwargs)

    try:
        prompts = args[0] if args else kwargs.get("prompts")
        sampling_params = args[1] if len(args) > 1 else kwargs.get("sampling_params")
    except Exception:
        prompts = None
        sampling_params = None

    parent = tracer.current_span()
    t0 = time.time()
    try:
        outputs = func(*args, **kwargs)
    except Exception as e:
        try:
            log.debug("[VLLM DEBUG] LLM.generate: inner call raised: %s", type(e).__name__)
        except Exception:
            pass
        raise

    # outputs is expected to be a list[RequestOutput]
    try:
        iter_outputs = list(outputs) if outputs is not None else []
    except Exception:
        iter_outputs = []

    try:
        log.debug("[VLLM DEBUG] LLM.generate: returned %s RequestOutput(s)", len(iter_outputs))
    except Exception:
        pass

    # Try to discover model name from engine
    try:
        model_name = getattr(getattr(getattr(instance, "llm_engine", None), "model_config", None), "model", None)
    except Exception:
        model_name = None

    for ro in iter_outputs:
        try:
            span = tracer.start_span(
                "vllm.request",
                child_of=parent.context if parent else None,
                resource="vllm.request",
                span_type=SpanTypes.LLM if (integration and integration.llmobs_enabled) else None,
                activate=False,
            )
            if integration and integration.llmobs_enabled:
                span._set_ctx_item(INTEGRATION, integration._integration_name)
            span.set_metric(_SPAN_MEASURED_KEY, 1)

            # Aggregate metrics from RequestOutput
            try:
                prompt = getattr(ro, "prompt", None)
                prompt_token_ids = getattr(ro, "prompt_token_ids", None) or []
                input_tokens = len(prompt_token_ids) if isinstance(prompt_token_ids, list) else int(prompt_token_ids or 0)
            except Exception:
                prompt = None
                input_tokens = 0
            output_tokens = 0
            output_text = ""
            finish_reason = None
            stop_reason = None
            try:
                for comp in getattr(ro, "outputs", None) or []:
                    txt = getattr(comp, "text", None)
                    if txt:
                        output_text += txt
                    tids = getattr(comp, "token_ids", None)
                    if tids is not None:
                        output_tokens += (len(tids) if not isinstance(tids, int) else 1)
                    fr = getattr(comp, "finish_reason", None)
                    if fr is not None:
                        finish_reason = fr
                    sr = getattr(comp, "stop_reason", None)
                    if sr is not None:
                        stop_reason = sr
            except Exception:
                pass

            # Base tags and LLMObs
            try:
                if integration:
                    try:
                        integration._set_base_span_tags(span, model_name=model_name)
                    except Exception:
                        pass
                if integration and integration.llmobs_enabled:
                    integration.llmobs_set_tags(
                        span=span,
                        args=[],
                        kwargs={
                            "model_name": model_name,
                            "prompt": prompt if isinstance(prompt, str) else (prompts if isinstance(prompts, str) else None),
                            "output_text": output_text,
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "sampling_params": sampling_params,
                            "request_id": getattr(ro, "request_id", None),
                            "finish_reason": finish_reason,
                            "stop_reason": stop_reason,
                            "num_cached_tokens": getattr(ro, "num_cached_tokens", None),
                            "operation": "completion",
                            "lora_name": getattr(getattr(ro, "lora_request", None), "name", None),
                        },
                        response=None,
                        operation="completion",
                    )
                # E2E latency metric for this RequestOutput
                try:
                    span.set_metric("vllm.latency.e2e", float(time.time() - t0))
                except Exception:
                    pass
            finally:
                span.finish()
        except Exception as e:
            try:
                log.debug("[VLLM DEBUG] LLM.generate: failed to emit span for RequestOutput: %s", type(e).__name__)
            except Exception:
                pass
            continue

    return outputs

@with_traced_module
def traced_openaiserving_log_inputs(vllm, pin, func, instance, args, kwargs):
    """Capture raw prompt for OpenAI entrypoints before generate/encode.

    Stores a plaintext prompt or a list[int] token-ids in the current parent span
    context under key 'vllm.captured_prompt'. This is later used as a fallback
    in AsyncLLM.generate when RequestOutput.prompt is missing or not a string.
    """
    try:
        # args: (request_id: str, inputs: RequestPrompt, params, lora_request)
        inputs = args[1] if len(args) > 1 else kwargs.get("inputs")
    except Exception:
        inputs = None

    captured_prompt: Optional[object] = None
    try:
        if isinstance(inputs, str):
            captured_prompt = inputs
        elif isinstance(inputs, list) and inputs and isinstance(inputs[0], int):
            captured_prompt = inputs  # list[int]
        elif isinstance(inputs, dict):
            # Common shapes: {"prompt": str, "prompt_token_ids": list[int]}, or {"prompt_embeds": tensor}
            if "prompt" in inputs and isinstance(inputs["prompt"], str):
                captured_prompt = inputs["prompt"]
            elif "prompt_token_ids" in inputs and isinstance(inputs["prompt_token_ids"], list):
                captured_prompt = list(inputs["prompt_token_ids"])  # list[int]
    except Exception:
        pass

    try:
        parent = pin.tracer.current_span() if pin and pin.tracer else None
        if parent is not None and captured_prompt is not None:
            parent._set_ctx_item("vllm.captured_prompt", captured_prompt)
            try:
                log.debug("[VLLM DEBUG] OpenAIServing._log_inputs: captured_prompt_type=%s", type(captured_prompt).__name__)
            except Exception:
                pass
        else:
            try:
                log.debug(
                    "[VLLM DEBUG] OpenAIServing._log_inputs: no parent or no captured prompt; has_parent=%s captured=%s",
                    bool(parent), captured_prompt is not None,
                )
            except Exception:
                pass
    except Exception:
        pass

    return func(*args, **kwargs)


@with_traced_module
async def traced_openai_serving_embedding_create_embedding(vllm, pin, func, instance, args, kwargs):
    """Batch aggregator wrapper for OpenAI Embeddings server endpoint.

    Creates a single vllm.request span covering all inner AsyncLLM.encode calls
    and merges prompt_token_ids and input tokens across chunks.
    """
    integration: VLLMIntegration = getattr(vllm, "_datadog_integration", None)
    tracer = _get_pin_tracer()
    if tracer is None:
        try:
            log.debug("[VLLM DEBUG] OAIEmbedding.create_embedding: tracer unavailable; bypassing aggregator")
        except Exception:
            pass
        return await func(*args, **kwargs)

    # Activate aggregator span so inner encode wrapper can detect it
    with tracer.trace(
        "vllm.request",
        service=None,
        resource="vllm.request",
        span_type=SpanTypes.LLM if (integration and integration.llmobs_enabled) else None,
    ) as span:
        try:
            if integration and integration.llmobs_enabled:
                span._set_ctx_item(INTEGRATION, integration._integration_name)
            span.set_metric(_SPAN_MEASURED_KEY, 1)
            # Base model tag
            model_name = getattr(getattr(instance, "model_config", None), "model", None)
            if integration:
                try:
                    integration._set_base_span_tags(span, model_name=model_name)
                except Exception:
                    pass
            try:
                log.debug("[VLLM DEBUG] OAIEmbedding.create_embedding: aggregator span started; model=%s", model_name)
            except Exception:
                pass

            # Initialize aggregator state on the span
            agg = {
                "merged_prompt_token_ids": [],
                "input_tokens": 0,
                "embedding_dim": None,
                "t0": time.time(),
                "inputs": [],
                "num_embeddings": 0,
            }
            span._set_ctx_items({"vllm.embedding_batch": True, "vllm.embedding_agg": agg})

            # Run the actual handler
            # Try to extract original embedding inputs from request to match OpenAI semantics
            try:
                req = args[0] if args else kwargs.get("request")
                embedding_inputs = getattr(req, "input", None)
                normalized_inputs = []
                if isinstance(embedding_inputs, str) or (
                    isinstance(embedding_inputs, list)
                    and len(embedding_inputs) > 0
                    and isinstance(embedding_inputs[0], int)
                ):
                    normalized_inputs = [embedding_inputs]
                elif isinstance(embedding_inputs, list):
                    normalized_inputs = embedding_inputs
                elif embedding_inputs is not None:
                    normalized_inputs = [embedding_inputs]
                agg["inputs"] = list(normalized_inputs)
                agg["num_embeddings"] = int(len(normalized_inputs))
                # Capture encoding_format if present on request
                try:
                    agg["encoding_format"] = getattr(req, "encoding_format", None)
                except Exception:
                    agg["encoding_format"] = None
            except Exception:
                pass

            resp = await func(*args, **kwargs)

            # Finalize LLMObs tags using aggregated data
            try:
                if integration and integration.llmobs_enabled:
                    # Determine number of embeddings
                    num_embeddings = int(agg.get("num_embeddings", 0) or 0)
                    try:
                        if not num_embeddings:
                            data = getattr(resp, "data", None)
                            if data is None and isinstance(resp, dict):
                                data = resp.get("data")
                            if isinstance(data, list):
                                num_embeddings = len(data)
                    except Exception:
                        pass
                    integration.llmobs_set_tags(
                        span=span,
                        args=[],
                        kwargs={
                            "model_name": model_name,
                            # Provide OpenAI-like input list for better UI formatting
                            "input": list(agg.get("inputs", [])),
                            "num_embeddings": num_embeddings,
                            "output_text": None,
                            "input_tokens": int(agg.get("input_tokens", 0) or 0),
                            "output_tokens": 0,
                            "encoding_format": agg.get("encoding_format"),
                            "sampling_params": None,
                            "request_id": None,
                            "operation": "embedding",
                            "lora_name": None,
                            "embedding_dim": agg.get("embedding_dim"),
                        },
                        response=None,
                        operation="embedding",
                    )
                # E2E latency metric for the aggregated request
                try:
                    span.set_metric("vllm.latency.e2e", float(time.time() - agg.get("t0", time.time())))
                    log.debug(
                        "[VLLM DEBUG] OAIEmbedding.create_embedding: finishing aggregator; num_inputs=%s input_tokens=%s",
                        agg.get("num_embeddings"), agg.get("input_tokens"),
                    )
                except Exception:
                    pass
            finally:
                return resp
        except Exception as e:
            span.set_exc_info(type(e), e, e.__traceback__)
            raise


@with_traced_module
def traced_asyncllm_encode(vllm, pin, func, instance, args, kwargs):
    """V1: Wrap AsyncLLM.encode to emit a span per embedding request."""
    prompt_arg = args[0] if args else kwargs.get("prompt")
    pooling_params = args[1] if len(args) > 1 else kwargs.get("pooling_params")
    request_id = args[2] if len(args) > 2 else kwargs.get("request_id")
    lora_request = args[3] if len(args) > 3 else kwargs.get("lora_request")

    integration: VLLMIntegration = getattr(vllm, "_datadog_integration", None)
    tracer = _get_pin_tracer()
    parent = tracer.current_span() if tracer else None
    under_batch = bool(parent and parent._get_ctx_item("vllm.embedding_batch"))
    span = tracer.start_span(
        "vllm.request",
        child_of=parent.context if parent else None,
        resource="vllm.request",
        span_type=SpanTypes.LLM if (integration and integration.llmobs_enabled) else None,
        activate=False,
    ) if (tracer and not under_batch) else None
    try:
        log.debug(
            "[VLLM DEBUG] V1.encode: entered; tracer=%s under_batch=%s will_start_span=%s",
            bool(tracer), under_batch, bool(span),
        )
    except Exception:
        pass

    input_tokens = 0
    embedding_dim: Optional[int] = None
    first_prompt_token_ids: Optional[list[int]] = None
    t0 = time.time()

    async def _iter():
        nonlocal input_tokens, embedding_dim, first_prompt_token_ids
        try:
            try:
                log.debug("[VLLM DEBUG] V1.encode: calling inner func")
            except Exception:
                pass
            res = func(*args, **kwargs)
            if hasattr(res, "__await__"):
                res = await res
            async for out in res:
                pts = getattr(out, "prompt_token_ids", None)
                if not input_tokens and pts is not None:
                    input_tokens = len(pts)
                if first_prompt_token_ids is None and isinstance(pts, list) and len(pts) > 0:
                    first_prompt_token_ids = list(pts)
                if under_batch and isinstance(pts, list) and len(pts) > 0:
                    try:
                        agg = parent._get_ctx_item("vllm.embedding_agg") if parent else None
                        if isinstance(agg, dict):
                            agg.setdefault("merged_prompt_token_ids", [])
                            agg["merged_prompt_token_ids"].extend(list(pts))
                            agg["input_tokens"] = int(agg.get("input_tokens", 0)) + len(pts)
                    except Exception:
                        pass
                try:
                    data = getattr(getattr(out, "outputs", None), "data", None)
                    if data is not None and hasattr(data, "shape") and len(data.shape) >= 1:
                        embedding_dim = int(data.shape[-1])
                        if under_batch and parent is not None:
                            try:
                                agg = parent._get_ctx_item("vllm.embedding_agg")
                                if isinstance(agg, dict) and agg.get("embedding_dim") is None:
                                    agg["embedding_dim"] = embedding_dim
                            except Exception:
                                pass
                except Exception:
                    pass
                yield out
        except Exception as e:
            if span is not None:
                try:
                    span.set_exc_info(type(e), e, e.__traceback__)
                    log.debug("[VLLM DEBUG] V1.encode: exception set on span: %s", type(e).__name__)
                except Exception:
                    pass
            raise
        finally:
            if under_batch:
                # Do not finish any inner span; batch aggregator will finalize.
                return
            if span is not None:
                try:
                    model_name = getattr(getattr(instance, "model_config", None), "model", None)
                    lora_name = getattr(lora_request, "lora_name", None) or getattr(lora_request, "name", None)
                    # Attempt detokenization of prompt_token_ids when prompt was not provided
                    detok_prompt: Optional[str] = None
                    try:
                        tk_group = getattr(instance, "tokenizer", None)
                        if tk_group is not None and first_prompt_token_ids:
                            tk = tk_group.get_lora_tokenizer(lora_request)
                            if tk is not None:
                                detok_prompt = decode_tokens(tk, first_prompt_token_ids, skip_special_tokens=True)
                    except Exception:
                        detok_prompt = None
                    if integration and integration.llmobs_enabled:
                        integration.llmobs_set_tags(
                            span=span,
                            args=[],
                            kwargs={
                                "model_name": model_name,
                                "prompt": detok_prompt if detok_prompt is not None else (first_prompt_token_ids if first_prompt_token_ids is not None else prompt_arg),
                                "output_text": None,
                                "input_tokens": input_tokens,
                                "output_tokens": 0,
                                "sampling_params": pooling_params,
                                "request_id": request_id,
                                "operation": "embedding",
                                "lora_name": lora_name,
                                "embedding_dim": embedding_dim,
                            },
                            response=None,
                            operation="embedding",
                        )
                    # API-server-side latency (v1): e2e only for pooling
                    try:
                        span.set_metric("vllm.latency.e2e", float(time.time() - t0))
                        log.debug(
                            "[VLLM DEBUG] V1.encode: finishing span; input_tokens=%s embedding_dim=%s",
                            input_tokens, embedding_dim,
                        )
                    except Exception:
                        pass
                finally:
                    span.finish()
    return _iter()


def patch():
    log.debug("[VLLM DEBUG] Loading vLLM integration")
    if getattr(vllm, "_datadog_patch", False):
        return

    vllm._datadog_patch = True

    # Attach global tracer to the vllm Pin so wrappers can start spans
    try:
        Pin(tracer=dd_tracer).onto(vllm)
        log.debug("[VLLM DEBUG] Pin attached with global tracer: %s", dd_tracer is not None)
    except Exception as e:
        Pin().onto(vllm)
        log.debug("[VLLM DEBUG] Pin attached without tracer due to error: %s", type(e).__name__)
    integration = VLLMIntegration(integration_config=config.vllm)
    vllm._datadog_integration = integration
    # V0 engine-side: hook RequestOutputFactory.create
    wrap("vllm.outputs", "RequestOutputFactory.create", traced_v0_requestoutputfactory_create(vllm))
    # V1 API-server-side: hook AsyncLLM.generate and AsyncLLM.encode
    wrap("vllm.v1.engine.async_llm", "AsyncLLM.generate", traced_asyncllm_generate(vllm))
    wrap("vllm.v1.engine.async_llm", "AsyncLLM.encode", traced_asyncllm_encode(vllm))
    # OpenAI server embedding endpoint aggregator: make a single span for batched inner encodes
    wrap("vllm.entrypoints.openai.serving_embedding", "OpenAIServingEmbedding.create_embedding", traced_openai_serving_embedding_create_embedding(vllm))
    # Capture raw prompt at OpenAI serving layer before tokenization/logging
    wrap("vllm.entrypoints.openai.serving_engine", "OpenAIServing._log_inputs", traced_openaiserving_log_inputs(vllm))
    # Offline LLM entrypoint (V0/V1 auto): LLM.generate
    wrap("vllm.entrypoints.llm", "LLM.generate", traced_llm_generate(vllm))
    # Post-wrap verification logs
    try:
        import importlib
        mod_v1 = importlib.import_module("vllm.v1.engine.async_llm")
        gen_attr = getattr(mod_v1.AsyncLLM, "generate", None)
        enc_attr = getattr(mod_v1.AsyncLLM, "encode", None)
        mod_out = importlib.import_module("vllm.outputs")
        create_attr = getattr(mod_out, "RequestOutputFactory", None)
        create_fn = getattr(create_attr, "create", None) if create_attr else None
        log.debug(
            "[VLLM DEBUG] Verify wrap: AsyncLLM.generate wrapped=%s; encode wrapped=%s; RequestOutputFactory.create exists=%s",
            iswrapped(gen_attr) if gen_attr else None,
            iswrapped(enc_attr) if enc_attr else None,
            bool(create_fn),
        )
    except Exception as e:
        log.debug("[VLLM DEBUG] Verify wrap: failed with %s", type(e).__name__)
    log.debug("[VLLM DEBUG] Patched vLLM V0 factory and V1 AsyncLLM hooks")


def unpatch():
    """Remove vLLM patches."""
    if not getattr(vllm, "_datadog_patch", False):
        return
    
    vllm._datadog_patch = False

    # Unpatch hooks
    unwrap("vllm.outputs", "RequestOutputFactory.create")
    unwrap("vllm.v1.engine.async_llm", "AsyncLLM.generate")
    unwrap("vllm.v1.engine.async_llm", "AsyncLLM.encode")
    unwrap("vllm.entrypoints.openai.serving_embedding", "OpenAIServingEmbedding.create_embedding")
    unwrap("vllm.entrypoints.openai.serving_engine", "OpenAIServing._log_inputs")

    # Clear integration holder
    if hasattr(vllm, "_datadog_integration"):
        delattr(vllm, "_datadog_integration")