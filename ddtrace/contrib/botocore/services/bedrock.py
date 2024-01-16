import json
import sys
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace import Span
from ddtrace.internal.llmobs.integrations import BedrockIntegration
from ddtrace.internal.logger import get_logger
from ddtrace.vendor import wrapt

from ....internal.schema import schematize_service_name


log = get_logger(__name__)


_AI21 = "ai21"
_AMAZON = "amazon"
_ANTHROPIC = "anthropic"
_COHERE = "cohere"
_META = "meta"
_STABILITY = "stability"


class TracedBotocoreStreamingBody(wrapt.ObjectProxy):
    """
    This class wraps the StreamingBody object returned by botocore api calls, specifically for Bedrock invocations.
    Since the response body is in the form of a stream object, we need to wrap it in order to tag the response data
    and finish the span as the user consumes the streamed response.
    Currently, the corresponding span finishes only if:
     1) the user fully consumes the stream body
     2) error during reading
    This means that if the stream is not consumed, there is a small risk of memory leak due to unfinished spans.
    """

    def __init__(self, wrapped, span, integration, prompt=None):
        """
        The TracedBotocoreStreamingBody wrapper stores a reference to the
        underlying Span object, BedrockIntegration object, and the response body that will saved and tagged.
        """
        super().__init__(wrapped)
        self._datadog_span = span
        self._datadog_integration = integration
        self._body = []
        self._prompt = prompt

    def read(self, amt=None):
        """Wraps around method to tags the response data and finish the span as the user consumes the stream."""
        try:
            body = self.__wrapped__.read(amt=amt)
            self._body.append(json.loads(body))
            if self.__wrapped__.tell() == int(self.__wrapped__._content_length):
                formatted_response = _extract_response(self._datadog_span, self._body[0])
                self._process_response(formatted_response)
                self._datadog_span.finish()
            return body
        except Exception:
            _handle_exception(self._datadog_span, self._datadog_integration, self._prompt, sys.exc_info())
            raise

    def readlines(self):
        """Wraps around method to tags the response data and finish the span as the user consumes the stream."""
        try:
            lines = self.__wrapped__.readlines()
            for line in lines:
                self._body.append(json.loads(line))
            formatted_response = _extract_response(self._datadog_span, self._body[0])
            self._process_response(formatted_response)
            self._datadog_span.finish()
            return lines
        except Exception:
            _handle_exception(self._datadog_span, self._datadog_integration, self._prompt, sys.exc_info())
            raise

    def __iter__(self):
        """Wraps around method to tags the response data and finish the span as the user consumes the stream."""
        try:
            for line in self.__wrapped__:
                self._body.append(json.loads(line["chunk"]["bytes"]))
                yield line
            metadata = _extract_streamed_response_metadata(self._datadog_span, self._body)
            formatted_response = _extract_streamed_response(self._datadog_span, self._body)
            self._process_response(formatted_response, metadata=metadata)
            self._datadog_span.finish()
        except Exception:
            _handle_exception(self._datadog_span, self._datadog_integration, self._prompt, sys.exc_info())
            raise

    def _process_response(self, formatted_response: Dict[str, Any], metadata: Dict[str, Any] = None) -> None:
        """
        Sets the response tags on the span given the formatted response body and any metadata.
        Also generates an LLM record if enabled.
        """
        if metadata is not None:
            for k, v in metadata.items():
                self._datadog_span.set_tag_str("bedrock.{}".format(k), str(v))
        for i in range(len(formatted_response["text"])):
            if self._datadog_integration.is_pc_sampled_span(self._datadog_span):
                self._datadog_span.set_tag_str(
                    "bedrock.response.choices.{}.text".format(i),
                    self._datadog_integration.trunc(str(formatted_response["text"][i])),
                )
            self._datadog_span.set_tag_str(
                "bedrock.response.choices.{}.finish_reason".format(i), str(formatted_response["finish_reason"][i])
            )
        if self._datadog_integration.is_pc_sampled_llmobs(self._datadog_span):
            self._datadog_integration.generate_llm_record(
                self._datadog_span, formatted_response=formatted_response, prompt=self._prompt
            )


def _handle_exception(span, integration, prompt, exc_info):
    """Helper method to finish the span on stream read error."""
    span.set_exc_info(*exc_info)
    span.finish()
    if integration.is_pc_sampled_llmobs(span):
        integration.generate_llm_record(span, formatted_response=None, prompt=prompt, err=1)


