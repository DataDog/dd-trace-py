from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional

from ddtrace.internal.utils import ArgumentError
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
from ddtrace.llmobs._integrations.google_utils import extract_message_from_part_gemini_vertexai
from ddtrace.llmobs._integrations.google_utils import get_system_instructions_gemini_vertexai
from ddtrace.llmobs._integrations.google_utils import llmobs_get_metadata_gemini_vertexai
from ddtrace.llmobs._utils import _get_attr
from ddtrace.trace import Span


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
    ) -> None:
        instance = kwargs.get("instance", None)
        history = kwargs.get("history", [])
        metrics = kwargs.get("metrics", {})
        metadata = llmobs_get_metadata_gemini_vertexai(kwargs, instance)

        system_instruction = get_system_instructions_gemini_vertexai(instance)
        input_contents = None
        try:
            input_contents = get_argument_value(args, kwargs, 0, "content")
        except ArgumentError:
            input_contents = get_argument_value(args, kwargs, 0, "contents")
        input_messages = self._extract_input_message(input_contents, history, system_instruction)

        output_messages = [{"content": ""}]
        if response is not None:
            output_messages = self._extract_output_message(response)
            metrics = self._extract_metrics_from_response(response)

        span._set_ctx_items(
            {
                SPAN_KIND: "llm",
                MODEL_NAME: span.get_tag("vertexai.request.model") or "",
                MODEL_PROVIDER: span.get_tag("vertexai.request.provider") or "",
                METADATA: metadata,
                INPUT_MESSAGES: input_messages,
                OUTPUT_MESSAGES: output_messages,
                METRICS: metrics,
            }
        )

    def _extract_metrics_from_response(self, response):
        """Extract metrics from the response."""
        if isinstance(response, list):
            for chunk in response:
                token_counts = _get_attr(chunk, "usage_metadata", None)
                if not token_counts:
                    continue
                input_tokens = _get_attr(token_counts, "prompt_token_count", 0)
                output_tokens = _get_attr(token_counts, "candidates_token_count", 0)
                total_tokens = _get_attr(token_counts, "total_token_count", 0)
        else:
            generations_dict = response.to_dict()

            token_counts = generations_dict.get("usage_metadata", None)
            if not token_counts:
                return

            input_tokens = _get_attr(token_counts, "prompt_token_count", 0)
            output_tokens = _get_attr(token_counts, "candidates_token_count", 0)
            total_tokens = _get_attr(token_counts, "total_token_count", 0)

        metrics = {}
        if input_tokens is not None:
            metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens
        if output_tokens is not None:
            metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
        if total_tokens is not None:
            metrics[TOTAL_TOKENS_METRIC_KEY] = total_tokens

        return metrics

    def _extract_input_message(self, contents, history, system_instruction=None):
        from vertexai.generative_models._generative_models import Part

        messages = []
        if system_instruction:
            for instruction in system_instruction:
                messages.append({"content": instruction or "", "role": "system"})
        for content in history:
            messages.extend(self._extract_messages_from_content(content))
        if isinstance(contents, str):
            messages.append({"content": contents})
            return messages
        if isinstance(contents, Part):
            message = extract_message_from_part_gemini_vertexai(contents)
            messages.append(message)
            return messages
        if not isinstance(contents, list):
            messages.append({"content": "[Non-text content object: {}]".format(repr(contents))})
            return messages
        for content in contents:
            if isinstance(content, str):
                messages.append({"content": content})
                continue
            if isinstance(content, Part):
                message = extract_message_from_part_gemini_vertexai(content)
                messages.append(message)
                continue
            messages.extend(self._extract_messages_from_content(content))
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
                    messages = self._extract_messages_from_content(content)
                    for message in messages:
                        message_content += message.get("content", "")
                        tool_calls.extend(message.get("tool_calls", []))
            message = {"content": message_content, "role": role}
            if tool_calls:
                message["tool_calls"] = tool_calls
            return [message]
        generations_dict = generations.to_dict()
        for candidate in generations_dict.get("candidates", []):
            content = candidate.get("content", {})
            output_messages.extend(self._extract_messages_from_content(content))
        return output_messages

    @staticmethod
    def _extract_messages_from_content(content):
        messages = []
        role = _get_attr(content, "role", "")
        parts = _get_attr(content, "parts", [])
        if not parts or not isinstance(parts, Iterable):
            message = {"content": "[Non-text content object: {}]".format(repr(content))}
            if role:
                message["role"] = role
            messages.append(message)
            return messages
        for part in parts:
            message = extract_message_from_part_gemini_vertexai(part, role)
            messages.append(message)
        return messages
