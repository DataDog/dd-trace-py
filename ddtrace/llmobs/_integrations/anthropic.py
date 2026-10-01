from collections.abc import Iterable
from typing import Any
from typing import Optional
from typing import Union

from ddtrace.contrib.internal.anthropic import _utils as anthropic_utils
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import IMAGE_DETECTED_MARKER
from ddtrace.llmobs._constants import IMAGE_TOO_LARGE_MARKER
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.utils import anthropic_tool_call_from_block
from ddtrace.llmobs._integrations.utils import anthropic_tool_result_from_block
from ddtrace.llmobs._integrations.utils import format_image_part_with_guard
from ddtrace.llmobs._integrations.utils import get_messages_from_anthropic_content
from ddtrace.llmobs._integrations.utils import get_tool_definitions_from_anthropic_tools
from ddtrace.llmobs._integrations.utils import is_renderable_image_mime
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs.types import Message
from ddtrace.llmobs.types import ToolDefinition
from ddtrace.trace import Span


log = get_logger(__name__)


def _extract_anthropic_image_source(block: Any) -> Optional[tuple[Union[bytes, str], str]]:
    """Return (data, media_type) for an inline base64 Anthropic image block, else None.

    Unencoded, so the caller's guard can reject an oversized image before paying to encode it.
    """
    source = _get_attr(block, "source", {})
    if _get_attr(source, "type", "") != "base64":
        return None
    data = _get_attr(source, "data", "")
    # source.data may be IO[bytes] or PathLike (anthropic Base64FileInput) -- keep those out of spans.
    if not data or not isinstance(data, (str, bytes)):
        return None
    # base64 is ASCII; len() would undercount non-ASCII text 4x and slip it past the guard.
    if isinstance(data, str) and not data.isascii():
        return None
    # Reject bad mime here, not in the guard, so the caller's "too large" marker can't misreport.
    media_type = str(_get_attr(source, "media_type", ""))
    if not is_renderable_image_mime(media_type):
        return None
    return data, media_type


class AnthropicIntegration(BaseLLMIntegration):
    _integration_name = "anthropic"

    def _set_base_span_tags(
        self,
        span: Span,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        **kwargs: dict[str, Any],
    ) -> None:
        anthropic_utils.set_base_span_tags(span, model=model, instance=kwargs.get("instance"))

    def _llmobs_set_tags(
        self,
        span: Span,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Extract prompt/response tags from a completion and set them as temporary "_ml_obs.*" tags."""
        parameters = {}
        if kwargs.get("temperature"):
            parameters["temperature"] = kwargs.get("temperature")
        if kwargs.get("max_tokens"):
            parameters["max_tokens"] = kwargs.get("max_tokens")
        tool_definitions = None
        if kwargs.get("tools"):
            tool_definitions = self._extract_tools(kwargs.get("tools"))
        messages = kwargs.get("messages")
        system_prompt = kwargs.get("system")
        input_messages = self._extract_input_message(list(messages) if messages else [], system_prompt)

        output_messages: list[Message] = [Message(content="")]
        # Record output whenever a response exists, independent of span.error: a
        # genuine model error leaves no response, while an AI Guard block after
        # the model call errors the span but keeps a valid response (APPSEC-68147).
        if response is not None:
            output_messages = self._extract_output_message(response)
            # Recorded under "finish_reason" to match openai/litellm
            finish_reason = _get_attr(response, "stop_reason", None) or _get_attr(response, "finish_reason", None)
            if finish_reason:
                parameters["finish_reason"] = str(finish_reason)
        span_kind = anthropic_utils.get_span_kind(span)

        usage = _get_attr(response, "usage", {})
        metrics = self._extract_usage(span, usage) if span_kind != "workflow" else {}

        _annotate_llmobs_span_data(
            span,
            kind=span_kind,
            model_name=span.get_tag(anthropic_utils.MODEL) or "",
            model_provider=self._get_model_provider(span),
            input_messages=input_messages,
            metadata=parameters,
            output_messages=output_messages,
            metrics=metrics,
            tool_definitions=tool_definitions,
        )

    def _set_apm_shadow_tags(self, span, args, kwargs, response=None, operation=""):
        anthropic_utils.set_apm_shadow_tags(span, response, self.llmobs_enabled)

    def _extract_input_message(
        self, messages: list[dict[str, Any]], system_prompt: Optional[Union[str, list[dict[str, Any]]]] = None
    ) -> list[Message]:
        """Extract input messages from the stored prompt.
        Anthropic allows for messages and multiple texts in a message, which requires some special casing.
        """
        if not isinstance(messages, Iterable):
            log.warning("Anthropic input must be a list of messages.")

        input_messages: list[Message] = []
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
                input_messages.append(Message(content=content, role=str(role)))

            elif isinstance(content, list):
                for block in content:
                    content_type = _get_attr(block, "type", None)
                    if content_type == "text":
                        input_messages.append(Message(content=str(_get_attr(block, "text", "")), role=str(role)))

                    elif content_type == "image":
                        source = _extract_anthropic_image_source(block)
                        if source is None:
                            input_messages.append(Message(content=IMAGE_DETECTED_MARKER, role=str(role)))
                            continue
                        data, media_type = source
                        image_part = format_image_part_with_guard(data, media_type)
                        if image_part is None:
                            input_messages.append(Message(content=IMAGE_TOO_LARGE_MARKER, role=str(role)))
                            continue
                        image_message: Message = Message(content="", role=str(role))
                        image_message["image_parts"] = [image_part]
                        input_messages.append(image_message)

                    elif content_type == "thinking":
                        thinking_text = _get_attr(block, "thinking", "")
                        input_messages.append(Message(content=str(thinking_text), role="reasoning"))

                    elif "tool_use" in (content_type or ""):
                        text = _get_attr(block, "text", None)
                        if text is None:
                            text = ""
                        input_messages.append(
                            Message(
                                content=str(text),
                                role=str(role),
                                tool_calls=[anthropic_tool_call_from_block(block)],
                            )
                        )

                    elif "tool_result" in (content_type or ""):
                        input_messages.append(
                            Message(
                                content="",
                                role=str(role),
                                tool_results=[anthropic_tool_result_from_block(block)],
                            )
                        )
                    else:
                        input_messages.append(Message(content=str(block), role=str(role)))

        return input_messages

    def _extract_output_message(self, response) -> list[Message]:
        """Extract output messages from the stored response."""
        return get_messages_from_anthropic_content(_get_attr(response, "role", ""), _get_attr(response, "content", ""))

    def _extract_usage(self, span: Span, usage: dict[str, Any]):
        return anthropic_utils.extract_usage(usage)

    def _get_model_provider(self, span: Span) -> str:
        return anthropic_utils.get_model_provider(span)

    def _get_base_url(self, **kwargs: dict[str, Any]) -> Optional[str]:
        return anthropic_utils.get_base_url(kwargs.get("instance"))

    def _extract_tools(self, tools: Optional[Any]) -> list[ToolDefinition]:
        return get_tool_definitions_from_anthropic_tools(tools)
