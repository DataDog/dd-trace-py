import json
import sys
import time
from typing import Any
from typing import Dict
from typing import List

from ddtrace import Span
from ddtrace.vendor import wrapt
from ddtrace.contrib._trace_utils_llm import BaseLLMIntegration


class _BedrockIntegration(BaseLLMIntegration):
    _integration_name = "bedrock"

    def __init__(self, config, stats_url, site, api_key, app_key=None):
        super().__init__(config, stats_url, site, api_key, app_key=app_key)

    def generate_llm_record(self, span: Span, params: Dict[str, Any], formatted_response: Dict[str, Any]) -> None:
        """Generate payloads for the LLM Obs API from a completion."""
        now = time.time()
        formatted_params = _extract_request_params(params, span.get_tag("bedrock.request.model_provider"))
        for i in range(len(formatted_response["text"])):
            attrs_dict = {
                "type": "completion",
                "id": span.get_tag("bedrock.response.id"),
                "timestamp": int(span.start * 1000),
                "model": span.get_tag("bedrock.request.model"),
                # "model_provider": span.get_tag("bedrock.request.model_provider"),
                "model_provider": "custom",
                "input": {
                    "prompts": [formatted_params.get("prompt")],
                    "temperature": formatted_params.get("temperature"),
                    "max_tokens": formatted_params.get("max_tokens"),
                },
                "output": {
                    "completions": [{"content": formatted_response["text"][i]}],
                    "durations": [now - span.start],
                },
            }
            self.llm_record(span, attrs_dict)


class TracedBotocoreStreamingBody(wrapt.ObjectProxy):
    # TODO: Need to figure out if we can somehow cause the span to timeout
    #  if the user never fully consumes the stream body.
    #  Currently the span finishes only if 1) the user fully consumes the stream body and 2) error during reading

    def __init__(self, wrapped, params, span, integration):
        super().__init__(wrapped)
        self._datadog_span = span
        self._datadog_integration = integration
        self._params = params
        self._body = bytearray()

    def read(self, amt=None):
        try:
            body = self.__wrapped__.read(amt=amt)
            self._body += body
            if self.__wrapped__.tell() == int(self.__wrapped__._content_length):
                self._process_streamed_response()
                self._datadog_span.finish()
            return body
        except Exception:
            self._datadog_span.set_exc_info(*sys.exc_info())
            self._datadog_span.finish()
            # TODO: set error metric, duration
            raise

    def readlines(self):
        try:
            lines = self.__wrapped__.readlines()
            for line in lines:
                self._body += line
            self._process_streamed_response()
            self._datadog_span.finish()
            return lines
        except Exception:
            self._datadog_span.set_exc_info(*sys.exc_info())
            self._datadog_span.finish()
            # TODO: set error metric, duration
            raise

    def _process_streamed_response(self) -> None:
        """
        Extracts the text and finish_reason from the streamed response and sets them as tags on the span.
        Also generates a llm record if sampled.
        """
        # TODO: handle error here unloading incomplete response?
        response = json.loads(self._body)
        formatted_response = _extract_response(self._datadog_span, response)
        for i in range(len(formatted_response["text"])):
            if self._datadog_integration.is_pc_sampled_span(self._datadog_span):
                self._datadog_span.set_tag_str(
                    "bedrock.response.choices.%d.text" % i,
                    self._datadog_integration.trunc(str(formatted_response["text"][i]))
                )
            self._datadog_span.set_tag_str(
                "bedrock.response.choices.%d.finish_reason" % i, str(formatted_response["finish_reason"][i])
            )
        if self._datadog_integration.is_pc_sampled_llmobs(self._datadog_span):
            self._datadog_integration.generate_llm_record(self._datadog_span, self._params, formatted_response)


