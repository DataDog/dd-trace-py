from dataclasses import dataclass
import json
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

from ddtrace._trace.span import Span
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._constants import DISPATCH_ON_LLM_TOOL_CHOICE
from ddtrace.llmobs._constants import DISPATCH_ON_TOOL_CALL_OUTPUT_USED
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import OAI_HANDOFF_TOOL_ARG
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import load_data_value
from ddtrace.llmobs._utils import safe_json


try:
    from tiktoken import encoding_for_model

    tiktoken_available = True
except ModuleNotFoundError:
    tiktoken_available = False

logger = get_logger(__name__)

COMMON_METADATA_KEYS = (
    "stream",
    "temperature",
    "top_p",
    "user",
)
OPENAI_METADATA_RESPONSE_KEYS = (
    "background",
    "include",
    "max_output_tokens",
    "max_tool_calls",
    "parallel_tool_calls",
    "previous_response_id",
    "prompt",
    "reasoning",
    "service_tier",
    "store",
    "text",
    "tool_choice",
    "tools",
    "top_logprobs",
    "truncation",
)
OPENAI_METADATA_CHAT_KEYS = (
    "audio",
    "frequency_penalty",
    "function_call",
    "logit_bias",
    "logprobs",
    "max_completion_tokens",
    "max_tokens",
    "modalities",
    "n",
    "parallel_tool_calls",
    "prediction",
    "presence_penalty",
    "reasoning_effort",
    "response_format",
    "seed",
    "service_tier",
    "stop",
    "store",
    "stream_options",
    "tool_choice",
    "top_logprobs",
    "web_search_options",
)
OPENAI_METADATA_COMPLETION_KEYS = (
    "best_of",
    "echo",
    "frequency_penalty",
    "logit_bias",
    "logprobs",
    "max_tokens",
    "n",
    "presence_penalty",
    "seed",
    "stop",
    "stream_options",
    "suffix",
)

LITELLM_METADATA_CHAT_KEYS = (
    "timeout",
    "n",
    "stream_options",
    "stop",
    "max_completion_tokens",
    "max_tokens",
    "modalities",
    "prediction",
    "presence_penalty",
    "frequency_penalty",
    "logit_bias",
    "response_format",
    "seed",
    "tool_choice",
    "parallel_tool_calls",
    "logprobs",
    "top_logprobs",
    "deployment_id",
    "reasoning_effort",
    "base_url",
    "api_base",
    "api_version",
    "model_list",
)
LITELLM_METADATA_COMPLETION_KEYS = (
    "best_of",
    "echo",
    "frequency_penalty",
    "logit_bias",
    "logprobs",
    "max_tokens",
    "n",
    "presence_penalty",
    "stop",
    "stream_options",
    "suffix",
    "api_base",
    "api_version",
    "model_list",
    "custom_llm_provider",
)


