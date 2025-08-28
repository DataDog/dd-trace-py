from typing import Any, Mapping, Optional

from ddtrace.ext import SpanTypes
from ddtrace.llmobs._constants import (
    INTEGRATION,
    SPAN_KIND,
    MODEL_NAME,
    MODEL_PROVIDER,
    METADATA,
    METRICS,
    INPUT_MESSAGES,
    OUTPUT_MESSAGES,
    INPUT_TOKENS_METRIC_KEY,
    OUTPUT_TOKENS_METRIC_KEY,
    TOTAL_TOKENS_METRIC_KEY,
)
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.settings import integration
import vllm

from ddtrace import config, tracer
from ddtrace.contrib.trace_utils import with_traced_module_async, wrap, unwrap, with_traced_module
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.vllm import VLLMIntegration
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Pin


log = get_logger(__name__)


# Integration configuration
config._add("vllm", {})


@with_traced_module
def traced_generate(vllm, pin, func, instance, args, kwargs):
    # Get class name for logging
    class_name = instance.__class__.__name__
    module_name = getattr(instance.__class__, "__module__", "")
    log.debug("[VLLM DEBUG] Starting generate from %s", class_name)
    
    # For V1, do NOT inject trace_headers (Processor rejects it with ValueError).
    if not module_name.startswith("vllm.v1."):
        # Inject trace context for span linking (no prompt data needed)
        current_span = tracer.current_span()
        if current_span:
            trace_headers = kwargs.get("trace_headers", {})
            if not isinstance(trace_headers, dict):
                trace_headers = {}
            # Inject current span context for proper trace linking
            HTTPPropagator.inject(current_span.context, trace_headers)
            kwargs["trace_headers"] = trace_headers
            log.debug("[VLLM DEBUG] Injected trace context for span linking")
        else:
            log.debug("[VLLM DEBUG] No current span found, skipping trace injection")

    prompt = args[0] if len(args) > 0 else kwargs.get("prompt")
    log.debug("[VLLM DEBUG] %s.generate Prompt: %s", class_name, prompt)

    # Call original function
    return func(*args, **kwargs)


@with_traced_module
def traced_process_model_outputs(vllm, pin, func, instance, args, kwargs):
    # Grab ctx (SchedulerContext) from positional or keyword args BEFORE calling the original method
    ctx = kwargs.get("ctx")
    if ctx is None and args:
        ctx = args[0]

    # Capture the first queue element (scheduler_outputs lives inside) *before* it is popped by the
    # original implementation. This ensures we still have access to the data after the original call
    output_data = None
    if ctx is not None and getattr(ctx, "output_queue", None):
        try:
            output_data = ctx.output_queue[0]
        except Exception:
            output_data = None

    # Call the original implementation (this may mutate ctx.output_queue)
    result = func(*args, **kwargs)

    # If we were able to snapshot an entry from the queue, inspect it now **after** core processing is done
    if output_data and len(output_data) >= 3:
        _, _, scheduler_outputs = output_data[:3]

        # Get model name from the engine instance for span tagging
        model_name = getattr(getattr(instance, 'model_config', None), 'model', None)

        # Get the vLLM integration instance for LLMObs processing
        integration: VLLMIntegration = vllm._datadog_integration

        for scheduled_seq_group in scheduler_outputs.scheduled_seq_groups:
            seq_group = scheduled_seq_group.seq_group

            if not seq_group.is_finished():
                continue  # only create spans for completed requests
            
            log.debug("[VLLM DEBUG] Grabbed ctx: %s", output_data)
            # Trace headers propagated from client
            trace_headers = getattr(seq_group, "trace_headers", None)
            if not trace_headers:
                continue

            log.debug("[VLLM DEBUG] Creating vllm.request span ; trace_headers=%s", trace_headers)

            parent_ctx = HTTPPropagator.extract(trace_headers)
            if parent_ctx and parent_ctx.trace_id:
                log.debug("[VLLM DEBUG] Extracted parent context ; trace_id=%s span_id=%s", parent_ctx.trace_id, parent_ctx.span_id)
        
            span = pin.tracer.start_span(
                    "vllm.request",
                    child_of=parent_ctx,
                    resource="vllm.request",
                    span_type=SpanTypes.LLM if integration.llmobs_enabled else None,
                    activate=False
            )
            if integration.llmobs_enabled:
                span._set_ctx_item(INTEGRATION, integration._integration_name)
            span.set_metric(_SPAN_MEASURED_KEY, 1)
            integration._set_base_span_tags(span, model_name=model_name)
            span.start_ns = int(seq_group.metrics.arrival_time * 1e9)
            
            # Extract request_id for OpenAI data retrieval
            request_id = getattr(seq_group, 'request_id', None)
            if not request_id and hasattr(seq_group, 'seqs') and seq_group.seqs:
                # Try to get request_id from first sequence
                first_seq = seq_group.seqs[0] if seq_group.seqs else None
                if first_seq and hasattr(first_seq, 'request_id'):
                    request_id = first_seq.request_id
            
            log.debug("[VLLM DEBUG] Found request_id for span: %s", request_id)
            for seq in seq_group.seqs:
                log.debug("[VLLM DEBUG] Found sequence: %s", seq.inputs)
            # Set LLMObs tags
            log.debug("[VLLM DEBUG] Content of _request_id_to_prompt: %s", integration._request_id_to_prompt)
            integration._llmobs_set_tags(
                span=span,
                args=[],
                kwargs={"model_name": model_name, "engine_instance": instance, "request_id": request_id},
                response=seq_group,
                operation="completion",
                request_id=request_id
            )
            
            span.finish()
            log.debug("[VLLM DEBUG] Span created trace_id=%s span_id=%s", parent_ctx.trace_id, span.span_id)
    else:
        log.debug("[VLLM DEBUG] No output_data captured or malformed entry, skipping span creation")

    return result


