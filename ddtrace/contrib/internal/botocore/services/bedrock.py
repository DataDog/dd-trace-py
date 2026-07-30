from contextvars import ContextVar
import json
import sys
from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name
from ddtrace.llmobs._constants import CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import CACHE_WRITE_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations._bedrock_inference_profiles import begin_resolve
from ddtrace.llmobs._integrations._bedrock_inference_profiles import lookup_inference_profile
from ddtrace.llmobs._integrations._bedrock_inference_profiles import record_inference_profile
from ddtrace.llmobs._integrations._bedrock_inference_profiles import record_resolve_failure
from ddtrace.llmobs._integrations.base_stream_handler import StreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import make_traced_stream
from ddtrace.llmobs._integrations.bedrock_utils import _AI21
from ddtrace.llmobs._integrations.bedrock_utils import _AMAZON
from ddtrace.llmobs._integrations.bedrock_utils import _ANTHROPIC
from ddtrace.llmobs._integrations.bedrock_utils import _COHERE
from ddtrace.llmobs._integrations.bedrock_utils import _META
from ddtrace.llmobs._integrations.bedrock_utils import _STABILITY
from ddtrace.llmobs._integrations.bedrock_utils import parse_model_id


log = get_logger(__name__)

# Set while resolving an application-inference-profile ARN via bedrock:GetInferenceProfile
# so the botocore patch skips tracing that internal control-plane call (no APM/LLM span).
_resolve_inference_profile_in_progress: ContextVar[bool] = ContextVar(
    "_dd_bedrock_resolve_inference_profile_in_progress", default=False
)


def traced_stream_read(traced_stream, original_read, amt=None):
    """Wraps around method to tags the response data and finish the span as the user consumes the stream."""
    handler = traced_stream.handler
    execution_ctx = handler.options.get("execution_ctx", {})
    try:
        body = original_read(amt=amt)
        handler.chunks.append(json.loads(body))
        if traced_stream.__wrapped__.tell() == int(traced_stream.__wrapped__._content_length):
            formatted_response = _extract_text_and_response_reason(execution_ctx, handler.chunks[0])
            core.dispatch("botocore.bedrock.process_response", (execution_ctx, formatted_response))
        return body
    except Exception:
        core.dispatch("botocore.patched_bedrock_api_call.exception", (execution_ctx, sys.exc_info()))
        raise


def traced_stream_readlines(traced_stream, original_readlines):
    """Wraps around method to tags the response data and finish the span as the user consumes the stream."""
    handler = traced_stream.handler
    execution_ctx = handler.options.get("execution_ctx", {})
    try:
        lines = original_readlines()
        for line in lines:
            handler.chunks.append(json.loads(line))
        formatted_response = _extract_text_and_response_reason(execution_ctx, handler.chunks[0])
        core.dispatch("botocore.bedrock.process_response", (execution_ctx, formatted_response))
        return lines
    except Exception:
        core.dispatch("botocore.patched_bedrock_api_call.exception", (execution_ctx, sys.exc_info()))
        raise


class BotocoreStreamingBodyStreamHandler(StreamHandler):
    def process_chunk(self, chunk, iterator=None):
        self.chunks.append(json.loads(chunk["chunk"]["bytes"]))

    def handle_exception(self, exception):
        core.dispatch(
            "botocore.patched_bedrock_api_call.exception", (self.options.get("execution_ctx", {}), sys.exc_info())
        )

    def finalize_stream(self, exception=None):
        if exception:
            return
        execution_ctx = self.options.get("execution_ctx", {})
        formatted_response = _extract_streamed_response(execution_ctx, self.chunks)
        core.dispatch("botocore.bedrock.process_response", (execution_ctx, formatted_response))


class BotocoreConverseStreamHandler(StreamHandler):
    def process_chunk(self, chunk: dict[str, Any], iterator=None):
        stream_processor = self.options.get("stream_processor", None)
        if stream_processor:
            stream_processor.send(chunk)

    def handle_exception(self, exception):
        stream_processor = self.options.get("stream_processor", None)
        execution_ctx = self.options.get("execution_ctx", {})
        core.dispatch("botocore.bedrock.process_response_converse", (execution_ctx, stream_processor))

    def finalize_stream(self, exception=None):
        if exception:
            return
        stream_processor = self.options.get("stream_processor", None)
        execution_ctx = self.options.get("execution_ctx", {})
        core.dispatch("botocore.bedrock.process_response_converse", (execution_ctx, stream_processor))