def get_llmobs_metrics_tags(integration_name, span):
    usage = {}

    # check for both prompt / completion or input / output tokens
    input_tokens = span.get_metric("%s.response.usage.prompt_tokens" % integration_name) or span.get_metric(
        "%s.response.usage.input_tokens" % integration_name
    )
    output_tokens = span.get_metric("%s.response.usage.completion_tokens" % integration_name) or span.get_metric(
        "%s.response.usage.output_tokens" % integration_name
    )
    total_tokens = None
    if input_tokens and output_tokens:
        total_tokens = input_tokens + output_tokens
    total_tokens = span.get_metric("%s.response.usage.total_tokens" % integration_name) or total_tokens

    if input_tokens is not None:
        usage[INPUT_TOKENS_METRIC_KEY] = input_tokens
    if output_tokens is not None:
        usage[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
    if total_tokens is not None:
        usage[TOTAL_TOKENS_METRIC_KEY] = total_tokens
    return usage


def parse_llmobs_metric_args(metrics):
    usage = {}
    input_tokens = _get_attr(metrics, "prompt_tokens", None)
    output_tokens = _get_attr(metrics, "completion_tokens", None)
    total_tokens = _get_attr(metrics, "total_tokens", None)
    if input_tokens is not None:
        usage[INPUT_TOKENS_METRIC_KEY] = input_tokens
    if output_tokens is not None:
        usage[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
    if total_tokens is not None:
        usage[TOTAL_TOKENS_METRIC_KEY] = total_tokens
    return usage


LANGCHAIN_ROLE_MAPPING = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
}


def format_langchain_io(
    messages,
):
    """
    Formats input and output messages for serialization to JSON.
    Specifically, makes sure that any schema messages are converted to strings appropriately.
    """
    if isinstance(messages, dict):
        formatted = {}
        for key, value in messages.items():
            formatted[key] = format_langchain_io(value)
        return formatted
    if isinstance(messages, list):
        return [format_langchain_io(message) for message in messages]
    return get_content_from_langchain_message(messages)


def get_content_from_langchain_message(message) -> Union[str, Tuple[str, str]]:
    """
    Attempts to extract the content and role from a message (AIMessage, HumanMessage, SystemMessage) object.
    """
    if isinstance(message, str):
        return message
    try:
        content = getattr(message, "__dict__", {}).get("content", str(message))
        role = getattr(message, "role", LANGCHAIN_ROLE_MAPPING.get(getattr(message, "type"), ""))
        return (role, content) if role else content
    except AttributeError:
        return str(message)


def get_messages_from_converse_content(role: str, content: List[Dict[str, Any]]):
    """
    Extracts out a list of messages from a converse `content` field.

    `content` is a list of `ContentBlock` objects. Each `ContentBlock` object is a union type
    of `text`, `toolUse`, amd more content types. We only support extracting out `text` and `toolUse`.

    For more info, see `ContentBlock` spec
    https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ContentBlock.html
    """
    if not content or not isinstance(content, list) or not isinstance(content[0], dict):
        return []
    messages: List[Dict[str, Union[str, List[Dict[str, Any]]]]] = []
    content_blocks = []
    tool_calls_info = []
    tool_messages: List[Dict[str, Any]] = []
    unsupported_content_messages: List[Dict[str, Union[str, List[Dict[str, Any]]]]] = []
    for content_block in content:
        if content_block.get("text") and isinstance(content_block.get("text"), str):
            content_blocks.append(content_block.get("text", ""))
        elif content_block.get("toolUse") and isinstance(content_block.get("toolUse"), dict):
            toolUse = content_block.get("toolUse", {})
            tool_calls_info.append(
                {
                    "name": str(toolUse.get("name", "")),
                    "arguments": toolUse.get("input", {}),
                    "tool_id": str(toolUse.get("toolUseId", "")),
                }
            )
        elif content_block.get("toolResult") and isinstance(content_block.get("toolResult"), dict):
            tool_message: Dict[str, Any] = content_block.get("toolResult", {})
            tool_message_contents: List[Dict[str, Any]] = tool_message.get("content", [])
            tool_message_id: str = tool_message.get("toolUseId", "")

            for tool_message_content in tool_message_contents:
                tool_message_content_text: Optional[str] = tool_message_content.get("text")
                tool_message_content_json: Optional[Dict[str, Any]] = tool_message_content.get("json")

                tool_messages.append(
                    {
                        "content": tool_message_content_text
                        or (tool_message_content_json and safe_json(tool_message_content_json))
                        or f"[Unsupported content type(s): {','.join(tool_message_content.keys())}]",
                        "role": "tool",
                        "tool_id": tool_message_id,
                    }
                )
        else:
            content_type = ",".join(content_block.keys())
            unsupported_content_messages.append(
                {"content": "[Unsupported content type: {}]".format(content_type), "role": role}
            )
    message = {}  # type: dict[str, Union[str, list[dict[str, dict]]]]
    if tool_calls_info:
        message.update({"tool_calls": tool_calls_info})
    if content_blocks:
        message.update({"content": " ".join(content_blocks), "role": role})
    if message:
        messages.append(message)
    if unsupported_content_messages:
        messages.extend(unsupported_content_messages)
    if tool_messages:
        messages.extend(tool_messages)
    return messages


def openai_set_meta_tags_from_completion(
    span: Span, kwargs: Dict[str, Any], completions: Any, integration_name: str = "openai"
) -> None:
    """Extract prompt/response tags from a completion and set them as temporary "_ml_obs.meta.*" tags."""
    prompt = kwargs.get("prompt", "")
    if isinstance(prompt, str):
        prompt = [prompt]
    parameters = get_metadata_from_kwargs(kwargs, integration_name, "completion")
    output_messages = [{"content": ""}]
    if not span.error and completions:
        choices = getattr(completions, "choices", completions)
        output_messages = [{"content": _get_attr(choice, "text", "")} for choice in choices]
    span._set_ctx_items(
        {
            INPUT_MESSAGES: [{"content": str(p)} for p in prompt],
            METADATA: parameters,
            OUTPUT_MESSAGES: output_messages,
        }
    )


def openai_set_meta_tags_from_chat(
    span: Span, kwargs: Dict[str, Any], messages: Optional[Any], integration_name: str = "openai"
) -> None:
    """Extract prompt/response tags from a chat completion and set them as temporary "_ml_obs.meta.*" tags."""
    input_messages = []
    for m in kwargs.get("messages", []):
        processed_message = {
            "content": str(_get_attr(m, "content", "")),
            "role": str(_get_attr(m, "role", "")),
        }  # type: dict[str, Union[str, list[dict[str, dict]]]]
        tool_call_id = _get_attr(m, "tool_call_id", None)
        if tool_call_id:
            core.dispatch(DISPATCH_ON_TOOL_CALL_OUTPUT_USED, (tool_call_id, span))
            processed_message["tool_id"] = tool_call_id
        tool_calls = _get_attr(m, "tool_calls", [])
        if tool_calls:
            processed_message["tool_calls"] = [
                {
                    "name": _get_attr(_get_attr(tool_call, "function", {}), "name", ""),
                    "arguments": _get_attr(_get_attr(tool_call, "function", {}), "arguments", {}),
                    "tool_id": _get_attr(tool_call, "id", ""),
                    "type": _get_attr(tool_call, "type", ""),
                }
                for tool_call in tool_calls
            ]
        input_messages.append(processed_message)
    parameters = get_metadata_from_kwargs(kwargs, integration_name, "chat")
    span._set_ctx_items({INPUT_MESSAGES: input_messages, METADATA: parameters})

    if span.error or not messages:
        span._set_ctx_item(OUTPUT_MESSAGES, [{"content": ""}])
        return
    if isinstance(messages, list):  # streamed response
        role = ""
        output_messages = []
        for streamed_message in messages:
            # litellm roles appear only on the first choice, so store it to be used for all choices
            role = streamed_message.get("role", "") or role
            message = {"content": streamed_message.get("content", ""), "role": role}
            tool_calls = streamed_message.get("tool_calls", [])
            if tool_calls:
                message["tool_calls"] = [
                    {
                        "name": tool_call.get("name", ""),
                        "arguments": json.loads(tool_call.get("arguments", "")),
                        "tool_id": tool_call.get("tool_id", ""),
                        "type": tool_call.get("type", ""),
                    }
                    for tool_call in tool_calls
                ]
            output_messages.append(message)
        span._set_ctx_item(OUTPUT_MESSAGES, output_messages)
        return
    choices = _get_attr(messages, "choices", [])
    output_messages = []
    for idx, choice in enumerate(choices):
        tool_calls_info = []
        choice_message = _get_attr(choice, "message", {})
        role = _get_attr(choice_message, "role", "")
        content = _get_attr(choice_message, "content", "") or ""
        function_call = _get_attr(choice_message, "function_call", None)
        if function_call:
            function_name = _get_attr(function_call, "name", "")
            arguments = json.loads(_get_attr(function_call, "arguments", ""))
            function_call_info = {"name": function_name, "arguments": arguments}
            output_messages.append({"content": content, "role": role, "tool_calls": [function_call_info]})
            continue
        tool_calls = _get_attr(choice_message, "tool_calls", []) or []
        for tool_call in tool_calls:
            tool_args = getattr(tool_call.function, "arguments", "")
            tool_name = getattr(tool_call.function, "name", "")
            tool_id = getattr(tool_call, "id", "")
            tool_call_info = {
                "name": tool_name,
                "arguments": json.loads(tool_args),
                "tool_id": tool_id,
                "type": "function",
            }
            tool_calls_info.append(tool_call_info)
            core.dispatch(
                DISPATCH_ON_LLM_TOOL_CHOICE,
                (
                    tool_id,
                    tool_name,
                    tool_args,
                    {
                        "trace_id": format_trace_id(span.trace_id),
                        "span_id": str(span.span_id),
                    },
                ),
            )
        if tool_calls_info:
            output_messages.append({"content": content, "role": role, "tool_calls": tool_calls_info})
            continue
        output_messages.append({"content": content, "role": role})
    span._set_ctx_item(OUTPUT_MESSAGES, output_messages)


def get_metadata_from_kwargs(
    kwargs: Dict[str, Any], integration_name: str = "openai", operation: str = "chat"
) -> Dict[str, Any]:
    metadata = {}
    keys_to_include: Tuple[str, ...] = COMMON_METADATA_KEYS
    if integration_name == "openai":
        keys_to_include += OPENAI_METADATA_CHAT_KEYS if operation == "chat" else OPENAI_METADATA_COMPLETION_KEYS
    elif integration_name == "litellm":
        keys_to_include += LITELLM_METADATA_CHAT_KEYS if operation == "chat" else LITELLM_METADATA_COMPLETION_KEYS
    metadata = {k: v for k, v in kwargs.items() if k in keys_to_include}
    return metadata


def openai_get_input_messages_from_response_input(
    messages: Optional[Union[str, List[Dict[str, Any]]]]
) -> List[Dict[str, Any]]:
    """Parses the input to openai responses api into a list of input messages

    Args:
        messages: the input to openai responses api

    Returns:
        - A list of processed messages
    """
    processed: List[Dict[str, Any]] = []

    if not messages:
        return processed

    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]

    for item in messages:
        processed_item: Dict[str, Union[str, List[Dict[str, str]]]] = {}
        # Handle regular message
        if "content" in item and "role" in item:
            processed_item_content = ""
            if isinstance(item["content"], list):
                for content in item["content"]:
                    processed_item_content += str(content.get("text", "") or "")
                    processed_item_content += str(content.get("refusal", "") or "")
            else:
                processed_item_content = item["content"]
            if processed_item_content:
                processed_item["content"] = str(processed_item_content)
                processed_item["role"] = item["role"]
        elif "call_id" in item and "arguments" in item:
            # Process `ResponseFunctionToolCallParam` type from input messages
            try:
                arguments = json.loads(item["arguments"])
            except json.JSONDecodeError:
                arguments = {"value": str(item["arguments"])}
            processed_item["tool_calls"] = [
                {
                    "tool_id": item["call_id"],
                    "arguments": arguments,
                    "name": item.get("name", ""),
                    "type": item.get("type", "function_call"),
                }
            ]
        elif "call_id" in item and "output" in item:
            # Process `FunctionCallOutput` type from input messages
            output = item["output"]

            if not isinstance(output, str):
                output = safe_json(output)

            processed_item.update(
                {
                    "role": "tool",
                    "content": output,
                    "tool_id": item["call_id"],
                }
            )
        if processed_item:
            processed.append(processed_item)

    return processed


