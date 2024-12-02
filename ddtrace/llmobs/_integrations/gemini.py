from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional

from ddtrace import Span
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import safe_json


class GeminiIntegration(BaseLLMIntegration):
    _integration_name = "gemini"

    def _set_base_span_tags(
        self, span: Span, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        if provider is not None:
            span.set_tag_str("google_generativeai.request.provider", str(provider))
        if model is not None:
            span.set_tag_str("google_generativeai.request.model", str(model))

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        span.set_tag_str(SPAN_KIND, "llm")
        span.set_tag_str(MODEL_NAME, span.get_tag("google_generativeai.request.model") or "")
        span.set_tag_str(MODEL_PROVIDER, span.get_tag("google_generativeai.request.provider") or "")

        instance = kwargs.get("instance", None)
        metadata = self._llmobs_set_metadata(kwargs, instance)
        span.set_tag_str(METADATA, safe_json(metadata))

        system_instruction = _get_attr(instance, "_system_instruction", None)
        input_contents = get_argument_value(args, kwargs, 0, "contents")
        input_messages = self._extract_input_message(input_contents, system_instruction)
        span.set_tag_str(INPUT_MESSAGES, safe_json(input_messages))

        if span.error or response is None:
            span.set_tag_str(OUTPUT_MESSAGES, safe_json([{"content": ""}]))
        else:
            output_messages = self._extract_output_message(response)
            span.set_tag_str(OUTPUT_MESSAGES, safe_json(output_messages))

        usage = self._get_llmobs_metrics_tags(span)
        if usage:
            span.set_tag_str(METRICS, safe_json(usage))

    @staticmethod
    def _llmobs_set_metadata(kwargs, instance):
        metadata = {}
        model_config = _get_attr(instance, "_generation_config", {})
        request_config = kwargs.get("generation_config", {})
        parameters = ("temperature", "max_output_tokens", "candidate_count", "top_p", "top_k")
        for param in parameters:
            model_config_value = _get_attr(model_config, param, None)
            request_config_value = _get_attr(request_config, param, None)
            if model_config_value or request_config_value:
                metadata[param] = request_config_value or model_config_value
        return metadata

    @staticmethod
    def _extract_message_from_part(part, role):
        text = _get_attr(part, "text", "")
        function_call = _get_attr(part, "function_call", None)
        function_response = _get_attr(part, "function_response", None)
        message = {"content": text}
        if role:
            message["role"] = role
        if function_call:
            function_call_dict = function_call
            if not isinstance(function_call, dict):
                function_call_dict = type(function_call).to_dict(function_call)
            message["tool_calls"] = [
                {"name": function_call_dict.get("name", ""), "arguments": function_call_dict.get("args", {})}
            ]
        if function_response:
            function_response_dict = function_response
            if not isinstance(function_response, dict):
                function_response_dict = type(function_response).to_dict(function_response)
            message["content"] = "[tool result: {}]".format(function_response_dict.get("response", ""))
        return message

    def _extract_input_message(self, contents, system_instruction=None):
        messages = []
        if system_instruction:
            for part in system_instruction.parts:
                messages.append({"content": part.text or "", "role": "system"})
        if isinstance(contents, str):
            messages.append({"content": contents})
            return messages
        if isinstance(contents, dict):
            message = {"content": contents.get("text", "")}
            if contents.get("role", None):
                message["role"] = contents["role"]
            messages.append(message)
            return messages
        if not isinstance(contents, list):
            messages.append({"content": "[Non-text content object: {}]".format(repr(contents))})
            return messages
        for content in contents:
            if isinstance(content, str):
                messages.append({"content": content})
                continue
            role = _get_attr(content, "role", None)
            parts = _get_attr(content, "parts", [])
            if not parts or not isinstance(parts, Iterable):
                message = {"content": "[Non-text content object: {}]".format(repr(content))}
                if role:
                    message["role"] = role
                messages.append(message)
                continue
            for part in parts:
                message = self._extract_message_from_part(part, role)
                messages.append(message)
        return messages

    def _extract_output_message(self, generations):
        output_messages = []
        generations_dict = generations.to_dict()
        for candidate in generations_dict.get("candidates", []):
            content = candidate.get("content", {})
            role = content.get("role", "model")
            parts = content.get("parts", [])
            for part in parts:
                message = self._extract_message_from_part(part, role)
                output_messages.append(message)
        return output_messages

    @staticmethod
    def _get_llmobs_metrics_tags(span):
        usage = {}
        input_tokens = span.get_metric("google_generativeai.response.usage.prompt_tokens")
        output_tokens = span.get_metric("google_generativeai.response.usage.completion_tokens")
        total_tokens = span.get_metric("google_generativeai.response.usage.total_tokens")

        if input_tokens is not None:
            usage[INPUT_TOKENS_METRIC_KEY] = input_tokens
        if output_tokens is not None:
            usage[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
        if total_tokens is not None:
            usage[TOTAL_TOKENS_METRIC_KEY] = total_tokens
        return usage
