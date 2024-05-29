import json
from typing import Any
from typing import Dict
from typing import Iterable
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import SPAN_KIND

from .base import BaseLLMIntegration


log = get_logger(__name__)


API_KEY = "anthropic.request.api_key"
MODEL = "anthropic.request.model"
TOTAL_COST = "anthropic.tokens.total_cost"
TYPE = "anthropic.request.type"


class AnthropicIntegration(BaseLLMIntegration):
    _integration_name = "anthropic"

    def llmobs_set_tags(
        self,
        span: Span,
        input_messages: Optional[Dict[str, Any]] = None,
        formatted_response: Optional[Dict[str, Any]] = None,
        err: bool = False,
    ) -> None:
        """Extract prompt/response tags from a completion and set them as temporary "_ml_obs.*" tags."""
        # if not self.llmobs_enabled:
        #     return
        parameters = {"temperature": float(span.get_tag("anthropic.request.parameters.temperature") or 0.0)}
        max_tokens = int(span.get_tag("anthropic.request.parameters.max_tokens") or 0)
        if max_tokens:
            parameters["max_tokens"] = max_tokens
        input_messages = self._extract_input_message(input_messages)

        span.set_tag_str(SPAN_KIND, "llm")
        span.set_tag_str(MODEL_NAME, span.get_tag("anthropic.request.model") or "")
        span.set_tag_str(INPUT_MESSAGES, json.dumps(input_messages))
        span.set_tag_str(METADATA, json.dumps(parameters))
        if err or formatted_response is None:
            span.set_tag_str(OUTPUT_MESSAGES, json.dumps([{"content": ""}]))
        else:
            output_messages = self._extract_output_message(formatted_response)
            span.set_tag_str(OUTPUT_MESSAGES, json.dumps(output_messages))

    def _set_base_span_tags(
        self,
        span: Span,
        interface_type: str = "",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        **kwargs: Dict[str, Any],
    ) -> None:
        """Set base level tags that should be present on all Anthropic spans (if they are not None)."""
        span.set_tag_str(TYPE, interface_type)
        if model is not None:
            span.set_tag_str(MODEL, model)
        if api_key is not None:
            if len(api_key) >= 4:
                span.set_tag_str(API_KEY, "...%s" % str(api_key[-4:]))
            else:
                span.set_tag_str(API_KEY, api_key)

    @staticmethod
    def _extract_input_message(messages):
        """Extract input messages from the stored prompt.
        Anthropic allows for messages and multiple texts in a message, which requires some special casing.
        """
        if not isinstance(messages, Iterable):
            log.warning("Anthropic input must be a list of messages.")

        input_messages = []
        for message in messages:
            if not isinstance(message, dict):
                log.warning("Anthropic message input must be a list of message param dicts.")
                continue

            content = message.get("content", None)
            role = message.get("role", None)

            # TO-DO: do we want to truncate these inputs ?

            if not role or not content:
                log.warning("Anthropic input message must have content and role.")

            if isinstance(content, str):
                input_messages.append({"content": content, "role": role})

            elif isinstance(content, list):
                for entry in content:
                    if entry.get("type") == "text":
                        input_messages.append({"content": entry.get("text", ""), "role": role})
                    elif entry.get("type") == "image":
                        # Store a placeholder for potentially enormous binary image data.
                        input_messages.append({"content": "([IMAGE DETECTED])", "role": role})
                    else:
                        input_messages.append({"content": entry, "role": role})

        return input_messages

    @staticmethod
    def _extract_output_message(formatted_response):
        """Extract output messages from the stored response."""
        # TO-DO: do we want to truncate these outputs ?

        output_messages = []
        if isinstance(formatted_response.content, str):
            return [{"content": formatted_response.content}]
        if isinstance(formatted_response.content, list):
            for response in formatted_response.content:
                if isinstance(response.text, str):
                    output_messages.append({"content": response.text})
        return output_messages

    # def record_usage(self, span: Span, usage: Dict[str, Any]) -> None:
    #     if not usage or self.metrics_enabled is False:
    #         return
    #     for token_type in ("prompt", "completion", "total"):
    #         num_tokens = usage.get("token_usage", {}).get(token_type + "_tokens")
    #         if not num_tokens:
    #             continue
    #         self.metric(span, "dist", "tokens.%s" % token_type, num_tokens)
    #     total_cost = span.get_metric(TOTAL_COST)
    #     if total_cost:
    #         self.metric(span, "incr", "tokens.total_cost", total_cost)