def openai_get_output_messages_from_response(response: Optional[Any]) -> List[Dict[str, Any]]:
    """
    Parses the output to openai responses api into a list of output messages

    Args:
        response: An OpenAI response object or dictionary containing output messages

    Returns:
        - A list of processed messages
    """
    if not response:
        return []

    messages = _get_attr(response, "output", [])
    if not messages:
        return []

    processed: List[Dict[str, Any]] = []

    for item in messages:
        message = {}
        message_type = _get_attr(item, "type", "")

        if message_type == "message":
            text = ""
            for content in _get_attr(item, "content", []):
                text += str(_get_attr(content, "text", "") or "")
                text += str(_get_attr(content, "refusal", "") or "")
            message.update({"role": _get_attr(item, "role", "assistant"), "content": text})
        elif message_type == "reasoning":
            message.update(
                {
                    "role": "reasoning",
                    "content": safe_json(
                        {
                            "summary": _get_attr(item, "summary", ""),
                            "encrypted_content": _get_attr(item, "encrypted_content", ""),
                            "id": _get_attr(item, "id", ""),
                        }
                    ),
                }
            )
        elif message_type == "function_call":
            arguments = _get_attr(item, "arguments", "{}")
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"value": str(arguments)}
            message.update(
                {
                    "tool_calls": [
                        {
                            "tool_id": _get_attr(item, "call_id", ""),
                            "arguments": arguments,
                            "name": _get_attr(item, "name", ""),
                            "type": _get_attr(item, "type", "function"),
                        }
                    ]
                }
            )
        else:
            message.update({"role": "assistant", "content": "Unsupported content type: {}".format(message_type)})

        processed.append(message)

    return processed