def make_botocore_streaming_body_traced_stream(streaming_body, execution_ctx):
    original_read = getattr(streaming_body, "read", None)
    original_readlines = getattr(streaming_body, "readlines", None)
    traced_stream = make_traced_stream(
        streaming_body,
        BotocoreStreamingBodyStreamHandler(None, None, None, None, execution_ctx=execution_ctx),
    )
    # add bedrock-specific methods to the traced stream
    if original_read:
        traced_stream.read = lambda amt=None: traced_stream_read(traced_stream, original_read, amt)
    if original_readlines:
        traced_stream.readlines = lambda: traced_stream_readlines(traced_stream, original_readlines)
    return traced_stream


def make_botocore_converse_traced_stream(stream, execution_ctx):
    stream_processor = execution_ctx["bedrock_integration"]._converse_output_stream_processor()
    next(stream_processor)
    return make_traced_stream(
        stream,
        BotocoreConverseStreamHandler(
            None, None, None, None, execution_ctx=execution_ctx, stream_processor=stream_processor
        ),
    )


def safe_token_count(token_count) -> Optional[int]:
    """
    Converse api returns integer token counts, while invoke api returns string token counts.

    Use this function to safely return an integer token count from either type.
    """
    if isinstance(token_count, int):
        return token_count
    elif isinstance(token_count, str) and token_count:
        return int(token_count)
    return None


def _set_llmobs_usage(
    ctx: core.ExecutionContext,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    total_tokens: Optional[int] = None,
    cache_read_tokens: Optional[int] = None,
    cache_write_tokens: Optional[int] = None,
) -> None:
    """
    Sets LLM usage metrics in the execution context for LLM Observability.
    """
    llmobs_usage = {}
    if input_tokens is not None:
        llmobs_usage["input_tokens"] = input_tokens
    if output_tokens is not None:
        llmobs_usage["output_tokens"] = output_tokens
    if total_tokens is not None:
        llmobs_usage["total_tokens"] = total_tokens
    if cache_read_tokens is not None:
        llmobs_usage[CACHE_READ_INPUT_TOKENS_METRIC_KEY] = cache_read_tokens
    if cache_write_tokens is not None:
        llmobs_usage[CACHE_WRITE_INPUT_TOKENS_METRIC_KEY] = cache_write_tokens
    if llmobs_usage:
        ctx.set_item("llmobs.usage", llmobs_usage)


def _extract_request_params_for_converse(params: dict[str, Any]) -> dict[str, Any]:
    """
    Extracts request parameters including prompt, temperature, top_p, max_tokens, and stop_sequences
        for converse and converse_stream.
    """
    messages = params.get("messages", [])
    inference_config = params.get("inferenceConfig", {})
    prompt = []
    system_content_block = params.get("system", None)
    if system_content_block:
        prompt.append({"role": "system", "content": system_content_block})
    prompt += messages
    tool_config = params.get("toolConfig", {})
    return {
        "prompt": prompt,
        "temperature": inference_config.get("temperature", ""),
        "top_p": inference_config.get("topP", ""),
        "max_tokens": inference_config.get("maxTokens", ""),
        "stop_sequences": inference_config.get("stopSequences", []),
        "tool_config": tool_config,
    }


