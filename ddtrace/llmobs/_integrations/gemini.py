from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional

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
        instance = kwargs.get("instance", None)
        metadata = llmobs_get_metadata_gemini_vertexai(kwargs, instance)

        system_instruction = get_system_instructions_gemini_vertexai(instance)
        input_contents = get_argument_value(args, kwargs, 0, "contents")
        input_messages = self._extract_input_message(input_contents, system_instruction)

        output_messages = [{"content": ""}]
        if response is not None:
            output_messages = self._extract_output_message(response)

        span._set_ctx_items(
            {
                SPAN_KIND: "llm",
                MODEL_NAME: span.get_tag("google_generativeai.request.model") or "",
                MODEL_PROVIDER: span.get_tag("google_generativeai.request.provider") or "",
                METADATA: metadata,
                INPUT_MESSAGES: input_messages,
                OUTPUT_MESSAGES: output_messages,
                METRICS: self._extract_metrics(response),
            }
        )

    def _extract_input_message(self, contents, system_instruction=None):
        messages = []
        if system_instruction:
            for instruction in system_instruction:
                messages.append({"content": instruction or "", "role": "system"})
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
                message = extract_message_from_part_gemini_vertexai(part, role)
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
                message = extract_message_from_part_gemini_vertexai(part, role)
                output_messages.append(message)
        return output_messages

    def _extract_metrics(self, generations):
        if not generations:
            return {}
        generations_dict = generations.to_dict()

        token_counts = generations_dict.get("usage_metadata", None)
        if not token_counts:
            return
        input_tokens = token_counts.get("prompt_token_count", 0)
        output_tokens = token_counts.get("candidates_token_count", 0)
        total_tokens = input_tokens + output_tokens

        usage = {}
        usage[INPUT_TOKENS_METRIC_KEY] = input_tokens
        usage[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
        usage[TOTAL_TOKENS_METRIC_KEY] = total_tokens
        return usage