def openai_get_metadata_from_response(
    response: Optional[Any], kwargs: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    metadata = {}

    if kwargs:
        metadata.update({k: v for k, v in kwargs.items() if k in OPENAI_METADATA_RESPONSE_KEYS + COMMON_METADATA_KEYS})

    if not response:
        return metadata

    # Add metadata from response
    for field in ["temperature", "max_output_tokens", "top_p", "tools", "tool_choice", "truncation", "text", "user"]:
        value = getattr(response, field, None)
        if value is not None:
            metadata[field] = load_data_value(value)

    usage = getattr(response, "usage", None)
    output_tokens_details = getattr(usage, "output_tokens_details", None)
    reasoning_tokens = getattr(output_tokens_details, "reasoning_tokens", 0)
    metadata["reasoning_tokens"] = reasoning_tokens

    return metadata


def openai_set_meta_tags_from_response(span: Span, kwargs: Dict[str, Any], response: Optional[Any]) -> None:
    """Extract input/output tags from response and set them as temporary "_ml_obs.meta.*" tags."""
    input_data = kwargs.get("input", [])
    input_messages = openai_get_input_messages_from_response_input(input_data)

    if "instructions" in kwargs:
        input_messages.insert(0, {"content": str(kwargs["instructions"]), "role": "system"})

    span._set_ctx_items(
        {
            INPUT_MESSAGES: input_messages,
            METADATA: openai_get_metadata_from_response(response, kwargs),
        }
    )

    if span.error or not response:
        span._set_ctx_item(OUTPUT_MESSAGES, [{"content": ""}])
        return

    # The response potentially contains enriched metadata (ex. tool calls) not in the original request
    metadata = span._get_ctx_item(METADATA) or {}
    metadata.update(openai_get_metadata_from_response(response))
    span._set_ctx_item(METADATA, metadata)
    output_messages = openai_get_output_messages_from_response(response)
    span._set_ctx_item(OUTPUT_MESSAGES, output_messages)


def openai_construct_completion_from_streamed_chunks(streamed_chunks: List[Any]) -> Dict[str, str]:
    """Constructs a completion dictionary of form {"text": "...", "finish_reason": "..."} from streamed chunks."""
    if not streamed_chunks:
        return {"text": ""}
    completion = {"text": "".join(c.text for c in streamed_chunks if getattr(c, "text", None))}
    if getattr(streamed_chunks[-1], "finish_reason", None):
        completion["finish_reason"] = streamed_chunks[-1].finish_reason
    if hasattr(streamed_chunks[0], "usage"):
        completion["usage"] = streamed_chunks[0].usage
    return completion


def openai_construct_tool_call_from_streamed_chunk(stored_tool_calls, tool_call_chunk=None, function_call_chunk=None):
    """Builds a tool_call dictionary from streamed function_call/tool_call chunks."""
    if function_call_chunk:
        if not stored_tool_calls:
            stored_tool_calls.append({"name": getattr(function_call_chunk, "name", ""), "arguments": ""})
        stored_tool_calls[0]["arguments"] += getattr(function_call_chunk, "arguments", "")
        return
    if not tool_call_chunk:
        return
    tool_call_idx = getattr(tool_call_chunk, "index", None)
    tool_id = getattr(tool_call_chunk, "id", None)
    tool_type = getattr(tool_call_chunk, "type", None)
    function_call = getattr(tool_call_chunk, "function", None)
    function_name = getattr(function_call, "name", "")
    # Find tool call index in tool_calls list, as it may potentially arrive unordered (i.e. index 2 before 0)
    list_idx = next(
        (idx for idx, tool_call in enumerate(stored_tool_calls) if tool_call["index"] == tool_call_idx),
        None,
    )
    if list_idx is None:
        stored_tool_calls.append(
            {"name": function_name, "arguments": "", "index": tool_call_idx, "tool_id": tool_id, "type": tool_type}
        )
        list_idx = -1
    stored_tool_calls[list_idx]["arguments"] += getattr(function_call, "arguments", "")


def openai_construct_message_from_streamed_chunks(streamed_chunks: List[Any]) -> Dict[str, Any]:
    """Constructs a chat completion message dictionary from streamed chunks.
    The resulting message dictionary is of form:
    {"content": "...", "role": "...", "tool_calls": [...], "finish_reason": "..."}
    """
    message: Dict[str, Any] = {"content": "", "tool_calls": []}
    for chunk in streamed_chunks:
        if _get_attr(chunk, "usage", None):
            message["usage"] = chunk.usage
        if not _get_attr(chunk, "delta", None):
            continue
        if _get_attr(chunk, "index", None) and not message.get("index"):
            message["index"] = chunk.index
        if _get_attr(chunk.delta, "role", None) and not message.get("role"):
            message["role"] = chunk.delta.role
        if _get_attr(chunk, "finish_reason", None) and not message.get("finish_reason"):
            message["finish_reason"] = chunk.finish_reason
        chunk_content = _get_attr(chunk.delta, "content", "")
        if chunk_content:
            message["content"] += chunk_content
            continue
        function_call = _get_attr(chunk.delta, "function_call", None)
        if function_call:
            openai_construct_tool_call_from_streamed_chunk(message["tool_calls"], function_call_chunk=function_call)
        tool_calls = _get_attr(chunk.delta, "tool_calls", None)
        if not tool_calls:
            continue
        for tool_call in tool_calls:
            openai_construct_tool_call_from_streamed_chunk(message["tool_calls"], tool_call_chunk=tool_call)
    if message["tool_calls"]:
        message["tool_calls"].sort(key=lambda x: x.get("index", 0))
    else:
        message.pop("tool_calls", None)
    message["content"] = message["content"].strip()
    return message


def update_proxy_workflow_input_output_value(span: Span, span_kind: str = ""):
    """Helper to update the input and output value for workflow spans."""
    if span_kind != "workflow":
        return
    input_messages = span._get_ctx_item(INPUT_MESSAGES)
    output_messages = span._get_ctx_item(OUTPUT_MESSAGES)
    if input_messages:
        span._set_ctx_item(INPUT_VALUE, input_messages)
    if output_messages:
        span._set_ctx_item(OUTPUT_VALUE, output_messages)


class OaiSpanAdapter:
    """Adapter for Oai Agents SDK Span objects that the llmobs integration code will use.
    This is to consolidate the code where we access oai library types which provides a clear starting point for
    troubleshooting data issues.
    It is also handy for providing defaults when we bump into missing data or unexpected data shapes.
    """

    def __init__(self, oai_span):
        self._raw_oai_span = oai_span  # openai span data type

    @property
    def span_id(self) -> str:
        """Get the span ID."""
        return self._raw_oai_span.span_id

    @property
    def trace_id(self) -> str:
        """Get the trace ID."""
        return self._raw_oai_span.trace_id

    @property
    def name(self) -> str:
        """Get the span name."""
        if hasattr(self._raw_oai_span, "span_data") and hasattr(self._raw_oai_span.span_data, "name"):
            return self._raw_oai_span.span_data.name
        return "openai_agents.{}".format(self.span_type.lower())

    @property
    def span_type(self) -> str:
        """Get the span type."""
        if hasattr(self._raw_oai_span, "span_data") and hasattr(self._raw_oai_span.span_data, "type"):
            return self._raw_oai_span.span_data.type
        return ""

    @property
    def llmobs_span_kind(self) -> Optional[str]:
        kind_mapping = {
            "function": "tool",
            "agent": "agent",
            "handoff": "tool",
            "response": "llm",
            "guardrail": "task",
            "custom": "task",
        }
        return kind_mapping.get(self.span_type)

    @property
    def input(self) -> Union[str, List[Any]]:
        """Get the span data input."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return ""
        return getattr(self._raw_oai_span.span_data, "input", "")

    @property
    def output(self) -> Optional[str]:
        """Get the span data output."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return None
        return getattr(self._raw_oai_span.span_data, "output", None)

    @property
    def response(self) -> Optional[Any]:
        """Get the span data response."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return None
        return getattr(self._raw_oai_span.span_data, "response", None)

    @property
    def from_agent(self) -> str:
        """Get the span data from_agent."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return ""
        return getattr(self._raw_oai_span.span_data, "from_agent", "")

    @property
    def to_agent(self) -> str:
        """Get the span data to_agent."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return ""
        return getattr(self._raw_oai_span.span_data, "to_agent", "")

    @property
    def handoffs(self) -> Optional[List[str]]:
        """Get the span data handoffs."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return None
        return getattr(self._raw_oai_span.span_data, "handoffs", None)

    @property
    def tools(self) -> Optional[List[str]]:
        """Get the span data tools."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return None
        return getattr(self._raw_oai_span.span_data, "tools", None)

    @property
    def data(self) -> Any:
        """Get the span data for custom spans."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return None
        return getattr(self._raw_oai_span.span_data, "data", None)

    @property
    def formatted_custom_data(self) -> Dict[str, Any]:
        """Get the custom span data in a formatted way."""
        data = self.data
        if not data:
            return {}
        return load_data_value(data)

    @property
    def response_output_text(self) -> str:
        """Get the output text from the response."""
        if not self.response:
            return ""
        return self.response.output_text

    @property
    def response_system_instructions(self) -> Optional[str]:
        """Get the system instructions from the response."""
        if not self.response:
            return None
        return getattr(self.response, "instructions", None)

    @property
    def llmobs_model_name(self) -> Optional[str]:
        """Get the model name formatted for LLMObs."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return None

        if self.span_type == "response" and self.response:
            if hasattr(self.response, "model"):
                return self.response.model
        return None

    @property
    def llmobs_metrics(self) -> Optional[Dict[str, Any]]:
        """Get metrics from the span data formatted for LLMObs."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return None

        metrics = {}

        if self.span_type == "response" and self.response and hasattr(self.response, "usage"):
            usage = self.response.usage
            if hasattr(usage, "input_tokens"):
                metrics["input_tokens"] = usage.input_tokens
            if hasattr(usage, "output_tokens"):
                metrics["output_tokens"] = usage.output_tokens
            if hasattr(usage, "total_tokens"):
                metrics["total_tokens"] = usage.total_tokens

        return metrics if metrics else None

    @property
    def llmobs_metadata(self) -> Optional[Dict[str, Any]]:
        """Get metadata from the span data formatted for LLMObs."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return None

        metadata = {}

        if self.span_type == "response" and self.response:
            for field in ["temperature", "max_output_tokens", "top_p", "tools", "tool_choice", "truncation"]:
                if hasattr(self.response, field):
                    value = getattr(self.response, field)
                    if value is not None:
                        metadata[field] = load_data_value(value)

            if hasattr(self.response, "text") and self.response.text:
                metadata["text"] = load_data_value(self.response.text)

            if hasattr(self.response, "usage") and hasattr(self.response.usage, "output_tokens_details"):
                metadata["reasoning_tokens"] = self.response.usage.output_tokens_details.reasoning_tokens

        if self.span_type == "custom" and hasattr(self._raw_oai_span.span_data, "data"):
            custom_data = getattr(self._raw_oai_span.span_data, "data", None)
            if custom_data:
                metadata.update(custom_data)

        return metadata if metadata else None

    @property
    def error(self) -> Optional[Dict[str, Any]]:
        """Get span error if it exists."""
        return self._raw_oai_span.error if hasattr(self._raw_oai_span, "error") else None

    def get_error_message(self) -> Optional[str]:
        """Get the error message if an error exists."""
        if not self.error:
            return None
        return self.error.get("message")

    def get_error_data(self) -> Optional[Dict[str, Any]]:
        """Get the error data if an error exists."""
        if not self.error:
            return None
        return self.error.get("data")

    def llmobs_input_messages(self) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Returns processed input messages for LLM Obs LLM spans.

        Returns:
            - A list of processed messages
            - A list of tool call IDs for span linking purposes
        """
        messages = self.input
        processed: List[Dict[str, Any]] = []
        tool_call_ids: List[str] = []

        if self.response_system_instructions:
            processed.append({"role": "system", "content": self.response_system_instructions})

        if not messages:
            return processed, tool_call_ids

        if isinstance(messages, str):
            return [{"content": messages, "role": "user"}], tool_call_ids

        for item in messages:
            processed_item: Dict[str, Union[str, List[Dict[str, str]]]] = {}
            # Handle regular message
            if "content" in item and "role" in item:
                processed_item_content = ""
                if isinstance(item["content"], list):
                    for content in item["content"]:
                        processed_item_content += content.get("text", "")
                        processed_item_content += content.get("refusal", "")
                else:
                    processed_item_content = item["content"]
                if processed_item_content:
                    processed_item["content"] = processed_item_content
                    processed_item["role"] = item["role"]
            elif "call_id" in item and "arguments" in item:
                """
                Process `ResponseFunctionToolCallParam` type from input messages
                """
                try:
                    arguments = json.loads(item["arguments"])
                except json.JSONDecodeError:
                    arguments = item["arguments"]
                processed_item["tool_calls"] = [
                    {
                        "tool_id": item["call_id"],
                        "arguments": arguments,
                        "name": item.get("name", ""),
                        "type": item.get("type", "function_call"),
                    }
                ]
            elif "call_id" in item and "output" in item:
                """
                Process `FunctionCallOutput` type from input messages
                """
                output = item["output"]

                if isinstance(output, str):
                    try:
                        output = json.loads(output)
                    except json.JSONDecodeError:
                        output = {"output": output}
                tool_call_ids.append(item["call_id"])
                processed_item["role"] = "tool"
                processed_item["content"] = item["output"]
                processed_item["tool_id"] = item["call_id"]
            if processed_item:
                processed.append(processed_item)

        return processed, tool_call_ids

    def llmobs_output_messages(self) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str, str]]]:
        """Returns processed output messages for LLM Obs LLM spans.

        Returns:
            - A list of processed messages
            - A list of tool call data (name, id, args) for span linking purposes
        """
        if not self.response or not self.response.output:
            return [], []

        messages: List[Any] = self.response.output
        processed: List[Dict[str, Any]] = []
        tool_call_outputs: List[Tuple[str, str, str]] = []
        if not messages:
            return processed, tool_call_outputs

        if not isinstance(messages, list):
            messages = [messages]

        for item in messages:
            message = {}
            # Handle content-based messages
            if hasattr(item, "content"):
                text = ""
                for content in item.content:
                    if hasattr(content, "text") or hasattr(content, "refusal"):
                        text += getattr(content, "text", "")
                        text += getattr(content, "refusal", "")
                message.update({"role": getattr(item, "role", "assistant"), "content": text})
            # Handle tool calls
            elif hasattr(item, "call_id") and hasattr(item, "arguments"):
                tool_call_outputs.append(
                    (
                        item.call_id,
                        getattr(item, "name", ""),
                        item.arguments if item.arguments else OAI_HANDOFF_TOOL_ARG,
                    )
                )
                message.update(
                    {
                        "tool_calls": [
                            {
                                "tool_id": item.call_id,
                                "arguments": json.loads(item.arguments)
                                if isinstance(item.arguments, str)
                                else item.arguments,
                                "name": getattr(item, "name", ""),
                                "type": getattr(item, "type", "function"),
                            }
                        ]
                    }
                )
            else:
                message.update({"content": str(item)})
            processed.append(message)

        return processed, tool_call_outputs

    def llmobs_trace_input(self) -> Optional[str]:
        """Converts Response span data to an input value for top level trace.

        Returns:
            The input content if found, None otherwise.
        """
        if not self.response or not self.input:
            return None

        try:
            messages, _ = self.llmobs_input_messages()
            if messages and len(messages) > 0:
                return messages[-1].get("content")
        except (AttributeError, IndexError):
            from ddtrace.internal.logger import get_logger

            logger = get_logger(__name__)
            logger.warning("Failed to process input messages from `response` span", exc_info=True)
        return None


