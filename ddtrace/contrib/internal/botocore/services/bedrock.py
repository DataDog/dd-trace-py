import json
import sys
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import wrapt

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name


log = get_logger(__name__)


_AI21 = "ai21"
_AMAZON = "amazon"
_ANTHROPIC = "anthropic"
_COHERE = "cohere"
_META = "meta"
_STABILITY = "stability"

_MODEL_TYPE_IDENTIFIERS = (
    "foundation-model/",
    "custom-model/",
    "provisioned-model/",
    "imported-model/",
    "prompt/",
    "endpoint/",
    "inference-profile/",
    "default-prompt-router/",
)


class TracedBotocoreStreamingBody(wrapt.ObjectProxy):
    """
    This class wraps the StreamingBody object returned by botocore api calls, specifically for Bedrock invocations.
    Since the response body is in the form of a stream object, we need to wrap it in order to tag the response data
    and fire completion events as the user consumes the streamed response.
    """

    def __init__(self, wrapped, ctx: core.ExecutionContext):
        super().__init__(wrapped)
        self._body = []
        self._execution_ctx = ctx

    def read(self, amt=None):
        """Wraps around method to tags the response data and finish the span as the user consumes the stream."""
        try:
            body = self.__wrapped__.read(amt=amt)
            self._body.append(json.loads(body))
            if self.__wrapped__.tell() == int(self.__wrapped__._content_length):
                formatted_response = _extract_text_and_response_reason(self._execution_ctx, self._body[0])
                model_provider = self._execution_ctx["model_provider"]
                model_name = self._execution_ctx["model_name"]
                should_set_choice_ids = model_provider == _COHERE and "embed" not in model_name
                core.dispatch(
                    "botocore.bedrock.process_response",
                    [self._execution_ctx, formatted_response, None, self._body[0], should_set_choice_ids],
                )
            return body
        except Exception:
            core.dispatch("botocore.patched_bedrock_api_call.exception", [self._execution_ctx, sys.exc_info()])
            raise

    def readlines(self):
        """Wraps around method to tags the response data and finish the span as the user consumes the stream."""
        try:
            lines = self.__wrapped__.readlines()
            for line in lines:
                self._body.append(json.loads(line))
            formatted_response = _extract_text_and_response_reason(self._execution_ctx, self._body[0])
            model_provider = self._execution_ctx["model_provider"]
            model_name = self._execution_ctx["model_name"]
            should_set_choice_ids = model_provider == _COHERE and "embed" not in model_name
            core.dispatch(
                "botocore.bedrock.process_response",
                [self._execution_ctx, formatted_response, None, self._body[0], should_set_choice_ids],
            )
            return lines
        except Exception:
            core.dispatch("botocore.patched_bedrock_api_call.exception", [self._execution_ctx, sys.exc_info()])
            raise

    def __iter__(self):
        """Wraps around method to tags the response data and finish the span as the user consumes the stream."""
        exception_raised = False
        try:
            for line in self.__wrapped__:
                self._body.append(json.loads(line["chunk"]["bytes"]))
                yield line
        except Exception:
            core.dispatch("botocore.patched_bedrock_api_call.exception", [self._execution_ctx, sys.exc_info()])
            exception_raised = True
            raise
        finally:
            if exception_raised:
                return
            metadata = _extract_streamed_response_metadata(self._execution_ctx, self._body)
            formatted_response = _extract_streamed_response(self._execution_ctx, self._body)
            model_provider = self._execution_ctx["model_provider"]
            model_name = self._execution_ctx["model_name"]
            should_set_choice_ids = (
                model_provider == _COHERE and "is_finished" not in self._body[0] and "embed" not in model_name
            )
            core.dispatch(
                "botocore.bedrock.process_response",
                [self._execution_ctx, formatted_response, metadata, self._body, should_set_choice_ids],
            )


class TracedBotocoreConverseStream(wrapt.ObjectProxy):
    """
    This class wraps the stream response returned by converse_stream.
    """

    def __init__(self, wrapped, ctx: core.ExecutionContext):
        super().__init__(wrapped)
        self._stream_chunks = []
        self._execution_ctx = ctx

    def __iter__(self):
        exception_raised = False
        try:
            for chunk in self.__wrapped__:
                self._stream_chunks.append(chunk)
                yield chunk
        except Exception:
            core.dispatch("botocore.patched_bedrock_api_call.exception", [self._execution_ctx, sys.exc_info()])
            exception_raised = True
            raise
        finally:
            if exception_raised:
                return
            core.dispatch("botocore.bedrock.process_response_converse", [self._execution_ctx, self._stream_chunks])


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
    if llmobs_usage:
        ctx.set_item("llmobs.usage", llmobs_usage)


def _extract_request_params_for_converse(params: Dict[str, Any]) -> Dict[str, Any]:
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
    return {
        "prompt": prompt,
        "temperature": inference_config.get("temperature", ""),
        "top_p": inference_config.get("topP", ""),
        "max_tokens": inference_config.get("maxTokens", ""),
        "stop_sequences": inference_config.get("stopSequences", []),
    }