@with_traced_module_async
async def traced_preprocess_chat(vllm, pin, func, instance, args, kwargs):
    request = args[0]
    tokenizer = args[1]
    can_decode = tokenizer is not None and hasattr(tokenizer, "decode")
    log.debug("[VLLM DEBUG] Preprocessing chat message: %s", request.messages)

    (conversation, request_prompts, engine_prompts) = await func(*args, **kwargs)
    log.debug("[VLLM DEBUG] request_prompts: %s", request_prompts)
    log.debug("[VLLM DEBUG] engine_prompts: %s", engine_prompts)
    # Do not mutate engine_prompts; prompt propagation handled in _process_tokens wrapper
    for request_prompt in request_prompts:
        log.debug("[VLLM DEBUG] request_prompt: %s", request_prompt)
        if isinstance(request_prompt, str):
            log.debug("[VLLM DEBUG] request_prompt is str")
        elif isinstance(request_prompt, dict) and "prompt" in request_prompt:
            log.debug("[VLLM DEBUG] request_prompt is dict with prompt")
        elif isinstance(request_prompt, list) and can_decode:
            log.debug("[VLLM DEBUG] request_prompt is list (tokens)")

    return conversation, request_prompts, engine_prompts 
    
@with_traced_module_async
async def traced_preprocess_completion(vllm, pin, func, instance, args, kwargs):
    request_prompts, engine_prompts = await func(*args, **kwargs)
    log.debug("[VLLM DEBUG] request_prompts: %s", request_prompts)
    log.debug("[VLLM DEBUG] engine_prompts: %s", engine_prompts)
    # Do not mutate engine_prompts; prompt propagation handled in _process_tokens wrapper
    return request_prompts, engine_prompts


@with_traced_module
def traced_add_request(vllm, pin, func, instance, args, kwargs):
    log.debug("[VLLM DEBUG] Adding request: %s", args[1] if len(args) > 1 else kwargs.get("prompt"))
    request_id = args[0] if len(args) > 0 else kwargs.get("request_id")
    prompt = args[1] if len(args) > 1 else kwargs.get("prompt")
    if not request_id or not prompt or not prompt.get("prompt"):
        log.debug("[VLLM DEBUG] No request id or prompt, skipping")
        return func(*args, **kwargs)

    log.debug("[VLLM DEBUG] Storing request id to prompt mapping: %s -> %s", request_id, prompt)
    integration: VLLMIntegration = vllm._datadog_integration
    integration.store_request_id_to_prompt(request_id, prompt["prompt"])
    log.debug("[VLLM DEBUG] Content of _request_id_to_prompt: %s", integration._request_id_to_prompt)
    return func(*args, **kwargs)


# --- V1 support ---