class OaiTraceAdapter:
    """Adapter for OpenAI Agents SDK Trace objects.

    This class provides a clean interface for the integration code to interact with
    OpenAI Agents SDK Trace objects without needing to know their internal structure.
    """

    def __init__(self, oai_trace):
        self._trace = oai_trace  # openai trace data type

    @property
    def trace_id(self) -> str:
        """Get the trace ID."""
        return self._trace.trace_id

    @property
    def name(self) -> str:
        """Get the trace name."""
        return getattr(self._trace, "name", "Agent workflow")

    @property
    def group_id(self) -> Optional[str]:
        """Get the group ID if it exists."""
        try:
            return self._trace.export().get("group_id")
        except (AttributeError, KeyError):
            return None

    @property
    def metadata(self) -> Optional[Dict[str, Any]]:
        """Get the trace metadata if it exists."""
        return self._trace.metadata if hasattr(self._trace, "metadata") else None

    @property
    def raw_trace(self):
        """Get the raw OpenAI Agents SDK trace."""
        return self._trace


@dataclass
class LLMObsTraceInfo:
    """Metadata for llmobs trace used for setting root span attributes and span links"""

    trace_id: str
    span_id: str
    """
    We only update trace's input/output llm spans when that llm span's parent is a top-level agent span.
    This is to ignore extraneous nested llm spans that aren't part of the core agent logic.
    """
    current_top_level_agent_span_id: Optional[str] = None
    input_oai_span: Optional[OaiSpanAdapter] = None
    output_oai_span: Optional[OaiSpanAdapter] = None