def _extract_request_params_for_invoke(params: dict[str, Any], provider: str) -> dict[str, Any]:
    """
    Extracts request parameters including prompt, temperature, top_p, max_tokens, and stop_sequences
        for invoke.
    """
    request_body = json.loads(params.get("body"))
    model_id = params.get("modelId")
    if provider == _AI21:
        return {
            "prompt": request_body.get("prompt"),
            "temperature": request_body.get("temperature", ""),
            "top_p": request_body.get("topP", ""),
            "max_tokens": request_body.get("maxTokens", ""),
            "stop_sequences": request_body.get("stopSequences", []),
        }
    elif provider == _AMAZON and "embed" in model_id:
        return {"prompt": request_body.get("inputText")}
    elif provider == _AMAZON:
        text_generation_config = request_body.get("textGenerationConfig", {})
        return {
            "prompt": request_body.get("inputText"),
            "temperature": text_generation_config.get("temperature", ""),
            "top_p": text_generation_config.get("topP", ""),
            "max_tokens": text_generation_config.get("maxTokenCount", ""),
            "stop_sequences": text_generation_config.get("stopSequences", []),
        }
    elif provider == _ANTHROPIC:
        prompt = request_body.get("prompt", "")
        messages = request_body.get("messages", "")
        return {
            "prompt": prompt or messages,
            "temperature": request_body.get("temperature", ""),
            "top_p": request_body.get("top_p", ""),
            "top_k": request_body.get("top_k", ""),
            "max_tokens": request_body.get("max_tokens_to_sample", ""),
            "stop_sequences": request_body.get("stop_sequences", []),
        }
    elif provider == _COHERE and "embed" in model_id:
        return {
            "prompt": request_body.get("texts"),
            "input_type": request_body.get("input_type", ""),
            "truncate": request_body.get("truncate", ""),
        }
    elif provider == _COHERE:
        return {
            "prompt": request_body.get("prompt"),
            "temperature": request_body.get("temperature", ""),
            "top_p": request_body.get("p", ""),
            "top_k": request_body.get("k", ""),
            "max_tokens": request_body.get("max_tokens", ""),
            "stop_sequences": request_body.get("stop_sequences", []),
            "stream": request_body.get("stream", ""),
            "n": request_body.get("num_generations", ""),
        }
    elif provider == _META:
        return {
            "prompt": request_body.get("prompt"),
            "temperature": request_body.get("temperature", ""),
            "top_p": request_body.get("top_p", ""),
            "max_tokens": request_body.get("max_gen_len", ""),
        }
    elif provider == _STABILITY:
        # TODO: request/response formats are different for image-based models. Defer for now
        return {}
    return {}


def _extract_text_and_response_reason(ctx: core.ExecutionContext, body: dict[str, Any]) -> dict[str, list[str]]:
    text, finish_reason = "", ""
    model_name = ctx["model_name"]
    provider = ctx["model_provider"]
    try:
        if provider == _AI21:
            completions = body.get("completions", [])
            if completions:
                data = completions[0].get("data", {})
                text = data.get("text")
                finish_reason = completions[0].get("finishReason")
        elif provider == _AMAZON and "embed" in model_name:
            text = [body.get("embedding", [])]
        elif provider == _AMAZON:
            results = body.get("results", [])
            if results:
                text = results[0].get("outputText")
                finish_reason = results[0].get("completionReason")
        elif provider == _ANTHROPIC:
            text = body.get("completion", "") or body.get("content", "")
            finish_reason = body.get("stop_reason")
        elif provider == _COHERE and "embed" in model_name:
            text = body.get("embeddings", [[]])
        elif provider == _COHERE:
            generations = body.get("generations", [])
            text = [generation["text"] for generation in generations]
            finish_reason = [generation["finish_reason"] for generation in generations]
        elif provider == _META:
            text = body.get("generation")
            finish_reason = body.get("stop_reason")
        elif provider == _STABILITY:
            # TODO: request/response formats are different for image-based models. Defer for now
            pass
    except (IndexError, AttributeError, TypeError):
        log.warning("Unable to extract text/finish_reason from response body. Defaulting to empty text/finish_reason.")

    if not isinstance(text, list):
        text = [text]
    if not isinstance(finish_reason, list):
        finish_reason = [finish_reason]

    return {"text": text, "finish_reason": finish_reason}


