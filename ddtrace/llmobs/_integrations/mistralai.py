from typing import Any
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.llmobs._constants import CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import REASONING_OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.mistralai_utils import extract_provider
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import safe_load_json
from ddtrace.llmobs.types import Document
from ddtrace.llmobs.types import Message
from ddtrace.llmobs.types import ToolCall
from ddtrace.llmobs.types import ToolDefinition


# https://docs.mistral.ai/api/#tag/chat/operation/chat_completion_v1_chat_completions_post
GENERATE_METADATA_PARAMS = [
    "temperature",
    "top_p",
    "max_tokens",
    "stop",
    "random_seed",
    "response_format",
    "presence_penalty",
    "frequency_penalty",
    "n",
    "parallel_tool_calls",
    "reasoning_effort",
    "prompt_mode",
    "guardrails",
    "safe_prompt",
]

# https://docs.mistral.ai/api/#tag/embeddings/operation/embeddings_v1_embeddings_post
EMBED_METADATA_PARAMS = [
    "output_dimension",
    "output_dtype",
    "encoding_format",
]


class MistralAIIntegration(BaseLLMIntegration):
    _integration_name = "mistralai"

    def _set_base_span_tags(
        self, span: Span, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: dict[str, Any]
    ) -> None:
        if provider is not None:
            span._set_attribute("mistralai.request.provider", provider)
        if model is not None:
            span._set_attribute("mistralai.request.model", model)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        model_name = kwargs.get("model", "")
        if response is not None:
            model_name = getattr(response, "model", "") or model_name
        provider = extract_provider(kwargs)
        _annotate_llmobs_span_data(
            span,
            kind=operation,
            model_name=model_name,
            model_provider=provider,
        )
        if operation == "embedding":
            self._llmobs_set_tags_from_embedding(span, args, kwargs, response)
        elif operation == "llm":
            self._llmobs_set_tags_from_llm(span, args, kwargs, response)

    def _llmobs_set_tags_from_llm(
        self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Optional[Any]
    ) -> None:
        tools = _extract_tools(kwargs.get("tools"))
        _annotate_llmobs_span_data(
            span,
            metadata=_extract_metadata(kwargs, GENERATE_METADATA_PARAMS),
            input_messages=_extract_input_messages(kwargs),
            output_messages=_extract_output_messages(response),
            metrics=_extract_metrics(response),
            tool_definitions=tools or None,
        )

    def _llmobs_set_tags_from_embedding(
        self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Optional[Any]
    ) -> None:
        _annotate_llmobs_span_data(
            span,
            metadata=_extract_metadata(kwargs, EMBED_METADATA_PARAMS),
            input_documents=_extract_embedding_input_documents(kwargs),
            output_value=_extract_embedding_output_value(response),
            metrics=_extract_metrics(response),
        )


def _extract_metadata(kwargs: dict[str, Any], params: list[str]) -> dict[str, Any]:
    return {param: value for param in params if (value := kwargs.get(param, None)) is not None}