def get_final_message_converse_stream_message(
    message: Dict[str, Any], text_blocks: Dict[int, str], tool_blocks: Dict[int, Dict[str, Any]]
) -> Dict[str, Any]:
    """Process a message and its content blocks into LLM Obs message format.

    Args:
        message: A message to be processed containing role and content block indices
        text_blocks: Mapping of content block indices to their text content
        tool_blocks: Mapping of content block indices to their tool usage data

    Returns:
        Dict containing the processed message with content and optional tool calls
    """
    indices = sorted(message.get("content_block_indicies", []))
    message_output = {"role": message["role"]}

    text_contents = [text_blocks[idx] for idx in indices if idx in text_blocks]
    message_output.update({"content": "".join(text_contents)} if text_contents else {})

    tool_calls = []
    for idx in indices:
        tool_block = tool_blocks.get(idx)
        if not tool_block:
            continue
        tool_call = {
            "name": tool_block.get("name", ""),
            "tool_id": tool_block.get("toolUseId", ""),
        }
        tool_input = tool_block.get("input")
        if tool_input is not None:
            tool_args = {}
            try:
                tool_args = json.loads(tool_input)
            except (json.JSONDecodeError, ValueError):
                tool_args = {"input": tool_input}
            tool_call.update({"arguments": tool_args} if tool_args else {})
        tool_calls.append(tool_call)

    if tool_calls:
        message_output["tool_calls"] = tool_calls

    return message_output


