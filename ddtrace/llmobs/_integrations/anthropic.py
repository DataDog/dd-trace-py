import json
from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import PROXY_REQUEST
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.utils import get_llmobs_metrics_tags
from ddtrace.llmobs._integrations.utils import update_input_output_value
from ddtrace.llmobs._utils import _get_attr
from ddtrace.trace import Span


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

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Extract prompt/response tags from a completion and set them as temporary "_ml_obs.*" tags."""
        parameters = {}
        if kwargs.get("temperature"):
            parameters["temperature"] = kwargs.get("temperature")
        if kwargs.get("max_tokens"):
            parameters["max_tokens"] = kwargs.get("max_tokens")
        messages = kwargs.get("messages")
        system_prompt = kwargs.get("system")
        input_messages = self._extract_input_message(messages, system_prompt)

        output_messages = [{"content": ""}]
        if not span.error and response is not None:
            output_messages = self._extract_output_message(response)
        span_kind = "workflow" if span._get_ctx_item(PROXY_REQUEST) else "llm"

        span._set_ctx_items(
            {
                SPAN_KIND: span_kind,
                MODEL_NAME: span.get_tag("anthropic.request.model") or "",
                MODEL_PROVIDER: "anthropic",
                INPUT_MESSAGES: input_messages,
                METADATA: parameters,
                OUTPUT_MESSAGES: output_messages,
                METRICS: get_llmobs_metrics_tags("anthropic", span) if span_kind != "workflow" else {},
            }
        )
        update_input_output_value(span, span_kind)

    def _extract_input_message(self, messages, system_prompt: Optional[Union[str, List[Dict[str, Any]]]] = None):
        """Extract input messages from the stored prompt.
        Anthropic allows for messages and multiple texts in a message, which requires some special casing.
        """
        if not isinstance(messages, Iterable):
            log.warning("Anthropic input must be a list of messages.")

        input_messages = []
        if system_prompt is not None:
            messages = [{"content": system_prompt, "role": "system"}] + messages

        for message in messages:
            if not isinstance(message, dict):
                log.warning("Anthropic message input must be a list of message param dicts.")
                continue

            content = _get_attr(message, "content", None)
            role = _get_attr(message, "role", None)

            if role is None or content is None:
                log.warning("Anthropic input message must have content and role.")

            if isinstance(content, str):
                input_messages.append({"content": content, "role": role})

            elif isinstance(content, list):
                for block in content:
                    if _get_attr(block, "type", None) == "text":
                        input_messages.append({"content": _get_attr(block, "text", ""), "role": role})

                    elif _get_attr(block, "type", None) == "image":
                        # Store a placeholder for potentially enormous binary image data.
                        input_messages.append({"content": "([IMAGE DETECTED])", "role": role})

                    elif _get_attr(block, "type", None) == "tool_use":
                        text = _get_attr(block, "text", None)
                        input_data = _get_attr(block, "input", "")
                        if isinstance(input_data, str):
                            input_data = json.loads(input_data)
                        tool_call_info = {
                            "name": _get_attr(block, "name", ""),
                            "arguments": input_data,
                            "tool_id": _get_attr(block, "id", ""),
                            "type": _get_attr(block, "type", ""),
                        }
                        if text is None:
                            text = ""
                        input_messages.append({"content": text, "role": role, "tool_calls": [tool_call_info]})

                    elif _get_attr(block, "type", None) == "tool_result":
                        content = _get_attr(block, "content", None)
                        if isinstance(content, str):
                            input_messages.append({"content": "[tool result: {}]".format(content), "role": role})
                        elif isinstance(content, list):
                            input_messages.append({"content": [], "role": role})
                            for tool_result_block in content:
                                if _get_attr(tool_result_block, "text", "") != "":
                                    input_messages[-1]["content"].append(_get_attr(tool_result_block, "text", ""))
                                elif _get_attr(tool_result_block, "type", None) == "image":
                                    # Store a placeholder for potentially enormous binary image data.
                                    input_messages[-1]["content"].append("([IMAGE DETECTED])")
                    else:
                        input_messages.append({"content": str(block), "role": role})

        return input_messages

    def _extract_output_message(self, response):
        """Extract output messages from the stored response."""
        output_messages = []
        content = _get_attr(response, "content", "")
        role = _get_attr(response, "role", "")

        if isinstance(content, str):
            return [{"content": content, "role": role}]

        elif isinstance(content, list):
            for completion in content:
                text = _get_attr(completion, "text", None)
                if isinstance(text, str):
                    output_messages.append({"content": text, "role": role})
                else:
                    if _get_attr(completion, "type", None) == "tool_use":
                        input_data = _get_attr(completion, "input", "")
                        if isinstance(input_data, str):
                            input_data = json.loads(input_data)
                        tool_call_info = {
                            "name": _get_attr(completion, "name", ""),
                            "arguments": input_data,
                            "tool_id": _get_attr(completion, "id", ""),
                            "type": _get_attr(completion, "type", ""),
                        }
                        if text is None:
                            text = ""
                        output_messages.append({"content": text, "role": role, "tool_calls": [tool_call_info]})
        return output_messages

    def record_usage(self, span: Span, usage: Dict[str, Any]) -> None:
        if not usage:
            return
        input_tokens = _get_attr(usage, "input_tokens", None)
        output_tokens = _get_attr(usage, "output_tokens", None)

        if input_tokens is not None:
            span.set_metric("anthropic.response.usage.input_tokens", input_tokens)
        if output_tokens is not None:
            span.set_metric("anthropic.response.usage.output_tokens", output_tokens)
        if input_tokens is not None and output_tokens is not None:
            span.set_metric("anthropic.response.usage.total_tokens", input_tokens + output_tokens)
        
    def _get_base_url(self, kwargs: Dict[str, Any]) -> Optional[str]:
        instance = kwargs.get("instance", None)
        client = getattr(instance, "_client", None)
        base_url = getattr(client, "_base_url", None) if client else None
        return str(base_url) if base_url else None
