import json
from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
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
        resp: Any,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        err: Optional[Any] = None,
    ) -> None:
        """Extract prompt/response tags from a completion and set them as temporary "_ml_obs.*" tags."""
        # if not self.llmobs_enabled:
        #     return
        parameters = {
            "temperature": float(span.get_tag("anthropic.request.parameters.temperature") or 1.0),
            "max_tokens": int(span.get_tag("anthropic.request.parameters.max_tokens") or 0),
        }
        messages = get_argument_value(args, kwargs, 0, "messages")
        input_messages = self._extract_input_message(messages)

        span.set_tag_str(SPAN_KIND, "llm")
        span.set_tag_str(MODEL_NAME, span.get_tag("anthropic.request.model") or "")
        span.set_tag_str(INPUT_MESSAGES, json.dumps(input_messages))
        span.set_tag_str(METADATA, json.dumps(parameters))
        if err or resp is None:
            span.set_tag_str(OUTPUT_MESSAGES, json.dumps([{"content": ""}]))
        else:
            output_messages = self._extract_output_message(resp)
            span.set_tag_str(OUTPUT_MESSAGES, json.dumps(output_messages))

        if isinstance(resp, dict) and "usage" in "response":
            self.record_usage(span, resp["usage"])
        elif getattr(resp, "usage", None) is not None:
            self.record_usage(
                span,
                {
                    "prompt": getattr(resp.usage, "input_tokens", 0),
                    "completion": getattr(resp.usage, "output_tokens", 0),
                },
            )

    def _set_base_span_tags(
        self,
        span: Span,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        **kwargs: Dict[str, Any],
    ) -> None:
        """Set base level tags that should be present on all Anthropic spans (if they are not None)."""
        if model is not None:
            span.set_tag_str(MODEL, model)
        if api_key is not None:
            if len(api_key) >= 4:
                span.set_tag_str(API_KEY, f"...{str(api_key[-4:])}")
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

            if role is None or content is None:
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
    def _extract_output_message(response):
        """Extract output messages from the stored response."""

        output_messages = []
        if isinstance(_get_attr(response, "content", None), str):
            return [{"content": response.content}]
        elif isinstance(_get_attr(response, "content", None), list):
            for completion in _get_attr(response, "content", []):
                if isinstance(_get_attr(completion, "text", None), str):
                    output_messages.append(
                        {"content": _get_attr(completion, "text", ""), "role": _get_attr(response, "role", "")}
                    )
        return output_messages

    def record_usage(self, span: Span, usage: Dict[str, Any]) -> None:
        if not usage or not self.metrics_enabled:
            return
        for token_type in ("prompt", "completion"):
            num_tokens = usage.get(token_type, None)
            if num_tokens is None:
                continue
            span.set_metric("anthropic.response.usage.%s_tokens" % token_type, num_tokens)


def _get_attr(o, attr, default):
    # Since our response may be a dict or object, convenience method
    if isinstance(o, dict):
        return o.get(attr, default)
    else:
        return getattr(o, attr, default)