def _extract_request_params(params: Dict[str, Any], provider: str) -> Dict[str, Any]:
    request_body = json.loads(params.get("body"))
    if provider == "ai21":
        return {
            "prompt": request_body.get("prompt"),
            "temperature": request_body.get("temperature", None),
            "top_p": request_body.get("topP", None),
            "max_tokens": request_body.get("maxTokens", None),
            "stop_sequences": request_body.get("stopSequences", []),
        }
    elif provider == "amazon":
        # FIXME: figure out differentiation and extraction for image-based models
        text_generation_config = request_body.get("textGenerationConfig", {})
        return {
            "prompt": request_body.get("inputText"),
            "temperature": text_generation_config.get("temperature", None),
            "top_p": text_generation_config.get("topP", None),
            "max_tokens": text_generation_config.get("maxTokenCount", None),
            "stop_sequences": text_generation_config.get("stopSequences", []),
        }
    elif provider == "anthropic":
        return {
            "prompt": request_body.get("prompt"),
            "temperature": request_body.get("temperature", None),
            "top_p": request_body.get("top_p", None),
            "top_k": request_body.get("top_k", None),
            "max_tokens": request_body.get("max_tokens_to_sample", None),
            "stop_sequences": request_body.get("stop_sequences", []),
        }
    elif provider == "cohere":
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
    elif provider == "meta":
        return {
            "prompt": request_body.get("prompt"),
            "temperature": request_body.get("temperature", None),
            "top_p": request_body.get("top_p", None),
            "max_tokens": request_body.get("max_gen_len", None),
        }
    elif provider == "stability":
        # FIXME: figure out extraction for image-based models
        return {}
    return {}


def _extract_response(span: Span, response: Dict[str, Any]) -> Dict[str, List[str]]:
    text, finish_reason = None, None
    provider = span.get_tag("bedrock.request.model_provider")
    if provider == "ai21":
        text = response.get("completions")[0].get("data").get("text")
        finish_reason = response.get("completions")[0].get("finishReason")
    elif provider == "amazon":
        text = response.get("results")[0].get("outputText")
        finish_reason = response.get("results")[0].get("completionReason")
    elif provider == "anthropic":
        text = response.get("completion")
        finish_reason = response.get("stop_reason")
    elif provider == "cohere":
        text = [generation["text"] for generation in response.get("generations")]
        finish_reason = [generation["finish_reason"] for generation in response.get("generations")]
        for i in range(len(text)):
            span.set_tag_str("bedrock.response.choices.%d.id" % i, str(response.get("generations")[i]["id"]))
    elif provider == "meta":
        text = response.get("generation")
        finish_reason = response.get("stop_reason")
    elif provider == "stability":
        # TODO: figure out extraction for image-based models
        pass

    if not isinstance(text, list):
        text = [text]
        finish_reason = [finish_reason]

    return {"text": text, "finish_reason": finish_reason}


def _extract_response_metadata(result: Dict[str, Any]) -> Dict[str, Any]:
    metadata = result["ResponseMetadata"]
    return {
        "id": metadata["RequestId"],
        "duration": metadata["HTTPHeaders"]["x-amzn-bedrock-invocation-latency"],
        "usage.prompt_tokens": metadata["HTTPHeaders"]["x-amzn-bedrock-input-token-count"],
        "usage.completion_tokens": metadata["HTTPHeaders"]["x-amzn-bedrock-output-token-count"],
    }


def handle_bedrock_request(span: Span, integration: _BedrockIntegration, params: Dict[str, Any]) -> None:
    model_provider, model_name = params.get("modelId").split(".")
    request_params = _extract_request_params(params, model_provider)

    span.set_tag_str("bedrock.request.model_provider", model_provider)
    span.set_tag_str("bedrock.request.model", model_name)
    for k, v in request_params.items():
        if k == "prompt" and integration.is_pc_sampled_span(span):
            v = integration.trunc(str(v))
        span.set_tag_str("bedrock.request.{}".format(k), str(v))


def handle_bedrock_response(
        span: Span, integration: _BedrockIntegration, params: Dict[str, Any], result: Dict[str, Any]
) -> Dict[str, Any]:
    metadata = _extract_response_metadata(result)
    for k, v in metadata.items():
        span.set_tag_str("bedrock.response.%s" % k, str(v))
    # Wrap the StreamingResponse in a traced object so that we can tag response data as the user consumes it.
    body = result["body"]
    result["body"] = TracedBotocoreStreamingBody(body, params, span, integration)
    return result
