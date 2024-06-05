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
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import SPAN_KIND

from .base import BaseLLMIntegration


log = get_logger(__name__)


API_KEY = "anthropic.request.api_key"
MODEL = "anthropic.request.model"


class AnthropicIntegration(BaseLLMIntegration):
    _integration_name = "anthropic"

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
                span.set_tag_str(API_KEY, f"sk-...{str(api_key[-4:])}")
            else:
                span.set_tag_str(API_KEY, api_key)

    def llmobs_set_tags(
        self,
        resp: Any,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        err: Optional[Any] = None,
    ) -> None:
        """Extract prompt/response tags from a completion and set them as temporary "_ml_obs.*" tags."""
        if not self.llmobs_enabled:
            return

        parameters = {
            "temperature": float(kwargs.get("temperature", 1.0)),
            "max_tokens": float(kwargs.get("max_tokens", 0)),
        }
        messages = get_argument_value([], kwargs, 0, "messages")
        system_prompt = get_argument_value([], kwargs, 0, "system", optional=True)
        input_messages = self._extract_input_message(messages, system_prompt)

        span.set_tag_str(SPAN_KIND, "llm")
        span.set_tag_str(MODEL_NAME, span.get_tag("anthropic.request.model") or "")
        span.set_tag_str(INPUT_MESSAGES, json.dumps(input_messages))
        span.set_tag_str(METADATA, json.dumps(parameters))
        span.set_tag_str(MODEL_PROVIDER, "anthropic")
        if err or resp is None:
            span.set_tag_str(OUTPUT_MESSAGES, json.dumps([{"content": ""}]))
        else:
            output_messages = self._extract_output_message(resp)
            span.set_tag_str(OUTPUT_MESSAGES, json.dumps(output_messages))

        span.set_tag_str(METRICS, json.dumps(_get_llmobs_metrics_tags(span)))

    def _extract_input_message(self, messages, system_prompt=None):
        """Extract input messages from the stored prompt.
        Anthropic allows for messages and multiple texts in a message, which requires some special casing.
        """
        if not isinstance(messages, Iterable):
            log.warning("Anthropic input must be a list of messages.")

        input_messages = []
        if system_prompt is not None:
            input_messages.append({"content": system_prompt, "role": "system"})
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
                for block in content:
                    if block.get("type") == "text":
                        input_messages.append({"content": block.get("text", ""), "role": role})
                    elif block.get("type") == "image":
                        # Store a placeholder for potentially enormous binary image data.
                        input_messages.append({"content": "([IMAGE DETECTED])", "role": role})
                    else:
                        input_messages.append({"content": str(block), "role": role})

        return input_messages

    def _extract_output_message(self, response):
        """Extract output messages from the stored response."""
        output_messages = []
        content = _get_attr(response, "content", None)
        role = _get_attr(response, "role", "")

        if isinstance(content, str):
            return [{"content": self.trunc(content), "role": role}]

        elif isinstance(content, list):
            for completion in content:
                text = _get_attr(completion, "text", None)
                if isinstance(text, str):
                    output_messages.append({"content": self.trunc(text), "role": role})
        return output_messages

    def record_usage(self, span: Span, usage: Dict[str, Any]) -> None:
        if not usage:
            return
        input_tokens = _get_attr(usage, "input_tokens", None)
        output_tokens = _get_attr(usage, "output_tokens", None)

        span.set_metric("anthropic.response.usage.input_tokens", input_tokens)
        span.set_metric("anthropic.response.usage.output_tokens", output_tokens)

        if input_tokens is not None and output_tokens is not None:
            span.set_metric("anthropic.response.usage.total_tokens", input_tokens + output_tokens)


def _get_llmobs_metrics_tags(span):
    return {
        "input_tokens": span.get_metric("anthropic.response.usage.input_tokens"),
        "output_tokens": span.get_metric("anthropic.response.usage.output_tokens"),
        "total_tokens": span.get_metric("anthropic.response.usage.total_tokens"),
    }


def _get_attr(o: Any, attr: str, default: Any):
    # Since our response may be a dict or object, convenience method
    if isinstance(o, dict):
        return o.get(attr, default)
    else:
        return getattr(o, attr, default)