_punc_regex = re.compile(r"[\w']+|[.,!?;~@#$%^&*()+/-]")


def _compute_prompt_tokens(model_name, prompts=None, messages=None):
    """Compute token span metrics on streamed chat/completion requests.
    Only required if token usage is not provided in the streamed response.
    """
    num_prompt_tokens = 0
    estimated = False
    if messages:
        for m in messages:
            estimated, prompt_tokens = _compute_token_count(m.get("content", ""), model_name)
            num_prompt_tokens += prompt_tokens
    elif prompts:
        if isinstance(prompts, str) or isinstance(prompts, list) and isinstance(prompts[0], int):
            prompts = [prompts]
        for prompt in prompts:
            estimated, prompt_tokens = _compute_token_count(prompt, model_name)
            num_prompt_tokens += prompt_tokens
    return estimated, num_prompt_tokens


def _compute_completion_tokens(completions_or_messages, model_name):
    """Compute/Estimate the completion token count from the streamed response."""
    if not completions_or_messages:
        return False, 0
    estimated = False
    num_completion_tokens = 0
    for choice in completions_or_messages:
        content = choice.get("content", "") or choice.get("text", "")
        estimated, completion_tokens = _compute_token_count(content, model_name)
        num_completion_tokens += completion_tokens
    return estimated, num_completion_tokens


