import json
from typing import Any
from typing import Dict
from typing import Optional

from ddtrace import Span
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._utils import _unserializable_default_repr
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.internal.utils import get_argument_value


class GeminiIntegration(BaseLLMIntegration):
    _integration_name = "gemini"

    def _set_base_span_tags(
            self, span: Span, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        if provider:
            span.set_tag_str("genai.request.model", model)
        if model:
            span.set_tag_str("genai.request.provider", provider)

    def llmobs_set_tags(self, span: Span, args: Any, kwargs: Any, instance: Any, generations: Any = None) -> None:
        if not self.llmobs_enabled:
            return

        span.set_tag_str(SPAN_KIND, "llm")
        span.set_tag_str(MODEL_NAME, span.get_tag("genai.request.model") or "")
        span.set_tag_str(MODEL_PROVIDER, span.get_tag("genai.request.provider") or "")

        metadata = self._llmobs_set_metadata(kwargs, instance)
        span.set_tag_str(METADATA, json.dumps(metadata, default=_unserializable_default_repr))

        system_instruction = _get_attr(instance, "_system_instruction", None)
        input_contents = get_argument_value(args, kwargs, 0, "contents")
        input_messages = self._extract_input_message(input_contents, system_instruction)
        span.set_tag_str(INPUT_MESSAGES, json.dumps(input_messages, default=_unserializable_default_repr))

        if span.error or generations is None:
            span.set_tag_str(OUTPUT_MESSAGES, json.dumps([{"content": ""}]))
        else:
            output_messages = self._extract_output_message(generations)
            span.set_tag_str(OUTPUT_MESSAGES, json.dumps(output_messages, default=_unserializable_default_repr))

        usage = self._get_llmobs_metrics_tags(span)
        if usage:
            span.set_tag_str(METRICS, json.dumps(usage, default=_unserializable_default_repr))

    @staticmethod
    def _llmobs_set_metadata(kwargs, instance):
        metadata = {}
        breakpoint()
        model_config = instance._generation_config or {}
        request_config = kwargs.get("generation_config", {})
        parameters = ("temperature", "max_output_tokens", "candidate_count", "top_p", "top_k")
        for param in parameters:
            model_config_value = _get_attr(model_config, param, None)
            request_config_value = _get_attr(request_config, param, None)
            if model_config_value or request_config_value:
                metadata[param] = request_config_value or model_config_value
        return metadata

    @staticmethod
    def _extract_input_message(contents, system_instruction=None):
        messages = []
        if system_instruction:
            for idx, part in enumerate(system_instruction.parts):
                messages.append({"content": part.text or "", "role": "system"})
        if isinstance(contents, str):
            messages.append({"content": contents, "role": "user"})
            return messages
        elif isinstance(contents, dict):
            messages.append({"content": contents.get("text", ""), "role": contents.get("role", "user")})
            return messages
        elif not isinstance(contents, list):
            return messages
        for content_idx, content in enumerate(contents):
            if isinstance(content, str):
                messages.append({"content": content, "role": "user"})
                continue
            role = _get_attr(content, "role", "user")
            parts = _get_attr(content, "parts", [])
            if not parts:
                messages.append({"content": "[Non-text content object: {}]".format(repr(content)), "role": role})
            for part in parts:
                text = _get_attr(part, "text", "")
                function_call = _get_attr(part, "function_call", None)
                function_response = _get_attr(part, "function_response", None)
                message = {"content": text, "role": role}
                if function_call:
                    function_call_dict = type(function_call).to_dict(function_call)
                    message["tool_calls"] = [
                        {"name": function_call_dict.get("name", ""), "arguments": function_call_dict.get("args", {})}
                    ]
                if function_response:
                    function_response_dict = type(function_response).to_dict(function_response)
                    message["content"] = "[tool result: {}]".format(function_response_dict.get("response", ""))
                messages.append(message)
        return messages

    @staticmethod
    def _extract_output_message(generations):
        output_messages = []
        generations_dict = generations.to_dict()
        for idx, candidate in enumerate(generations_dict.get("candidates", [])):
            content = candidate.get("content", {})
            role = content.get("role", "model")
            parts = content.get("parts", [])
            for part_idx, part in enumerate(parts):
                text = part.get("text", "")
                function_call = part.get("function_call", None)
                if not function_call:
                    output_messages.append({"content": text, "role": role})
                    continue
                function_name = function_call.get("name", "")
                function_args = function_call.get("args", {})
                output_messages.append(
                    {"content": text, "role": role, "tool_calls": [{"name": function_name, "arguments": function_args}]}
                )
        return output_messages

    @staticmethod
    def _get_llmobs_metrics_tags(span):
        usage = {}
        input_tokens = span.get_metric("genai.response.usage.prompt_tokens")
        output_tokens = span.get_metric("genai.response.usage.completion_tokens")
        total_tokens = span.get_metric("genai.response.usage.total_tokens")

        if input_tokens is not None:
            usage[INPUT_TOKENS_METRIC_KEY] = input_tokens
        if output_tokens is not None:
            usage[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
        if total_tokens is not None:
            usage[TOTAL_TOKENS_METRIC_KEY] = total_tokens
        return usage


def _get_attr(o: Any, attr: str, default: Any):
    # Convenience method to get an attribute from an object or dict
    if isinstance(o, dict):
        return o.get(attr, default)
    else:
        return getattr(o, attr, default)
