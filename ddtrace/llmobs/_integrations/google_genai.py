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
         metadata = config.model_dump() if config else {}

         input_messages = self._extract_input_message(args, kwargs, config)
         output_messages = None
         metrics = None

         span._set_ctx_items(
            {
                SPAN_KIND: "llm",
                MODEL_NAME: span.get_tag("google_genai.request.model") or "",
                MODEL_PROVIDER: span.get_tag("google_genai.request.provider") or "",
                METADATA: metadata,
                INPUT_MESSAGES: input_messages,
                # OUTPUT_MESSAGES: output_messages,
                # METRICS: get_llmobs_metrics_tags("vertexai", span),
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
                            message = extract_message_from_part_google_genai(part, role)
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

                message = extract_message_from_part_google_genai(part, role)
                messages.append(message)

        return messages
    

def extract_message_from_part_google_genai(part, role=None):
    text = _get_attr(part, "text", "")
    function_call = _get_attr(part, "function_call", None)
    function_response = _get_attr(part, "function_response", None)
    message = {"content": text}
    if role:
        message["role"] = role
    if function_call:
        function_call_dict = function_call
        if not isinstance(function_call, dict):
            function_call_dict = function_call.model_dump()
        message["tool_calls"] = [
            {"name": function_call_dict.get("name", ""), "arguments": function_call_dict.get("args", {})}
        ]
    if function_response:
        function_response_dict = function_response
        if not isinstance(function_response, dict):
            function_response_dict = function_response.model_dump()
        message["content"] = "[tool result: {}]".format(function_response_dict.get("response", "")) #this will override the text in the part?
    return message