def _extract_streamed_response(ctx: core.ExecutionContext, streamed_body: list[dict[str, Any]]) -> dict[str, list[str]]:
    text, finish_reason = "", ""
    model_name = ctx["model_name"]
    provider = ctx["model_provider"]
    try:
        if provider == _AI21:
            pass  # DEV: ai21 does not support streamed responses
        elif provider == _AMAZON and "embed" in model_name:
            pass  # DEV: amazon embed models do not support streamed responses
        elif provider == _AMAZON:
            text = "".join([chunk["outputText"] for chunk in streamed_body])
            finish_reason = streamed_body[-1]["completionReason"]
        elif provider == _ANTHROPIC:
            for chunk in streamed_body:
                if "completion" in chunk:
                    text += chunk["completion"]
                    if chunk["stop_reason"]:
                        finish_reason = chunk["stop_reason"]
                elif "delta" in chunk:
                    text += chunk["delta"].get("text", "")
                    if "stop_reason" in chunk["delta"]:
                        finish_reason = str(chunk["delta"]["stop_reason"])
        elif provider == _COHERE and "embed" in model_name:
            pass  # DEV: cohere embed models do not support streamed responses
        elif provider == _COHERE:
            if "is_finished" in streamed_body[0]:  # streamed response
                if "index" in streamed_body[0]:  # n >= 2
                    num_generations = int(ctx.find_item("num_generations") or 0)
                    text = [
                        "".join([chunk["text"] for chunk in streamed_body[:-1] if chunk["index"] == i])
                        for i in range(num_generations)
                    ]
                    finish_reason = [streamed_body[-1]["finish_reason"] for _ in range(num_generations)]
                else:
                    text = "".join([chunk["text"] for chunk in streamed_body[:-1]])
                    finish_reason = streamed_body[-1]["finish_reason"]
            else:
                text = [chunk["text"] for chunk in streamed_body[0]["generations"]]
                finish_reason = [chunk["finish_reason"] for chunk in streamed_body[0]["generations"]]
        elif provider == _META:
            text = "".join([chunk["generation"] for chunk in streamed_body])
            finish_reason = streamed_body[-1]["stop_reason"]
        elif provider == _STABILITY:
            pass  # DEV: we do not yet support image modality models
    except (IndexError, AttributeError):
        log.warning("Unable to extract text/finish_reason from response body. Defaulting to empty text/finish_reason.")

    if not isinstance(text, list):
        text = [text]
    if not isinstance(finish_reason, list):
        finish_reason = [finish_reason]

    return {"text": text, "finish_reason": finish_reason}


def _extract_streamed_response_metadata(
    ctx: core.ExecutionContext, streamed_body: list[dict[str, Any]]
) -> dict[str, Any]:
    """
    Returns token usage metadata from streamed response, sets it in the context for LLMObs, and returns it.
    """
    provider = ctx["model_provider"]
    metadata = {}
    if provider == _AI21:
        pass  # ai21 does not support streamed responses
    elif provider in [_AMAZON, _ANTHROPIC, _COHERE, _META] and streamed_body:
        metadata = streamed_body[-1].get("amazon-bedrock-invocationMetrics", {})
    elif provider == _STABILITY:
        # TODO: figure out extraction for image-based models
        pass

    input_tokens = metadata.get("inputTokenCount")
    output_tokens = metadata.get("outputTokenCount")

    _set_llmobs_usage(ctx, safe_token_count(input_tokens), safe_token_count(output_tokens), None, None, None)
    return {
        "response.duration": metadata.get("invocationLatency", None),
        "usage.prompt_tokens": input_tokens,
        "usage.completion_tokens": output_tokens,
    }


def handle_bedrock_request(ctx: core.ExecutionContext) -> None:
    """Perform request param extraction and tagging."""
    request_params = (
        _extract_request_params_for_converse(ctx["params"])
        if ctx["resource"] in ("Converse", "ConverseStream")
        else _extract_request_params_for_invoke(ctx["params"], ctx["model_provider"])
    )

    core.dispatch("botocore.patched_bedrock_api_call.started", (ctx, request_params))
    if ctx["bedrock_integration"].llmobs_enabled:
        ctx.set_item("llmobs.request_params", request_params)


