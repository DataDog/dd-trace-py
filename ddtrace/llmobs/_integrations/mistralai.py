from typing import Any
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_attr
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
        _annotate_llmobs_span_data(
            span,
            kind=operation,
            model_name=model_name,
            model_provider="mistral",
        )
        if operation == "embedding":
            self._llmobs_set_tags_from_embedding(span, args, kwargs, response)
        elif operation == "llm":
            self._llmobs_set_tags_from_llm(span, args, kwargs, response)

    def _llmobs_set_tags_from_llm(self, span, args, kwargs, response):
        tools = self._extract_tools(kwargs.get("tools"))
        _annotate_llmobs_span_data(
            span,
            metadata=self._extract_metadata(kwargs, GENERATE_METADATA_PARAMS),
            input_messages=self._extract_input_messages(kwargs),
            output_messages=self._extract_output_messages(response),
            metrics=self._extract_metrics(response),
            tool_definitions=tools or None,
        )

    def _llmobs_set_tags_from_embedding(self, span, args, kwargs, response):
        _annotate_llmobs_span_data(
            span,
            metadata=self._extract_metadata(kwargs, EMBED_METADATA_PARAMS),
            input_documents=self._extract_embedding_input_documents(kwargs),
            output_value=self._extract_embedding_output_value(response),
            metrics=self._extract_metrics(response),
        )

    def _extract_metadata(self, kwargs, params):
        filtered_metadata = {}
        for param in params:
            value = kwargs.get(param, None)
            if value is not None:
                filtered_metadata[param] = value
        return filtered_metadata

    def _extract_input_messages(self, kwargs):
        messages = kwargs.get("messages", []) or []
        input_messages = []
        # message could be a str or list of message chunks
        for message in messages:
            role = _get_attr(message, "role", "") or ""
            content = _get_attr(message, "content", None)
            if isinstance(content, list):
                for chunk in content:
                    input_messages.append(Message(content=str(_get_attr(chunk, "text", "")), role=role))
            else:
                msg = Message(content=str(content) if content is not None else "", role=role)
                tool_calls_raw = _get_attr(message, "tool_calls", None)
                if tool_calls_raw:
                    msg["tool_calls"] = self._extract_tool_calls(tool_calls_raw)
                input_messages.append(msg)
        return input_messages

    def _extract_output_messages(self, response):
        if response is None:
            return [Message(content="", role="assistant")]
        output_messages = []
        for choice in _get_attr(response, "choices", []) or []:
            message = _get_attr(choice, "message", None)
            if message is None:
                continue
            output_messages.append(self._extract_message_from_assistant_message(message))
        return output_messages or [Message(content="", role="assistant")]

    def _extract_tool_calls(self, tool_calls_raw):
        tool_calls = []
        for tool_call in tool_calls_raw:
            fn = _get_attr(tool_call, "function", None)
            tool_calls.append(
                ToolCall(
                    name=str(_get_attr(fn, "name", "")),
                    arguments=_get_attr(fn, "arguments", {}),
                    tool_id=str(_get_attr(tool_call, "id", "")),
                    type="function",
                )
            )
        return tool_calls

    def _extract_message_from_assistant_message(self, message):
        role = str(_get_attr(message, "role", "assistant") or "assistant")
        content = str(_get_attr(message, "content", "") or "")
        msg = Message(content=content, role=role)
        tool_calls_raw = _get_attr(message, "tool_calls", None)
        if tool_calls_raw:
            msg["tool_calls"] = self._extract_tool_calls(tool_calls_raw)
        return msg

    def _extract_embedding_input_documents(self, kwargs):
        inputs = kwargs.get("inputs", "")
        if isinstance(inputs, str):
            return [Document(text=inputs)]
        if isinstance(inputs, list):
            return [Document(text=str(item)) for item in inputs]
        return [Document(text=str(inputs))]

    def _extract_embedding_output_value(self, response):
        data = _get_attr(response, "data", []) or []
        if data:
            embedding = _get_attr(data[0], "embedding", []) or []
            return "[{} embedding(s) returned with size {}]".format(len(data), len(embedding))
        return ""

    def _extract_metrics(self, response):
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
        return metrics

    def _function_declaration_to_tool_definition(self, function_declaration):
        return ToolDefinition(
            name=str(_get_attr(function_declaration, "name", "") or ""),
            description=str(_get_attr(function_declaration, "description", "") or ""),
            schema=_get_attr(function_declaration, "parameters", {}) or {},
        )

    def _extract_tools(self, tools):
        if not tools:
            return []
        tool_definitions = []
        for tool in tools:
            fn = _get_attr(tool, "function", None) or tool
            tool_definitions.append(self._function_declaration_to_tool_definition(fn))
        return tool_definitions