def _extract_thinking_text(chunk: Any) -> Optional[str]:
    thinking = _get_attr(chunk, "thinking", None)
    if not isinstance(thinking, list):
        return None
    parts = []
    for nested in thinking:
        text = _get_attr(nested, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _extract_input_messages(kwargs: dict[str, Any]) -> list[Message]:
    messages = kwargs.get("messages", []) or []
    input_messages = []
    for message in messages:
        role = _get_attr(message, "role", "") or ""
        content = _get_attr(message, "content", None)
        if isinstance(content, list):
            for chunk in content:
                thinking_text = _extract_thinking_text(chunk)
                if thinking_text is not None:
                    input_messages.append(Message(content=thinking_text, role="reasoning"))
                else:
                    input_messages.append(Message(content=str(_get_attr(chunk, "text", "")), role=role))
        else:
            msg = Message(content=str(content) if content is not None else "", role=role)
            tool_calls_raw = _get_attr(message, "tool_calls", None)
            if tool_calls_raw:
                msg["tool_calls"] = _extract_tool_calls(tool_calls_raw)
            input_messages.append(msg)
    return input_messages


def _extract_output_messages(response: Optional[Any]) -> list[Message]:
    if response is None:
        return [Message(content="", role="assistant")]
    output_messages = []
    for choice in _get_attr(response, "choices", []) or []:
        message = _get_attr(choice, "message", None)
        if message is None:
            continue
        output_messages.extend(_extract_messages_from_assistant_message(message))
    return output_messages or [Message(content="", role="assistant")]


def _extract_tool_calls(tool_calls_raw: list[ToolCall]) -> list[ToolCall]:
    tool_calls = []
    for tool_call in tool_calls_raw:
        fn = _get_attr(tool_call, "function", None)
        args = _get_attr(fn, "arguments", {})
        arguments = safe_load_json(args) if isinstance(args, str) else args
        tool_calls.append(
            ToolCall(
                name=str(_get_attr(fn, "name", "")),
                arguments=arguments,
                tool_id=str(_get_attr(tool_call, "id", "")),
                type="function",
            )
        )
    return tool_calls


def _extract_messages_from_assistant_message(message: Message) -> list[Message]:
    role = str(_get_attr(message, "role", "assistant") or "assistant")
    content = _get_attr(message, "content", "")

    reasoning_parts = []
    text_parts = []
    if isinstance(content, list):
        for chunk in content:
            thinking_text = _extract_thinking_text(chunk)
            if thinking_text is not None:
                reasoning_parts.append(thinking_text)
            else:
                text = _get_attr(chunk, "text", None)
                if isinstance(text, str):
                    text_parts.append(text)
    else:
        text_parts.append(str(content or ""))

    messages = []
    if reasoning_parts:
        messages.append(Message(content="".join(reasoning_parts), role="reasoning"))

    msg = Message(content="".join(text_parts), role=role)
    tool_calls_raw = _get_attr(message, "tool_calls", None)
    if tool_calls_raw:
        msg["tool_calls"] = _extract_tool_calls(tool_calls_raw)
    messages.append(msg)
    return messages


def _extract_embedding_input_documents(kwargs: dict[str, Any]) -> list[Document]:
    inputs = kwargs.get("inputs", "")
    if isinstance(inputs, str):
        return [Document(text=inputs)]
    if isinstance(inputs, list):
        return [Document(text=str(item)) for item in inputs]
    return [Document(text=str(inputs))]


def _extract_embedding_output_value(response: Optional[Any]) -> str:
    data = _get_attr(response, "data", []) or []
    if data:
        embedding = _get_attr(data[0], "embedding", []) or []
        return "[{} embedding(s) returned with size {}]".format(len(data), len(embedding))
    return ""


def _extract_metrics(response: Optional[Any]) -> dict[str, Any]:
    if response is None:
        return {}
    usage = _get_attr(response, "usage", None)
    if usage is None:
        return {}
    metrics = {}
    input_tokens = _get_attr(usage, "prompt_tokens", None)
    output_tokens = _get_attr(usage, "completion_tokens", None)
    total_tokens = _get_attr(usage, "total_tokens", None)
    if input_tokens is not None:
        metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens
    if output_tokens is not None:
        metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
    if total_tokens is not None:
        metrics[TOTAL_TOKENS_METRIC_KEY] = total_tokens
    cached_tokens = _get_attr(usage, "num_cached_tokens", None)
    if cached_tokens is not None:
        metrics[CACHE_READ_INPUT_TOKENS_METRIC_KEY] = cached_tokens
    completion_tokens_details = _get_attr(usage, "completion_tokens_details", None)
    reasoning_tokens = _get_attr(completion_tokens_details, "reasoning_tokens", None)
    if reasoning_tokens is not None:
        metrics[REASONING_OUTPUT_TOKENS_METRIC_KEY] = reasoning_tokens
    return metrics


def _function_declaration_to_tool_definition(function_declaration: Any) -> ToolDefinition:
    return ToolDefinition(
        name=str(_get_attr(function_declaration, "name", "") or ""),
        description=str(_get_attr(function_declaration, "description", "") or ""),
        schema=_get_attr(function_declaration, "parameters", {}) or {},
    )


def _extract_tools(tools: Optional[Any]) -> list[ToolDefinition]:
    if not tools:
        return []
    tool_definitions = []
    for tool in tools:
        fn = _get_attr(tool, "function", None) or tool
        tool_definitions.append(_function_declaration_to_tool_definition(fn))
    return tool_definitions