def handle_bedrock_response(
    ctx: core.ExecutionContext,
    result: dict[str, Any],
) -> dict[str, Any]:
    metadata = result["ResponseMetadata"]
    http_headers = metadata["HTTPHeaders"]

    total_tokens = None
    input_tokens = http_headers.get("x-amzn-bedrock-input-token-count", "")
    output_tokens = http_headers.get("x-amzn-bedrock-output-token-count", "")
    request_latency = str(http_headers.get("x-amzn-bedrock-invocation-latency", ""))

    cache_read_tokens = None
    cache_write_tokens = None

    if ctx["resource"] == "Converse":
        if "metrics" in result:
            latency = result.get("metrics", {}).get("latencyMs", "")
            request_latency = str(latency) if latency else request_latency
        if "usage" in result:
            usage = result.get("usage", {})
            if usage:
                input_tokens = usage.get("inputTokens", input_tokens)
                output_tokens = usage.get("outputTokens", output_tokens)
                total_tokens = usage.get("totalTokens", total_tokens)
                cache_read_tokens = usage.get("cacheReadInputTokenCount", None) or usage.get(
                    "cacheReadInputTokens", None
                )
                cache_write_tokens = usage.get("cacheWriteInputTokenCount", None) or usage.get(
                    "cacheWriteInputTokens", None
                )

        if "stopReason" in result:
            ctx.set_item("llmobs.stop_reason", result.get("stopReason"))

    _set_llmobs_usage(
        ctx,
        safe_token_count(input_tokens),
        safe_token_count(output_tokens),
        safe_token_count(total_tokens),
        safe_token_count(cache_read_tokens),
        safe_token_count(cache_write_tokens),
    )

    if ctx["resource"] == "Converse":
        core.dispatch("botocore.bedrock.process_response_converse", (ctx, result))
        return result
    if ctx["resource"] == "ConverseStream":
        if "stream" in result:
            result["stream"] = make_botocore_converse_traced_stream(result["stream"], ctx)
        return result

    body = result["body"]
    result["body"] = make_botocore_streaming_body_traced_stream(body, ctx)
    return result


def _region_from_arn(arn):
    # arn:aws:bedrock:<region>:<account>:application-inference-profile/<id>
    parts = arn.split(":")
    return parts[3] if len(parts) > 4 and parts[3] else None


def _foundation_model_id_from_profile(response):
    """Return the model id backing this application inference profile, or None."""
    models = response.get("models") or []
    if not models:
        return None
    model_arn = models[0].get("modelArn") or ""
    return model_arn.rsplit("/", 1)[-1] or None


def _frozen_credentials(instance):
    """Snapshot the runtime client's credentials to reuse on the control-plane client.
    No public API exposes them, so fall through the private paths; None if absent.
    """
    get_credentials = getattr(instance, "_get_credentials", None)
    credentials = get_credentials() if callable(get_credentials) else None
    if credentials is None:
        credentials = getattr(getattr(instance, "_request_signer", None), "_credentials", None)
    return credentials.get_frozen_credentials() if credentials is not None else None


def _fetch_inference_profile_base_model(instance, profile_arn):
    """Resolve `profile_arn` to its base foundation-model id via ``bedrock:GetInferenceProfile``,
    reusing the runtime client's credentials and region, and cache it. Returns the id or None.

    Never raises into the caller: any failure backs the ARN off (retried later, never
    permanently) and is logged. Even credential lookup, which can trigger a signer/SSO
    refresh, runs in the guard.
    """
    try:
        import botocore.config
        import botocore.session

        frozen = _frozen_credentials(instance)
        if frozen is None:
            _note_resolve_failure(profile_arn, "no credentials on the client")
            return None
        # Bound this hidden call so a slow/hung GetInferenceProfile can't stall the caller's
        # request. No in-call retries: a failure backs off and a future request retries.
        resolve_config = botocore.config.Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 1})
        base_config = getattr(instance, "_client_config", None)
        client_config = base_config.merge(resolve_config) if base_config is not None else resolve_config
        client = botocore.session.get_session().create_client(
            "bedrock",
            region_name=_region_from_arn(profile_arn) or instance.meta.region_name,
            aws_access_key_id=frozen.access_key,
            aws_secret_access_key=frozen.secret_key,
            aws_session_token=frozen.token,
            config=client_config,
        )
        token = _resolve_inference_profile_in_progress.set(True)
        try:
            response = client.get_inference_profile(inferenceProfileIdentifier=profile_arn)
        finally:
            _resolve_inference_profile_in_progress.reset(token)
        base_model_id = _foundation_model_id_from_profile(response)
    except Exception:
        _note_resolve_failure(profile_arn, "GetInferenceProfile call failed", exc_info=True)
        return None

    if not base_model_id:
        _note_resolve_failure(profile_arn, "no underlying foundation model in the profile")
        return None
    record_inference_profile(profile_arn, base_model_id)
    return base_model_id