def _extract_request_params_for_invoke(params: Dict[str, Any], provider: str) -> Dict[str, Any]:
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


def _extract_text_and_response_reason(ctx: core.ExecutionContext, body: Dict[str, Any]) -> Dict[str, List[str]]:
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


def _extract_streamed_response(ctx: core.ExecutionContext, streamed_body: List[Dict[str, Any]]) -> Dict[str, List[str]]:
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
                    num_generations = int(ctx.get_item("num_generations") or 0)
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
    ctx: core.ExecutionContext, streamed_body: List[Dict[str, Any]]
) -> Dict[str, Any]:
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

    _set_llmobs_usage(ctx, safe_token_count(input_tokens), safe_token_count(output_tokens))
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
    core.dispatch("botocore.patched_bedrock_api_call.started", [ctx, request_params])
    if ctx["bedrock_integration"].llmobs_enabled:
        ctx.set_item("llmobs.request_params", request_params)


def handle_bedrock_response(
    ctx: core.ExecutionContext,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    metadata = result["ResponseMetadata"]
    http_headers = metadata["HTTPHeaders"]

    total_tokens = None
    input_tokens = http_headers.get("x-amzn-bedrock-input-token-count", "")
    output_tokens = http_headers.get("x-amzn-bedrock-output-token-count", "")
    request_latency = str(http_headers.get("x-amzn-bedrock-invocation-latency", ""))

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
        if "stopReason" in result:
            ctx.set_item("llmobs.stop_reason", result.get("stopReason"))

    _set_llmobs_usage(
        ctx, safe_token_count(input_tokens), safe_token_count(output_tokens), safe_token_count(total_tokens)
    )

    # for both converse & invoke, dispatch success event to store basic metrics
    core.dispatch(
        "botocore.patched_bedrock_api_call.success",
        [
            ctx,
            str(metadata.get("RequestId", "")),
            request_latency,
            str(input_tokens),
            str(output_tokens),
        ],
    )

    if ctx["resource"] == "Converse":
        core.dispatch("botocore.bedrock.process_response_converse", [ctx, result])
        return result
    if ctx["resource"] == "ConverseStream":
        if "stream" in result:
            result["stream"] = TracedBotocoreConverseStream(result["stream"], ctx)
        return result

    body = result["body"]
    result["body"] = TracedBotocoreStreamingBody(body, ctx)
    return result


def _parse_model_id(model_id: str):
    """Best effort to extract the model provider and model name from the bedrock model ID.
    model_id can be a 1/2 period-separated string or a full AWS ARN, based on the following formats:
    1. Base model: "{model_provider}.{model_name}"
    2. Cross-region model: "{region}.{model_provider}.{model_name}"
    3. Other: Prefixed by AWS ARN "arn:aws{+region?}:bedrock:{region}:{account-id}:"
        a. Foundation model: ARN prefix + "foundation-model/{region?}.{model_provider}.{model_name}"
        b. Custom model: ARN prefix + "custom-model/{model_provider}.{model_name}"
        c. Provisioned model: ARN prefix + "provisioned-model/{model-id}"
        d. Imported model: ARN prefix + "imported-module/{model-id}"
        e. Prompt management: ARN prefix + "prompt/{prompt-id}"
        f. Sagemaker: ARN prefix + "endpoint/{model-id}"
        g. Inference profile: ARN prefix + "{application-?}inference-profile/{model-id}"
        h. Default prompt router: ARN prefix + "default-prompt-router/{prompt-id}"
    If model provider cannot be inferred from the model_id formatting, then default to "custom"
    """
    if not model_id.startswith("arn:aws"):
        model_meta = model_id.split(".")
        if len(model_meta) < 2:
            return "custom", model_meta[0]
        return model_meta[-2], model_meta[-1]
    for identifier in _MODEL_TYPE_IDENTIFIERS:
        if identifier not in model_id:
            continue
        model_id = model_id.rsplit(identifier, 1)[-1]
        if identifier in ("foundation-model/", "custom-model/"):
            model_meta = model_id.split(".")
            if len(model_meta) < 2:
                return "custom", model_id
            return model_meta[-2], model_meta[-1]
        return "custom", model_id
    return "custom", "custom"


def patched_bedrock_api_call(original_func, instance, args, kwargs, function_vars):
    params = function_vars.get("params")
    pin = function_vars.get("pin")
    model_id = params.get("modelId")
    model_provider, model_name = _parse_model_id(model_id)
    integration = function_vars.get("integration")
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
        instance=instance,
    ) as ctx:
        try:
            handle_bedrock_request(ctx)
            result = original_func(*args, **kwargs)
            result = handle_bedrock_response(ctx, result)
            return result
        except Exception:
            core.dispatch("botocore.patched_bedrock_api_call.exception", [ctx, sys.exc_info()])
            raise