def _compute_token_count(content, model):
    # type: (Union[str, List[int]], Optional[str]) -> Tuple[bool, int]
    """
    Takes in prompt/response(s) and model pair, and returns a tuple of whether or not the number of prompt
    tokens was estimated, and the estimated/calculated prompt token count.
    """
    num_prompt_tokens = 0
    estimated = False
    if model is not None and tiktoken_available is True:
        try:
            enc = encoding_for_model(model)
            if isinstance(content, str):
                num_prompt_tokens += len(enc.encode(content))
            elif content and isinstance(content, list) and isinstance(content[0], int):
                num_prompt_tokens += len(content)
            return estimated, num_prompt_tokens
        except KeyError:
            # tiktoken.encoding_for_model() will raise a KeyError if it doesn't have a tokenizer for the model
            estimated = True
    else:
        estimated = True

    # If model is unavailable or tiktoken is not imported, then provide a very rough estimate of the number of tokens
    return estimated, _est_tokens(content)


def _est_tokens(prompt):
    # type: (Union[str, List[int]]) -> int
    """
    Provide a very rough estimate of the number of tokens in a string prompt.
    Note that if the prompt is passed in as a token array (list of ints), the token count
    is just the length of the token array.
    """
    # If model is unavailable or tiktoken is not imported, then provide a very rough estimate of the number of tokens
    # Approximate using the following assumptions:
    #    * English text
    #    * 1 token ~= 4 chars
    #    * 1 token ~= ¾ words
    if not prompt:
        return 0
    est_tokens = 0
    if isinstance(prompt, str):
        est1 = len(prompt) / 4
        est2 = len(_punc_regex.findall(prompt)) * 0.75
        return round((1.5 * est1 + 0.5 * est2) / 2)
    elif isinstance(prompt, list) and isinstance(prompt[0], int):
        return len(prompt)
    return est_tokens