def _extract_request_params(params: Dict[str, Any], provider: str) -> Dict[str, Any]:
    """
    Extracts request parameters including prompt, temperature, top_p, max_tokens, and stop_sequences.
    """
    request_body = json.loads(params.get("body"))
    if provider == _AI21:
        return {
            "prompt": request_body.get("prompt"),
            "temperature": request_body.get("temperature", None),
            "top_p": request_body.get("topP", None),
            "max_tokens": request_body.get("maxTokens", None),
            "stop_sequences": request_body.get("stopSequences", []),
        }
    elif provider == _AMAZON:
        text_generation_config = request_body.get("textGenerationConfig", {})
        return {
            "prompt": request_body.get("inputText"),
            "temperature": text_generation_config.get("temperature", None),
            "top_p": text_generation_config.get("topP", None),
            "max_tokens": text_generation_config.get("maxTokenCount", None),
            "stop_sequences": text_generation_config.get("stopSequences", []),
        }
    elif provider == _ANTHROPIC:
        return {
            "prompt": request_body.get("prompt"),
            "temperature": request_body.get("temperature", None),
            "top_p": request_body.get("top_p", None),
            "top_k": request_body.get("top_k", None),
            "max_tokens": request_body.get("max_tokens_to_sample", None),
            "stop_sequences": request_body.get("stop_sequences", []),
        }
    elif provider == _COHERE:
        return {
            "prompt": request_body.get("prompt"),
            "temperature": request_body.get("temperature", None),
            "top_p": request_body.get("p", None),
            "top_k": request_body.get("k", None),
            "max_tokens": request_body.get("max_tokens", None),
            "stop_sequences": request_body.get("stop_sequences", []),
            "stream": request_body.get("stream", None),
            "n": request_body.get("num_generations", None),
        }
    elif provider == _META:
        return {
            "prompt": request_body.get("prompt"),
            "temperature": request_body.get("temperature", None),
            "top_p": request_body.get("top_p", None),
            "max_tokens": request_body.get("max_gen_len", None),
        }
    elif provider == _STABILITY:
        # TODO: request/response formats are different for image-based models. Defer for now
        return {}
    return {}


