from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from urllib.parse import urlparse

from ddtrace.constants import SPAN_KIND as APM_SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import net
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
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
from ddtrace.llmobs.types import Message
from ddtrace.trace import Span


log = get_logger(__name__)


MODEL_TAG = "llama_index.request.model"


class LlamaIndexIntegration(BaseLLMIntegration):
    """LLMObs integration for llama_index."""

    _integration_name = "llama_index"

    def _set_base_span_tags(
        self,
        span: Span,
        model: Optional[str] = None,
        **kwargs: Dict[str, Any],
    ) -> None:
        """Set base level tags that should be present on all llama_index spans.

        Sets APM span.kind to CLIENT for peer service detection and extracts
        the API host from the instance's api_base attribute when available.
        """
        if model is not None:
            span._set_tag_str(MODEL_TAG, model)
        span._set_tag_str(APM_SPAN_KIND, SpanKind.CLIENT)
        span._set_tag_str(COMPONENT, self._integration_name)

        instance = kwargs.get("instance")
        if instance is not None:
            api_base = getattr(instance, "api_base", None)
            if api_base:
                try:
                    host = urlparse(api_base).hostname
                    if host:
                        span._set_tag_str(net.TARGET_HOST, host)
                except Exception:
                    log.debug("Failed to parse api_base URL for peer service: %s", api_base)

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

        messages = kwargs.get("messages", [])
        if not messages and args:
            first_arg = args[0]
            if isinstance(first_arg, str):
                messages = [{"role": "user", "content": first_arg}]
            elif isinstance(first_arg, (list, tuple)):
                messages = first_arg
        input_messages = self._extract_input_messages(messages)

        output_messages: List[Message] = [Message(content="")]
        if not span.error and response is not None:
            output_messages = self._extract_output_messages(response)

        metrics = self._extract_usage(response) if response else {}

        span._set_ctx_items(
            {
                SPAN_KIND: "llm",
                MODEL_NAME: span.get_tag(MODEL_TAG) or "",
                MODEL_PROVIDER: "llama_index",
                INPUT_MESSAGES: input_messages,
                METADATA: parameters,
                OUTPUT_MESSAGES: output_messages,
                METRICS: metrics,
            }
        )

    def _extract_input_messages(self, messages: List[Any]) -> List[Message]:
        """Extract input messages from llama_index ChatMessage objects or dicts."""
        input_messages: List[Message] = []
        for message in messages:
            content = _get_attr(message, "content", "")
            role = _get_attr(message, "role", "user")
            # llama_index uses MessageRole enum; extract the string value
            role_str = role.value if hasattr(role, "value") else str(role)

            if isinstance(content, str):
                input_messages.append(Message(content=content, role=role_str))
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        part_type = _get_attr(part, "type", "")
                        if part_type == "text":
                            input_messages.append(Message(content=str(_get_attr(part, "text", "")), role=role_str))
                        elif part_type == "image":
                            input_messages.append(Message(content="([IMAGE DETECTED])", role=role_str))
        return input_messages

    def _extract_output_messages(self, response: Any) -> List[Message]:
        """Extract output messages from llama_index response objects.

        Handles ChatResponse (.message.content), CompletionResponse (.text),
        and Response (.response) types from llama_index.
        """
        # ChatResponse: response.message.content
        message = _get_attr(response, "message", None)
        if message is not None:
            content = _get_attr(message, "content", "")
            role = _get_attr(message, "role", "assistant")
            role_str = role.value if hasattr(role, "value") else str(role)
            if content:
                return [Message(content=str(content), role=role_str)]

        # CompletionResponse: response.text
        text = _get_attr(response, "text", None)
        if text:
            return [Message(content=str(text), role="assistant")]

        # Response (query engine): response.response
        resp_text = _get_attr(response, "response", None)
        if resp_text:
            return [Message(content=str(resp_text), role="assistant")]

        return [Message(content="")]

    def _extract_usage(self, response: Any) -> Dict[str, int]:
        """Extract token usage from llama_index response objects.

        Checks response.raw (dict with API provider response) and
        response.usage for token counts.
        """
        metrics: Dict[str, int] = {}
        usage = _get_attr(response, "usage", None)
        # llama_index stores raw API responses in response.raw
        if usage is None:
            raw = _get_attr(response, "raw", None)
            if isinstance(raw, dict):
                usage = raw.get("usage")

        if not usage:
            return metrics

        if isinstance(usage, dict):
            input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens")
            output_tokens = usage.get("output_tokens") or usage.get("completion_tokens")
        else:
            input_tokens = _get_attr(usage, "input_tokens", None) or _get_attr(usage, "prompt_tokens", None)
            output_tokens = _get_attr(usage, "output_tokens", None) or _get_attr(usage, "completion_tokens", None)

        if input_tokens is not None:
            metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens
        if output_tokens is not None:
            metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
        if input_tokens is not None and output_tokens is not None:
            metrics[TOTAL_TOKENS_METRIC_KEY] = input_tokens + output_tokens
        return metrics
