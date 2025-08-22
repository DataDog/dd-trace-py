from re import I
from typing import Any, Mapping, Optional

from ddtrace.ext import SpanTypes
from ddtrace.llmobs._constants import INTEGRATION
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
    log.debug("[VLLM DEBUG] Starting generate from %s", class_name)
    
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
    # engine_prompt is a TokensPrompt
    for request_prompt, engine_prompt in zip(request_prompts, engine_prompts):
        log.debug("[VLLM DEBUG] request_prompt: %s", request_prompt)
        if isinstance(request_prompt, str):
            log.debug("[VLLM DEBUG] request_prompt is str")
            engine_prompt["prompt"] = request_prompt
        elif isinstance(request_prompt, dict) and "prompt" in request_prompt:
            log.debug("[VLLM DEBUG] request_prompt is dict")
            engine_prompt["prompt"] = request_prompt["prompt"]
        elif isinstance(request_prompt, list) and can_decode:
            log.debug("[VLLM DEBUG] request_prompt is list")
            engine_prompt["prompt"] = tokenizer.decode(request_prompt)
        log.debug("[VLLM DEBUG] engine_prompt: %s", engine_prompt)

    return conversation, request_prompts, engine_prompts 
    
@with_traced_module_async
async def traced_preprocess_completion(vllm, pin, func, instance, args, kwargs):
    request_prompts, engine_prompts = await func(*args, **kwargs)
    log.debug("[VLLM DEBUG] request_prompts: %s", request_prompts)
    log.debug("[VLLM DEBUG] engine_prompts: %s", engine_prompts)
    for request_prompt, engine_prompt in zip(request_prompts, engine_prompts):
        #request_prompt is a TextTokensPrompt
        engine_prompt["prompt"] = request_prompt["prompt"]
        log.debug("[VLLM DEBUG] engine_prompt: %s", engine_prompt)
    
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

    # Patch all generate methods to inject trace context
    log.debug("[VLLM DEBUG] Patching vLLM")
    #wrap("vllm.entrypoints.llm", "LLM.generate", traced_generate(vllm))
    #wrap("vllm.v1.engine.async_llm", "AsyncLLM.generate", traced_generate(vllm))
    #wrap("vllm.engine.protocol", "EngineClient.generate", traced_generate(vllm))
    wrap("vllm.engine.multiprocessing.client", "MQLLMEngineClient.generate", traced_generate(vllm))
    wrap("vllm.engine.async_llm_engine", "AsyncLLMEngine.generate", traced_generate(vllm))
    
    # Patch _process_model_outputs to create comprehensive spans
    wrap("vllm.engine.llm_engine", "LLMEngine._process_model_outputs", traced_process_model_outputs(vllm))
    
    # Patch OpenAI-compatible serving endpoints
    wrap("vllm.entrypoints.openai.serving_engine", "OpenAIServing._preprocess_completion", traced_preprocess_completion(vllm))
    wrap("vllm.entrypoints.openai.serving_engine", "OpenAIServing._preprocess_chat", traced_preprocess_chat(vllm))

    wrap("vllm.engine.llm_engine", "LLMEngine.add_request", traced_add_request(vllm))
    log.debug("[VLLM DEBUG] Patched vLLM")


def unpatch():
    """Remove vLLM patches."""
    if not getattr(vllm, "_datadog_patch", False):
        return
    
    vllm._datadog_patch = False

    #unwrap("vllm.entrypoints.llm", "LLM.generate")
    #unwrap("vllm.v1.engine.async_llm", "AsyncLLM.generate")
    #unwrap("vllm.engine.protocol", "EngineClient.generate")
    unwrap("vllm.engine.multiprocessing.client", "MQLLMEngineClient.generate")
    unwrap("vllm.engine.async_llm_engine", "AsyncLLMEngine.generate")
    unwrap("vllm.engine.llm_engine", "LLMEngine._process_model_outputs")
    
    # Remove OpenAI-compatible serving endpoint patches
    unwrap("vllm.entrypoints.openai.serving_engine", "OpenAIServing._preprocess_completion")
    unwrap("vllm.entrypoints.openai.serving_engine", "OpenAIServing._preprocess_chat")

    delattr(vllm, "_datadog_integration")