@with_traced_module_async
async def traced_asyncllm__add_request(vllm, pin, func, instance, args, kwargs):
    """Capture parent context and prompt for V1 requests at add time.

    Signature: AsyncLLM._add_request(self, request, prompt, parent_req, index, queue)
    """
    try:
        request = args[0] if args else kwargs.get("request")
        prompt = args[1] if len(args) > 1 else kwargs.get("prompt")
        req_id = getattr(request, "request_id", None)
        integration: VLLMIntegration = vllm._datadog_integration
    except Exception:
        request = None
        prompt = None
        req_id = None
        integration = None

    # Store prompt and parent context to link the finishing span later
    if integration is not None and req_id:
        try:
            if prompt:
                integration.store_request_id_to_prompt(req_id, prompt)
        except Exception:
            # Fallback to a plain dict if the helper isn't available
            try:
                integration._request_id_to_prompt[req_id] = prompt or ""
            except Exception:
                pass

        current_span = pin.tracer.current_span()
        if current_span:
            if not hasattr(integration, "_parent_ctx_by_req"):
                integration._parent_ctx_by_req = {}
            integration._parent_ctx_by_req[req_id] = current_span.context

        # Store model name by request for tagging
        if not hasattr(integration, "_model_name_by_req"):
            integration._model_name_by_req = {}
        model_name = getattr(getattr(instance, "model_config", None), "model", None)
        if model_name:
            integration._model_name_by_req[req_id] = model_name

        # Store sampling params for LLMObs metadata later
        try:
            if not hasattr(integration, "_sampling_params_by_req"):
                integration._sampling_params_by_req = {}
            sampling_params = getattr(request, "sampling_params", None)
            integration._sampling_params_by_req[req_id] = sampling_params
        except Exception:
            pass

    return await func(*args, **kwargs)


@with_traced_module_async
async def traced_asyncllm_add_request(vllm, pin, func, instance, args, kwargs):
    """Diagnostic wrapper around AsyncLLM.add_request to surface exceptions."""
    try:
        return await func(*args, **kwargs)
    except Exception as e:
        try:
            request_id = args[0] if args else kwargs.get("request_id")
            prompt = args[1] if len(args) > 1 else kwargs.get("prompt")
            params = args[2] if len(args) > 2 else kwargs.get("params")
            log.exception("[VLLM DEBUG] AsyncLLM.add_request failed (request_id=%s, prompt_keys=%s, params=%s)",
                          request_id,
                          list(prompt.keys()) if isinstance(prompt, dict) else type(prompt).__name__,
                          getattr(params, "__class__", type(params)).__name__)
        except Exception:
            log.exception("[VLLM DEBUG] AsyncLLM.add_request failed (error logging fallback)")
        raise


@with_traced_module
def traced_processor_process_inputs(vllm, pin, func, instance, args, kwargs):
    """Diagnostic wrapper around Processor.process_inputs to log prompt extraction."""
    try:
        result = func(*args, **kwargs)
        try:
            prompt_str, engine_req = result
            req_id = getattr(engine_req, "request_id", None)
            ptids = getattr(engine_req, "prompt_token_ids", None)
            log.debug("[VLLM DEBUG] Processor.process_inputs -> request_id=%s prompt_str_present=%s prompt_token_ids_len=%s",
                      req_id, bool(prompt_str), len(ptids) if ptids is not None else None)
        except Exception:
            pass
        return result
    except Exception:
        log.exception("[VLLM DEBUG] Processor.process_inputs raised")
        raise