def _extract_response(span: Span, body: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Extracts text and finish_reason from the response body, which has different formats for different providers.
    """
    text, finish_reason = "", ""
    provider = span.get_tag("bedrock.request.model_provider")
    try:
        if provider == _AI21:
            text = body.get("completions")[0].get("data").get("text")
            finish_reason = body.get("completions")[0].get("finishReason")
        elif provider == _AMAZON:
            text = body.get("results")[0].get("outputText")
            finish_reason = body.get("results")[0].get("completionReason")
        elif provider == _ANTHROPIC:
            text = body.get("completion")
            finish_reason = body.get("stop_reason")
        elif provider == _COHERE:
            text = [generation["text"] for generation in body.get("generations")]
            finish_reason = [generation["finish_reason"] for generation in body.get("generations")]
            for i in range(len(text)):
                span.set_tag_str("bedrock.response.choices.{}.id".format(i), str(body.get("generations")[i]["id"]))
        elif provider == _META:
            text = body.get("generation")
            finish_reason = body.get("stop_reason")
        elif provider == _STABILITY:
            # TODO: request/response formats are different for image-based models. Defer for now
            pass
    except (IndexError, AttributeError):
        log.warning("Unable to extract text/finish_reason from response body. Defaulting to empty text/finish_reason.")

    if not isinstance(text, list):
        text = [text]
    if not isinstance(finish_reason, list):
        finish_reason = [finish_reason]

    return {"text": text, "finish_reason": finish_reason}


def _extract_streamed_response(span: Span, streamed_body: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """
    Extracts text,finish_reason from the streamed response body, which has different formats for different providers.
    """
    text, finish_reason = "", ""
    provider = span.get_tag("bedrock.request.model_provider")
    try:
        if provider == _AI21:
            pass  # note: ai21 does not support streamed responses
        elif provider == _AMAZON:
            text = "".join([chunk["outputText"] for chunk in streamed_body])
            finish_reason = streamed_body[-1]["completionReason"]
        elif provider == _ANTHROPIC:
            text = "".join([chunk["completion"] for chunk in streamed_body])
            finish_reason = streamed_body[-1]["stop_reason"]
        elif provider == _COHERE and streamed_body:
            if "is_finished" in streamed_body[0]:  # streamed response
                if "index" in streamed_body[0]:  # n >= 2
                    n = int(span.get_tag("bedrock.request.n"))
                    text = [
                        "".join([chunk["text"] for chunk in streamed_body[:-1] if chunk["index"] == i])
                        for i in range(n)
                    ]
                    finish_reason = [streamed_body[-1]["finish_reason"] for _ in range(n)]
                else:
                    text = "".join([chunk["text"] for chunk in streamed_body[:-1]])
                    finish_reason = streamed_body[-1]["finish_reason"]
            else:
                text = [chunk["text"] for chunk in streamed_body[0]["generations"]]
                finish_reason = [chunk["finish_reason"] for chunk in streamed_body[0]["generations"]]
                for i in range(len(text)):
                    span.set_tag_str(
                        "bedrock.response.choices.{}.id".format(i),
                        str(streamed_body[0]["generations"][i].get("id", None)),
                    )
        elif provider == _META:
            text = "".join([chunk["generation"] for chunk in streamed_body])
            finish_reason = streamed_body[-1]["stop_reason"]
        elif provider == _STABILITY:
            # TODO: figure out extraction for image-based models
            pass
    except (IndexError, AttributeError):
        log.warning("Unable to extract text/finish_reason from response body. Defaulting to empty text/finish_reason.")

    if not isinstance(text, list):
        text = [text]
    if not isinstance(finish_reason, list):
        finish_reason = [finish_reason]

    return {"text": text, "finish_reason": finish_reason}


def _extract_streamed_response_metadata(span: Span, streamed_body: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extracts metadata from the streamed response body."""
    provider = span.get_tag("bedrock.request.model_provider")
    metadata = {}
    if provider == _AI21:
        pass  # ai21 does not support streamed responses
    elif provider in [_AMAZON, _ANTHROPIC, _COHERE, _META] and streamed_body:
        metadata = streamed_body[-1].get("amazon-bedrock-invocationMetrics", {})
    elif provider == _STABILITY:
        # TODO: figure out extraction for image-based models
        pass
    return {
        "response.duration": metadata.get("invocationLatency", None),
        "usage.prompt_tokens": metadata.get("inputTokenCount", None),
        "usage.completion_tokens": metadata.get("outputTokenCount", None),
    }


def handle_bedrock_request(span: Span, integration: BedrockIntegration, params: Dict[str, Any]) -> None:
    """Perform request param extraction and tagging."""
    model_provider, model_name = params.get("modelId").split(".")
    request_params = _extract_request_params(params, model_provider)

    span.set_tag_str("bedrock.request.model_provider", model_provider)
    span.set_tag_str("bedrock.request.model", model_name)
    prompt = None
    for k, v in request_params.items():
        if k == "prompt" and integration.is_pc_sampled_span(span):
            v = integration.trunc(str(v))
        if k == "prompt" and integration.is_pc_sampled_llmobs(span):
            prompt = v
        span.set_tag_str("bedrock.request.{}".format(k), str(v))
    return prompt


def handle_bedrock_response(
    span: Span, integration: BedrockIntegration, result: Dict[str, Any], prompt: Optional[str] = None
) -> Dict[str, Any]:
    """Perform response param extraction and tagging."""
    metadata = result["ResponseMetadata"]
    http_headers = metadata["HTTPHeaders"]
    span.set_tag_str("bedrock.response.id", str(metadata.get("RequestId", "")))
    span.set_tag_str("bedrock.response.duration", str(http_headers.get("x-amzn-bedrock-invocation-latency", "")))
    span.set_tag_str("bedrock.usage.prompt_tokens", str(http_headers.get("x-amzn-bedrock-input-token-count", "")))
    span.set_tag_str("bedrock.usage.completion_tokens", str(http_headers.get("x-amzn-bedrock-output-token-count", "")))

    # Wrap the StreamingResponse in a traced object so that we can tag response data as the user consumes it.
    body = result["body"]
    result["body"] = TracedBotocoreStreamingBody(body, span, integration, prompt=prompt)
    return result


def patched_bedrock_api_call(original_func, instance, args, kwargs, function_vars):
    params = function_vars.get("params")
    trace_operation = function_vars.get("trace_operation")
    operation = function_vars.get("operation")
    pin = function_vars.get("pin")
    endpoint_name = function_vars.get("endpoint_name")
    integration = function_vars.get("integration")
    # This span will be finished separately as the user fully consumes the stream body, or on error.
    bedrock_span = pin.tracer.start_span(
        trace_operation,
        service=schematize_service_name("{}.{}".format(pin.service, endpoint_name)),
        resource=operation,
        activate=False,
    )
    prompt = None
    try:
        prompt = handle_bedrock_request(bedrock_span, integration, params)
        result = original_func(*args, **kwargs)
        result = handle_bedrock_response(bedrock_span, integration, result, prompt=prompt)
        return result
    except Exception:
        _handle_exception(bedrock_span, integration, prompt, sys.exc_info())
        raise
