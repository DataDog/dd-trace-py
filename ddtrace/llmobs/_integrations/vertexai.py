import json
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
import vertexai
from vertexai.generative_models import Part


class VertexAIIntegration(BaseLLMIntegration):
    _integration_name = "vertexai"

    def _set_base_span_tags(
        self, span: Span, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        if provider is not None:
            span.set_tag_str("vertexai.request.provider", provider)
        if model is not None:
            span.set_tag_str("vertexai.request.model", model)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
        history: List[Any] = [],
    ) -> None:
        span.set_tag_str(SPAN_KIND, "llm")
        span.set_tag_str(MODEL_NAME, span.get_tag("vertexai.request.model") or "")
        span.set_tag_str(MODEL_PROVIDER, span.get_tag("vertexai.request.provider") or "")

        instance = kwargs.get("instance", None)
        metadata = self._llmobs_set_metadata(kwargs, instance)
        span.set_tag_str(METADATA, safe_json(metadata))
        system_instruction = self._extract_system_instructions(instance)
        # TODO: figure out how to get history from the instance
        input_contents = get_argument_value(args, kwargs, 0, "contents")
        input_messages = self._extract_input_message(input_contents, history, system_instruction)
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
    def _extract_system_instructions(instance):
        """
        Extract system instructions from model, convert to []str, ad return.
        """
        # extract model instance from chat session
        if hasattr(instance, "_model"):
            instance = instance._model
        raw_system_instructions = getattr(instance, "_system_instruction", [])
        if isinstance(raw_system_instructions, str):
            return [raw_system_instructions]
        elif isinstance(raw_system_instructions, Part):
            return [raw_system_instructions.text]
        elif not isinstance(raw_system_instructions, list):
            return []

        system_instructions = []
        for elem in raw_system_instructions:
            if isinstance(elem, str):
                system_instructions.append(elem)
            elif isinstance(elem, Part):
                system_instructions.append(elem.text)
        return system_instructions
    
    @staticmethod
    def _llmobs_set_metadata(kwargs, instance):
        metadata = {}
        model_config = _get_attr(instance, "_generation_config", {})
        request_config = kwargs.get("generation_config", {})
        request_config = request_config if isinstance(request_config, dict) else request_config.to_dict()
        parameters = ("temperature", "max_output_tokens", "candidate_count", "top_p", "top_k")
        for param in parameters:
            model_config_value = _get_attr(model_config, param, None)
            request_config_value = _get_attr(request_config, param, None)
            if model_config_value or request_config_value:
                metadata[param] = request_config_value or model_config_value
        return metadata

    @staticmethod
    def _extract_message_from_part(part, role=None):
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

    def _extract_input_message(self, contents, history, system_instruction=None):
        messages = []
        if system_instruction:
            for instruction in system_instruction:
                messages.append({"content": instruction or "", "role": "system"})
        for content in history:
            role = _get_attr(content, "role", "")
            parts = _get_attr(content, "parts", [])
            for part in parts:
                message = self._extract_message_from_part(part, role)
                messages.append(message)
        if isinstance(contents, str):
            messages.append({"content": contents})
            return messages
        if isinstance(contents, dict):
            message = {"content": contents.get("text", "")}
            if contents.get("role", None):
                message["role"] = contents["role"]
            messages.append(message)
            return messages
        if isinstance(contents, Part):
            message = self._extract_message_from_part(contents)
            messages.append(message)
            return messages
        if not isinstance(contents, list):
            messages.append({"content": "[Non-text content object: {}]".format(repr(contents))})
            return messages
        for content in contents:
            if isinstance(content, str):
                messages.append({"content": content})
                continue
            if isinstance(content, dict):
                role = content.get("role", "")
                parts = content.get("parts", [])
                for part in parts:
                    message = self._extract_message_from_part(part)
                    if role:
                        message["role"] = role
                    messages.append(message)
                continue
            if isinstance(content, Part):
                message = self._extract_message_from_part(contents)
                messages.append(message)
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
        # streamed responses will be a list of chunks
        if isinstance(generations, list):
            message_content = ""
            tool_calls = []
            role = "model"
            for chunk in generations:
                for candidate in _get_attr(chunk, "candidates", []):
                    content = _get_attr(candidate, "content", {})
                    role = _get_attr(content, "role", role)
                    parts = _get_attr(content, "parts", [])
                    for part in parts:
                        message = self._extract_message_from_part(part)
                        message_content += message.get("content", "")
                        tool_calls.extend(message.get("tool_calls", []))
            message = {"content": message_content, "role": role}
            if tool_calls:
                message["tool_calls"] = tool_calls
            return [message]      
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
        input_tokens = span.get_metric("vertexai.response.usage.prompt_tokens")
        output_tokens = span.get_metric("vertexai.response.usage.completion_tokens")
        total_tokens = span.get_metric("vertexai.response.usage.total_tokens")

        if input_tokens is not None:
            usage[INPUT_TOKENS_METRIC_KEY] = input_tokens
        if output_tokens is not None:
            usage[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
        if total_tokens is not None:
            usage[TOTAL_TOKENS_METRIC_KEY] = total_tokens
        return usage

