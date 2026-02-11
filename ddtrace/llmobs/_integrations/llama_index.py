from typing import Any
from typing import Dict
from typing import List
from typing import Optional

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
        """Set base level tags that should be present on all llama_index spans."""
        if model is not None:
            span._set_tag_str(MODEL_TAG, model)

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

    def _extract_input_messages(self, messages: List[Dict[str, Any]]) -> List[Message]:
        """Extract input messages from request.

        TODO: Customize this method based on the library's message format.
        """
        input_messages: List[Message] = []
        for message in messages:
            content = _get_attr(message, "content", "")
            role = _get_attr(message, "role", "user")

            if isinstance(content, str):
                input_messages.append(Message(content=content, role=str(role)))
            elif isinstance(content, list):
                # Handle multi-part messages (text, images, etc.)
                for part in content:
                    if isinstance(part, dict):
                        part_type = _get_attr(part, "type", "")
                        if part_type == "text":
                            input_messages.append(
                                Message(content=str(_get_attr(part, "text", "")), role=str(role))
                            )
                        elif part_type == "image":
                            input_messages.append(Message(content="([IMAGE DETECTED])", role=str(role)))
        return input_messages

    def _extract_output_messages(self, response: Any) -> List[Message]:
        """Extract output messages from response.

        TODO: Customize this method based on the library's response format.
        """
        output_messages: List[Message] = []

        # Try common response patterns
        # Pattern 1: response.content (Anthropic style)
        content = _get_attr(response, "content", None)
        if content:
            if isinstance(content, str):
                return [Message(content=content, role="assistant")]
            elif isinstance(content, list):
                for item in content:
                    text = _get_attr(item, "text", None)
                    if text:
                        output_messages.append(Message(content=str(text), role="assistant"))

        # Pattern 2: response.choices[0].message.content (OpenAI style)
        choices = _get_attr(response, "choices", None)
        if choices and len(choices) > 0:
            message = _get_attr(choices[0], "message", None)
            if message:
                msg_content = _get_attr(message, "content", "")
                if msg_content:
                    output_messages.append(Message(content=str(msg_content), role="assistant"))

        return output_messages if output_messages else [Message(content="")]

    def _extract_usage(self, response: Any) -> Dict[str, int]:
        """Extract token usage from response.

        TODO: Customize this method based on the library's usage format.
        """
        metrics = {}
        usage = _get_attr(response, "usage", None)
        if usage:
            # Try different token field names
            input_tokens = (
                _get_attr(usage, "input_tokens", None)
                or _get_attr(usage, "prompt_tokens", None)
            )
            output_tokens = (
                _get_attr(usage, "output_tokens", None)
                or _get_attr(usage, "completion_tokens", None)
            )

            if input_tokens is not None:
                metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens
            if output_tokens is not None:
                metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
            if input_tokens is not None and output_tokens is not None:
                metrics[TOTAL_TOKENS_METRIC_KEY] = input_tokens + output_tokens
        return metrics
