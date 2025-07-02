from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.google_genai_utils import DEFAULT_MODEL_ROLE
from ddtrace.llmobs._integrations.google_genai_utils import extract_message_from_part_google_genai
from ddtrace.llmobs._integrations.google_genai_utils import extract_metrics_google_genai
from ddtrace.llmobs._integrations.google_genai_utils import extract_provider_and_model_name
from ddtrace.llmobs._integrations.google_genai_utils import normalize_contents
from ddtrace.llmobs._utils import _get_attr


# https://cloud.google.com/vertex-ai/generative-ai/docs/multimodal/content-generation-parameters
METADATA_PARAMS = [
    "temperature",
    "top_p",
    "top_k",
    "candidate_count",
    "max_output_tokens",
    "stop_sequences",
    "response_logprobs",
    "logprobs",
    "presence_penalty",
    "frequency_penalty",
    "seed",
    "response_mime_type",
    "safety_settings",
    "automatic_function_calling",
    "tools",
]


class GoogleGenAIIntegration(BaseLLMIntegration):
    _integration_name = "google_genai"

    def _set_base_span_tags(
        self, span: Span, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        if provider is not None:
            span.set_tag_str("google_genai.request.provider", provider)
        if model is not None:
            span.set_tag_str("google_genai.request.model", model)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        config = get_argument_value(args, kwargs, -1, "config", optional=True)
        provider_name, model_name = extract_provider_and_model_name(kwargs)

        span._set_ctx_items(
            {
                SPAN_KIND: "llm",
                MODEL_NAME: model_name,
                MODEL_PROVIDER: provider_name,
                METADATA: self._extract_metadata(config),
                INPUT_MESSAGES: self._extract_input_message(args, kwargs, config),
                OUTPUT_MESSAGES: self._extract_output_messages(response),
                METRICS: extract_metrics_google_genai(response),
            }
        )

    def _extract_input_message(self, args: List[Any], kwargs: Dict[str, Any], config) -> List[Dict[str, Any]]:
        messages = []

        system_instruction = _get_attr(config, "system_instruction", None)
        if system_instruction is not None:
            messages.extend(self._extract_messages_from_contents(system_instruction, "system"))

        contents = get_argument_value(args, kwargs, -1, "contents")
        messages.extend(self._extract_messages_from_contents(contents, "user"))

        return messages

    def _extract_messages_from_contents(self, contents, default_role: str) -> List[Dict[str, Any]]:
        messages = []
        for content in normalize_contents(contents):
            role = content.get("role") or default_role
            for part in content.get("parts", []):
                messages.append(extract_message_from_part_google_genai(part, role))
        return messages

    def _extract_output_messages(self, response) -> List[Dict[str, Any]]:
        if not response:
            return [{"content": "", "role": DEFAULT_MODEL_ROLE}]
        messages = []
        candidates = _get_attr(response, "candidates", [])
        for candidate in candidates:
            content = _get_attr(candidate, "content", None)
            if not content:
                continue
            parts = _get_attr(content, "parts", [])
            role = _get_attr(content, "role", DEFAULT_MODEL_ROLE)
            for part in parts:
                message = extract_message_from_part_google_genai(part, role)
                messages.append(message)
        return messages

    def _extract_metadata(self, config) -> Dict[str, Any]:
        if not config:
            return {}
        metadata = {}
        for param in METADATA_PARAMS:
            metadata[param] = _get_attr(config, param, None)
        return metadata