@with_traced_module
def traced_requeststate_make_request_output(vllm, pin, func, instance, args, kwargs):
    """Create vllm.request span when a V1 RequestState finishes and set LLMObs context.

    Also accumulates output text/tokens across streaming outputs so final span has full content.
    """
    out = func(*args, **kwargs)

    # Accumulate output text and token counts for this request
    try:
        req_id = getattr(instance, "request_id", None)
        integration: VLLMIntegration = vllm._datadog_integration
        if req_id and integration is not None:
            # Init accumulators
            if not hasattr(integration, "_accum_text_by_req"):
                integration._accum_text_by_req = {}
            if not hasattr(integration, "_accum_output_tokens_by_req"):
                integration._accum_output_tokens_by_req = {}
            if not hasattr(integration, "_input_tokens_by_req"):
                integration._input_tokens_by_req = {}

            # Input tokens (fixed per request)
            try:
                if req_id not in integration._input_tokens_by_req:
                    prompt_token_ids = getattr(out, "prompt_token_ids", None)
                    if prompt_token_ids is not None:
                        integration._input_tokens_by_req[req_id] = len(prompt_token_ids)
            except Exception:
                pass

            # Accumulate output text and tokens from this chunk
            try:
                for completion in getattr(out, "outputs", []) or []:
                    text = getattr(completion, "text", None)
                    if text:
                        integration._accum_text_by_req[req_id] = (
                            integration._accum_text_by_req.get(req_id, "") + text
                        )
                    token_ids = getattr(completion, "token_ids", None)
                    if token_ids is not None:
                        integration._accum_output_tokens_by_req[req_id] = (
                            integration._accum_output_tokens_by_req.get(req_id, 0)
                            + len(token_ids)
                        )
            except Exception:
                pass
    except Exception:
        req_id = None
        integration = None

    # Finalize span on finished output
    try:
        finished = getattr(out, "finished", False)
    except Exception:
        finished = False

    if finished and req_id and integration is not None:
        try:
            parent_ctx = None
            model_name = None
            if hasattr(integration, "_parent_ctx_by_req"):
                parent_ctx = integration._parent_ctx_by_req.pop(req_id, None)
            if hasattr(integration, "_model_name_by_req"):
                model_name = integration._model_name_by_req.pop(req_id, None)

            span = pin.tracer.start_span(
                "vllm.request",
                child_of=parent_ctx,
                resource="vllm.request",
                span_type=SpanTypes.LLM if integration.llmobs_enabled else None,
                activate=False,
            )
            if integration.llmobs_enabled:
                span._set_ctx_item(INTEGRATION, integration._integration_name)
            span.set_metric(_SPAN_MEASURED_KEY, 1)

            # Tag model name if known
            if model_name:
                try:
                    integration._set_base_span_tags(span, model_name=model_name)
                except Exception:
                    pass

            # Backdate start time to arrival if stats available
            try:
                stats = getattr(instance, "stats", None)
                arrival = getattr(stats, "arrival_time", None) if stats else None
                if arrival:
                    span.start_ns = int(arrival * 1e9)
            except Exception:
                pass

            # Build LLMObs context
            try:
                # Input prompt
                prompt = getattr(out, "prompt", None)
                if not prompt and hasattr(integration, "_request_id_to_prompt"):
                    prompt = integration._request_id_to_prompt.get(req_id)

                # Output text accumulated
                output_text = ""
                if hasattr(integration, "_accum_text_by_req"):
                    output_text = integration._accum_text_by_req.pop(req_id, "")
                if not output_text:
                    # fallback to current out outputs
                    for completion in getattr(out, "outputs", []) or []:
                        if getattr(completion, "text", None):
                            output_text += completion.text

                # Token metrics
                input_tokens = 0
                if hasattr(integration, "_input_tokens_by_req"):
                    input_tokens = integration._input_tokens_by_req.pop(req_id, 0)
                output_tokens = 0
                if hasattr(integration, "_accum_output_tokens_by_req"):
                    output_tokens = integration._accum_output_tokens_by_req.pop(req_id, 0)

                metrics_ctx = {}
                if input_tokens:
                    metrics_ctx[INPUT_TOKENS_METRIC_KEY] = input_tokens
                if output_tokens:
                    metrics_ctx[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
                if input_tokens or output_tokens:
                    metrics_ctx[TOTAL_TOKENS_METRIC_KEY] = input_tokens + output_tokens

                # Sampling metadata
                metadata_ctx = {}
                try:
                    sp = None
                    if hasattr(integration, "_sampling_params_by_req"):
                        sp = integration._sampling_params_by_req.pop(req_id, None)
                    for key in (
                        "temperature",
                        "max_tokens",
                        "top_p",
                        "top_k",
                        "n",
                        "presence_penalty",
                        "frequency_penalty",
                        "repetition_penalty",
                        "seed",
                    ):
                        val = getattr(sp, key, None) if sp is not None else None
                        if val is not None:
                            metadata_ctx[key] = val
                except Exception:
                    pass

                # Model fields
                provider = "vllm"
                model_short = model_name or ""
                if model_short and "/" in model_short:
                    provider, model_short = model_short.split("/", 1)

                ctx_items = {
                    SPAN_KIND: "llm",
                    MODEL_NAME: model_short or (model_name or ""),
                    MODEL_PROVIDER: provider,
                    METADATA: metadata_ctx,
                    METRICS: metrics_ctx,
                }
                if prompt:
                    ctx_items[INPUT_MESSAGES] = [{"content": prompt}]
                if output_text:
                    ctx_items[OUTPUT_MESSAGES] = [{"content": output_text}]

                span._set_ctx_items(ctx_items)
            except Exception as e:
                log.debug("[VLLM DEBUG] Failed to set LLMObs ctx: %s", e)

            # Best-effort cleanup for prompt map
            try:
                if hasattr(integration, "_request_id_to_prompt"):
                    integration._request_id_to_prompt.pop(req_id, None)
            except Exception:
                pass

            span.finish()
            log.debug("[VLLM DEBUG] Created vllm.request span for %s with trace_id %s", req_id, span.trace_id)
        except Exception as e:
            log.debug("[VLLM DEBUG] Failed to create vllm.request span: %s", e)

    return out


@with_traced_module
def traced_outputprocessor_abort_requests(vllm, pin, func, instance, args, kwargs):
    req_ids = args[0] if args else kwargs.get("request_ids") or []
    res = func(*args, **kwargs)
    try:
        integration: VLLMIntegration = vllm._datadog_integration
        if hasattr(integration, "_parent_ctx_by_req"):
            for rid in req_ids:
                integration._parent_ctx_by_req.pop(rid, None)
        if hasattr(integration, "_model_name_by_req"):
            for rid in req_ids:
                integration._model_name_by_req.pop(rid, None)
        if hasattr(integration, "_request_id_to_prompt"):
            for rid in req_ids:
                integration._request_id_to_prompt.pop(rid, None)
    except Exception:
        pass
    return res

def patch():
    log.debug("[VLLM DEBUG] Loading vLLM integration")
    if getattr(vllm, "_datadog_patch", False):
        return

    vllm._datadog_patch = True

    Pin().onto(vllm)
    integration = VLLMIntegration(integration_config=config.vllm)
    vllm._datadog_integration = integration
    # TODO: Check for integration.llmobs_enabled?
    # TODO: Patch beam_search?

    def safe_wrap(mod, name, wrapper):
        try:
            wrap(mod, name, wrapper)
        except Exception as e:
            log.debug("[VLLM DEBUG] Skipping wrap %s.%s: %s", mod, name, e)

    # Patch all generate methods to inject trace context where supported
    log.debug("[VLLM DEBUG] Patching vLLM")
    # V1 AsyncLLM.generate (trace_headers injection disabled internally)
    safe_wrap("vllm.v1.engine.async_llm", "AsyncLLM.generate", traced_generate(vllm))
    # V0 client/engine generate (if present)
    safe_wrap("vllm.engine.multiprocessing.client", "MQLLMEngineClient.generate", traced_generate(vllm))
    safe_wrap("vllm.engine.async_llm_engine", "AsyncLLMEngine.generate", traced_generate(vllm))

    # V0: process_model_outputs (if present)
    safe_wrap("vllm.engine.llm_engine", "LLMEngine._process_model_outputs", traced_process_model_outputs(vllm))

    # V1: create spans at finish and capture parent context at add
    safe_wrap("vllm.v1.engine.async_llm", "AsyncLLM._add_request", traced_asyncllm__add_request(vllm))
    safe_wrap("vllm.v1.engine.async_llm", "AsyncLLM.add_request", traced_asyncllm_add_request(vllm))
    safe_wrap("vllm.v1.engine.processor", "Processor.process_inputs", traced_processor_process_inputs(vllm))
    safe_wrap("vllm.v1.engine.output_processor", "RequestState.make_request_output", traced_requeststate_make_request_output(vllm))
    safe_wrap("vllm.v1.engine.output_processor", "OutputProcessor.abort_requests", traced_outputprocessor_abort_requests(vllm))

    # Ensure prompt text is propagated even when OpenAI serving passes token ids
    def _traced_process_tokens(vllm_module):
        @with_traced_module
        def _inner(vllm, pin, func, instance, args, kwargs):
            try:
                parsed_content = args[0] if args else kwargs.get("parsed_content")
                lora_request = None
                if len(args) >= 3:
                    lora_request = args[2]
                else:
                    lora_request = kwargs.get("lora_request")
            except Exception:
                parsed_content = None
                lora_request = None

            result = func(*args, **kwargs)
            try:
                # If prompt already present, nothing to do
                if isinstance(result, dict) and "prompt" in result:
                    return result

                prompt_text = None
                if isinstance(parsed_content, dict):
                    prompt_text = parsed_content.get("prompt")
                    if not prompt_text:
                        # Best-effort decode
                        token_ids = parsed_content.get("prompt_token_ids")
                        if token_ids and hasattr(instance, "get_tokenizer_group"):
                            try:
                                tok = instance.get_tokenizer_group().get_lora_tokenizer(lora_request)
                                prompt_text = tok.decode(token_ids)
                            except Exception:
                                prompt_text = None

                if isinstance(result, dict) and prompt_text:
                    result["prompt"] = prompt_text
            except Exception:
                pass
            return result

        # Return the actual wrapper by binding the module now
        return _inner(vllm_module)

    def _traced_process_tokens_async(vllm_module):
        @with_traced_module_async
        async def _inner(vllm, pin, func, instance, args, kwargs):
            try:
                parsed_content = args[0] if args else kwargs.get("parsed_content")
                lora_request = None
                if len(args) >= 3:
                    lora_request = args[2]
                else:
                    lora_request = kwargs.get("lora_request")
            except Exception:
                parsed_content = None
                lora_request = None

            result = await func(*args, **kwargs)
            try:
                if isinstance(result, dict) and "prompt" in result:
                    return result

                prompt_text = None
                if isinstance(parsed_content, dict):
                    prompt_text = parsed_content.get("prompt")
                    if not prompt_text:
                        token_ids = parsed_content.get("prompt_token_ids")
                        if token_ids and hasattr(instance, "get_tokenizer_group"):
                            try:
                                tok = instance.get_tokenizer_group().get_lora_tokenizer(lora_request)
                                prompt_text = tok.decode(token_ids)
                            except Exception:
                                prompt_text = None

                if isinstance(result, dict) and prompt_text:
                    result["prompt"] = prompt_text
            except Exception:
                pass
            return result

        return _inner(vllm_module)

    safe_wrap("vllm.inputs.preprocess", "InputPreprocessor._process_tokens", _traced_process_tokens(vllm))
    safe_wrap("vllm.inputs.preprocess", "InputPreprocessor._process_tokens_async", _traced_process_tokens_async(vllm))

    # Patch OpenAI-compatible serving endpoints (if server in use)
    safe_wrap("vllm.entrypoints.openai.serving_engine", "OpenAIServing._preprocess_completion", traced_preprocess_completion(vllm))
    safe_wrap("vllm.entrypoints.openai.serving_engine", "OpenAIServing._preprocess_chat", traced_preprocess_chat(vllm))

    # V0: capture prompt on add_request (if present)
    safe_wrap("vllm.engine.llm_engine", "LLMEngine.add_request", traced_add_request(vllm))
    log.debug("[VLLM DEBUG] Patched vLLM")


def unpatch():
    """Remove vLLM patches."""
    if not getattr(vllm, "_datadog_patch", False):
        return
    
    vllm._datadog_patch = False

    try:
        unwrap("vllm.v1.engine.async_llm", "AsyncLLM.generate")
    except Exception:
        pass
    try:
        unwrap("vllm.engine.multiprocessing.client", "MQLLMEngineClient.generate")
    except Exception:
        pass
    try:
        unwrap("vllm.engine.async_llm_engine", "AsyncLLMEngine.generate")
    except Exception:
        pass
    try:
        unwrap("vllm.engine.llm_engine", "LLMEngine._process_model_outputs")
    except Exception:
        pass
    # V1 unpatch
    try:
        unwrap("vllm.v1.engine.async_llm", "AsyncLLM._add_request")
    except Exception:
        pass
    try:
        unwrap("vllm.v1.engine.output_processor", "RequestState.make_request_output")
    except Exception:
        pass
    try:
        unwrap("vllm.v1.engine.output_processor", "OutputProcessor.abort_requests")
    except Exception:
        pass
    try:
        unwrap("vllm.v1.engine.async_llm", "AsyncLLM.add_request")
    except Exception:
        pass
    try:
        unwrap("vllm.v1.engine.processor", "Processor.process_inputs")
    except Exception:
        pass
    try:
        unwrap("vllm.inputs.preprocess", "InputPreprocessor._process_tokens")
    except Exception:
        pass
    try:
        unwrap("vllm.inputs.preprocess", "InputPreprocessor._process_tokens_async")
    except Exception:
        pass
    
    # Remove OpenAI-compatible serving endpoint patches
    try:
        unwrap("vllm.entrypoints.openai.serving_engine", "OpenAIServing._preprocess_completion")
    except Exception:
        pass
    try:
        unwrap("vllm.entrypoints.openai.serving_engine", "OpenAIServing._preprocess_chat")
    except Exception:
        pass

    delattr(vllm, "_datadog_integration")