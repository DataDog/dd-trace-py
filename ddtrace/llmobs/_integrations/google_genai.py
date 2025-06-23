from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Iterable

from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._utils import _get_attr
from ddtrace._trace.span import Span
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.internal.utils import get_argument_value
from ddtrace.contrib.internal.google_genai._utils import normalize_contents
from ddtrace.contrib.internal.google_genai._utils import extract_metrics_google_genai
from ddtrace.llmobs._integrations.utils import extract_message_from_part_google


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

         span._set_ctx_items(
            {
                SPAN_KIND: "llm",
                MODEL_NAME: span.get_tag("google_genai.request.model") or "",
                MODEL_PROVIDER: span.get_tag("google_genai.request.provider") or "",
                METADATA: config.model_dump() if config and hasattr(config, "model_dump") else {},
                INPUT_MESSAGES: self._extract_input_message(args, kwargs, config),
                OUTPUT_MESSAGES: self._extract_output_message(response),
                METRICS: extract_metrics_google_genai(response),
            }
        )
    
    def _extract_input_message(self, args, kwargs, config):
        messages = []

        if config is not None:
            system_instruction = _get_attr(config, "system_instruction", None)
            if system_instruction is not None:
                normalized_sys = normalize_contents(system_instruction)

                for content in normalized_sys:
                    role = content.get("role") or "system"
                    parts = content.get("parts", [])

                    if not parts or not isinstance(parts, Iterable):
                        messages.append({"content": "[Non-text content object: {}]".format(repr(content)), "role": role})
                        continue

                    for part in parts:
                        if isinstance(part, str):
                            messages.append({"content": part, "role": role})
                        else:
                            message = extract_message_from_part_google(part, role)
                            messages.append(message)
       
        contents = get_argument_value(args, kwargs, -1, "contents")
        normalized_contents = normalize_contents(contents)
        for content in normalized_contents:
            role = content.get("role") or "user"
            parts = content.get("parts", [])

            if not parts or not isinstance(parts, Iterable):
                message = {"content": "[Non-text content object: {}]".format(repr(content))}
                if role:
                    message["role"] = role
                messages.append(message)
                continue

            for part in parts:
                if isinstance(part, str):
                    message = {"content": part}
                    if role:
                        message["role"] = role
                    messages.append(message)
                    continue

                message = extract_message_from_part_google(part, role)
                messages.append(message)

        return messages
    
    def _extract_output_message(self, response):
         output_messages = []
         if not response:
            return output_messages