def _note_resolve_failure(profile_arn, reason, exc_info=False) -> None:
    """Back off the profile after a failed resolution (retried later, never permanently) and
    log it: warn on the first failure, debug on repeats so a persistently unresolvable profile
    doesn't spam warnings.
    """
    delay, count = record_resolve_failure(profile_arn)
    if count <= 1:
        log.warning(
            "Bedrock inference profile %s could not be resolved (%s); skipping future resolution attempts for ~%ds",
            profile_arn,
            reason,
            int(delay),
            exc_info=exc_info,
        )
    else:
        log.debug(
            "Bedrock inference profile %s still unresolved (%s, attempt %d); "
            "skipping future resolution attempts for ~%ds",
            profile_arn,
            reason,
            count,
            int(delay),
        )


def _resolve_application_inference_profile(model_id, model_provider, model_name, instance=None, llmobs_enabled=False):
    """If model_id is an application-inference-profile ARN whose base model can be
    resolved, return (model_id, model_provider, model_name) with model_id replaced by
    the base model id instead of the opaque ARN. Otherwise return the inputs unchanged.

    The base model is taken from the cache (populated by the langchain integration) or,
    when ``DD_BOTOCORE_BEDROCK_RESOLVE_INFERENCE_PROFILE`` is enabled and LLM Obs is
    enabled for this call, resolved with an extra ``bedrock:GetInferenceProfile`` call
    and cached. The extra call is skipped for APM-only usage since it only benefits
    LLM Obs cost data - a cache hit still applies either way.

    Overriding model_id matters because the LLM Obs annotator reads
    ``ctx.get_item("model_id")`` first and only falls back to ``model_name``.
    """
    if not isinstance(model_id, str) or "application-inference-profile/" not in model_id:
        return model_id, model_provider, model_name
    base_model_id = lookup_inference_profile(model_id)
    if (
        not base_model_id
        and instance is not None
        and llmobs_enabled
        and config.botocore["bedrock_resolve_inference_profile"]
    ):
        # Single-flight + backoff gate: only one thread attempts a given ARN at a time, and
        # only once its backoff window has elapsed. Others keep the opaque id for this call.
        with begin_resolve(model_id) as claimed:
            if claimed:
                base_model_id = _fetch_inference_profile_base_model(instance, model_id)
    if not base_model_id:
        return model_id, model_provider, model_name
    new_provider, new_name = parse_model_id(base_model_id)
    return base_model_id, new_provider, new_name


def patched_bedrock_api_call(original_func, instance, args, kwargs, function_vars):
    params = function_vars.get("params")
    pin = function_vars.get("pin")
    integration = function_vars.get("integration")
    model_id = params.get("modelId")
    model_provider, model_name = parse_model_id(model_id)
    model_id, model_provider, model_name = _resolve_application_inference_profile(
        model_id, model_provider, model_name, instance, llmobs_enabled=integration.llmobs_enabled
    )
    submit_to_llmobs = integration.llmobs_enabled and "embed" not in model_name
    with core.context_with_data(
        "botocore.patched_bedrock_api_call",
        pin=pin,
        span_name=function_vars.get("trace_operation"),
        service=schematize_service_name(
            "{}.{}".format(ext_service(pin, int_config=config.botocore), function_vars.get("endpoint_name"))
        ),
        resource=function_vars.get("operation"),
        span_type=SpanTypes.LLM if submit_to_llmobs else None,
        call_trace=True,
        bedrock_integration=integration,
        params=params,
        model_provider=model_provider,
        model_name=model_name,
        model_id=model_id,
        instance=instance,
    ) as ctx:
        try:
            handle_bedrock_request(ctx)
            result = original_func(*args, **kwargs)
            result = handle_bedrock_response(ctx, result)
            return result
        except Exception:
            core.dispatch("botocore.patched_bedrock_api_call.exception", (ctx, sys.exc_info()))
            raise
