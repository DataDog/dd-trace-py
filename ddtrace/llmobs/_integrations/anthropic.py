from typing import Any
from typing import Iterable
from typing import Optional
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import CACHE_WRITE_1H_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import CACHE_WRITE_5M_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import CACHE_WRITE_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import PROXY_REQUEST
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import UNKNOWN_MODEL_PROVIDER
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.utils import MAX_TOOL_SCHEMA_DEPTH
from ddtrace.llmobs._integrations.utils import _tool_schema_exceeds_depth
from ddtrace.llmobs._integrations.utils import _truncate_schema_to_depth
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs._utils import safe_load_json
from ddtrace.llmobs.types import Message
from ddtrace.llmobs.types import ToolCall
from ddtrace.llmobs.types import ToolDefinition
from ddtrace.llmobs.types import ToolResult
from ddtrace.trace import Span


log = get_logger(__name__)


MODEL = "anthropic.request.model"


class AnthropicIntegration(BaseLLMIntegration):
    _integration_name = "anthropic"

    def _set_base_span_tags(
        self,
        span: Span,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        **kwargs: dict[str, Any],
    ) -> None:
        """Set base level tags that should be present on all Anthropic spans (if they are not None)."""
        self._base_url = self._get_base_url(**kwargs)
        if model is not None:
            span._set_attribute(MODEL, model)

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
        if not span.error and response is not None:
            output_messages = self._extract_output_message(response)
        span_kind = "workflow" if span._get_ctx_item(PROXY_REQUEST) else "llm"

        usage = _get_attr(response, "usage", {})
        metrics = self._extract_usage(span, usage) if span_kind != "workflow" else {}

        _annotate_llmobs_span_data(
            span,
            kind=span_kind,
            model_name=span.get_tag("anthropic.request.model") or "",
            model_provider=self._get_model_provider(),
            input_messages=input_messages,
            metadata=parameters,
            output_messages=output_messages,
            metrics=metrics,
            tool_definitions=tool_definitions,
        )

    def _set_apm_shadow_tags(self, span, args, kwargs, response=None, operation=""):
        span_kind = "workflow" if span._get_ctx_item(PROXY_REQUEST) else "llm"
        usage = _get_attr(response, "usage", {})
        metrics = self._extract_usage(span, usage) if span_kind != "workflow" else {}
        model_name = span.get_tag("anthropic.request.model")
        self._apply_shadow_metrics(
            span,
            metrics,
            span_kind,
            model_name=model_name,
            model_provider=self._get_model_provider(),
        )

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
                        # Store a placeholder for potentially enormous binary image data.
                        input_messages.append(Message(content="([IMAGE DETECTED])", role=str(role)))

                    elif content_type == "thinking":
                        thinking_text = _get_attr(block, "thinking", "")
                        input_messages.append(Message(content=str(thinking_text), role="reasoning"))

                    elif "tool_use" in (content_type or ""):
                        text = _get_attr(block, "text", None)
                        input_data = _get_attr(block, "input", {})
                        if isinstance(input_data, str):
                            input_data = safe_load_json(input_data)
                        tool_call_info = ToolCall(
                            name=str(_get_attr(block, "name", "")),
                            arguments=input_data,
                            tool_id=str(_get_attr(block, "id", "")),
                            type=str(_get_attr(block, "type", "")),
                        )
                        if text is None:
                            text = ""
                        input_messages.append(Message(content=str(text), role=str(role), tool_calls=[tool_call_info]))

                    elif "tool_result" in (content_type or ""):
                        content = _get_attr(block, "content", None)
                        formatted_content = self._format_tool_result_content(content)
                        tool_result_info = ToolResult(
                            result=formatted_content,
                            tool_id=str(_get_attr(block, "tool_use_id", "")),
                            type="tool_result",
                        )
                        input_messages.append(Message(content="", role=str(role), tool_results=[tool_result_info]))
                    else:
                        input_messages.append(Message(content=str(block), role=str(role)))

        return input_messages

    def _format_tool_result_content(self, content) -> str:
        if isinstance(content, str):
            return content
        elif isinstance(content, dict):
            return safe_json(content)
        elif isinstance(content, Iterable):
            formatted_content = []
            for tool_result_block in content:
                if _get_attr(tool_result_block, "text", "") != "":
                    formatted_content.append(_get_attr(tool_result_block, "text", ""))
                elif _get_attr(tool_result_block, "type", None) == "image":
                    # Store a placeholder for potentially enormous binary image data.
                    formatted_content.append("([IMAGE DETECTED])")
            return ",".join(formatted_content)
        return str(content)

    def _extract_output_message(self, response) -> list[Message]:
        """Extract output messages from the stored response."""
        output_messages: list[Message] = []
        content = _get_attr(response, "content", "")
        role = _get_attr(response, "role", "")

        if isinstance(content, str):
            return [Message(content=content, role=str(role))]

        elif isinstance(content, list):
            for completion in content:
                completion_type = _get_attr(completion, "type", "") or ""
                if completion_type == "thinking":
                    thinking_text = _get_attr(completion, "thinking", "")
                    output_messages.append(Message(content=str(thinking_text), role="reasoning"))
                    continue
                text = _get_attr(completion, "text", None)
                output_message = Message(content=str(text) if text else "", role=str(role))
                if "tool_use" in completion_type:
                    input_data = _get_attr(completion, "input", {})
                    if isinstance(input_data, str):
                        input_data = safe_load_json(input_data)
                    tool_call_info = ToolCall(
                        name=str(_get_attr(completion, "name", "")),
                        arguments=input_data,
                        tool_id=str(_get_attr(completion, "id", "")),
                        type=str(completion_type),
                    )
                    output_message["tool_calls"] = [tool_call_info]
                if "tool_result" in completion_type:
                    result = _get_attr(completion, "content", {})
                    if hasattr(result, "model_dump") and callable(result.model_dump):
                        result = result.model_dump()
                    formatted_result = self._format_tool_result_content(result)
                    tool_result_info = ToolResult(
                        result=formatted_result,
                        tool_id=str(_get_attr(completion, "tool_use_id", "")),
                        type="tool_result",
                    )
                    output_message["tool_results"] = [tool_result_info]
                output_messages.append(output_message)
        return output_messages

    def _extract_usage(self, span: Span, usage: dict[str, Any]):
        if not usage:
            return
        input_tokens = _get_attr(usage, "input_tokens", None)
        output_tokens = _get_attr(usage, "output_tokens", None)
        cache_write_tokens = _get_attr(usage, "cache_creation_input_tokens", None)
        cache_read_tokens = _get_attr(usage, "cache_read_input_tokens", None)

        metrics = {}

        # `input_tokens` in the returned usage is the number of non-cached tokens. We normalize it to mean
        # the total tokens sent to the model to be consistent with other model providers.
        metrics[INPUT_TOKENS_METRIC_KEY] = (input_tokens or 0) + (cache_write_tokens or 0) + (cache_read_tokens or 0)

        if output_tokens is not None:
            metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
        if INPUT_TOKENS_METRIC_KEY in metrics and output_tokens is not None:
            metrics[TOTAL_TOKENS_METRIC_KEY] = metrics[INPUT_TOKENS_METRIC_KEY] + output_tokens

        if cache_write_tokens is not None:
            metrics[CACHE_WRITE_INPUT_TOKENS_METRIC_KEY] = cache_write_tokens
            cache_creation_breakdown = _get_attr(usage, "cache_creation", {})
            cache_creation_1h_tokens = _get_attr(cache_creation_breakdown, "ephemeral_1h_input_tokens", None)
            cache_creation_5m_tokens = _get_attr(cache_creation_breakdown, "ephemeral_5m_input_tokens", None)
            if cache_creation_1h_tokens is None and cache_creation_5m_tokens is None:
                # Legacy API response without cache_creation breakdown; assume all writes are 5m TTL.
                cache_creation_5m_tokens = cache_write_tokens
            metrics[CACHE_WRITE_1H_INPUT_TOKENS_METRIC_KEY] = cache_creation_1h_tokens or 0
            metrics[CACHE_WRITE_5M_INPUT_TOKENS_METRIC_KEY] = cache_creation_5m_tokens or 0

        if cache_read_tokens is not None:
            metrics[CACHE_READ_INPUT_TOKENS_METRIC_KEY] = cache_read_tokens
        return metrics

    def _get_model_provider(self) -> str:
        """Return the model provider based on the base_url.
        Returns "anthropic" for default clients or when the base_url contains "anthropic".
        Returns "unknown" when a custom base_url is set that doesn't contain "anthropic".
        """
        if not self._base_url or "anthropic" in self._base_url.lower():
            return "anthropic"
        return UNKNOWN_MODEL_PROVIDER

    def _get_base_url(self, **kwargs: dict[str, Any]) -> Optional[str]:
        instance = kwargs.get("instance")
        client = getattr(instance, "_client", None)
        base_url = getattr(client, "_base_url", None) if client else None
        return str(base_url) if base_url else None

    def _extract_tools(self, tools: Optional[Any]) -> list[ToolDefinition]:
        if not tools:
            return []

        tool_definitions = []
        for tool in tools:
            is_deferred = bool(_get_attr(tool, "defer_loading", False))
            tool_def = ToolDefinition(
                name=tool.get("name", ""),
                description="" if is_deferred else tool.get("description", ""),
                schema={} if is_deferred else tool.get("input_schema", {}),
            )
            schema = tool_def.get("schema") or {}
            if _tool_schema_exceeds_depth(tool_def.get("name") or "", schema):
                tool_def["schema"] = _truncate_schema_to_depth(schema, MAX_TOOL_SCHEMA_DEPTH)
            tool_definitions.append(tool_def)
        return tool_definitions
