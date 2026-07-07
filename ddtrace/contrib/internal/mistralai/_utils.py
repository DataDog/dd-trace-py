from typing import Any
from typing import Optional

from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import BaseStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import StreamHandler
from ddtrace.llmobs._utils import _get_attr


def _accumulate_tool_calls(tool_calls_map: dict[int, dict[str, Any]], tool_calls: list[Any]) -> None:
    for tool_call in tool_calls:
        index = _get_attr(tool_call, "index", 0)

        if index not in tool_calls_map:
            tool_calls_map[index] = {"id": "", "function": {"name": "", "arguments": ""}}

        accumulated = tool_calls_map[index]

        tool_id = _get_attr(tool_call, "id", None)
        if tool_id is not None and tool_id != "null":
            accumulated["id"] = tool_id

        function = _get_attr(tool_call, "function", None)
        if function is not None:
            name = _get_attr(function, "name", "")
            arguments = _get_attr(function, "arguments", "")
            if name and not accumulated["function"]["name"]:
                accumulated["function"]["name"] = name
            if arguments:
                accumulated["function"]["arguments"] += arguments


def _process_thinking(content: list[Any], text_parts: list[str], thinking_parts: list[str]) -> None:
    for content_chunk in content:
        thinking = _get_attr(content_chunk, "thinking", None)
        if isinstance(thinking, list):
            for thinking_chunk in thinking:
                thinking_chunk_text = _get_attr(thinking_chunk, "text", None)
                if isinstance(thinking_chunk_text, str):
                    thinking_parts.append(thinking_chunk_text)
            continue  # Extracted thinking chunks, skip to next content_chunk
        text = _get_attr(content_chunk, "text", None)
        if isinstance(text, str):
            text_parts.append(text)


def _join_chunks(chunks: list[Any]) -> Optional[dict[str, Any]]:
    if not chunks:
        return None

    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_calls_map: dict[int, dict[str, Any]] = {}
    role: Optional[str] = None
    usage = None

    for event in chunks:
        chunk = _get_attr(event, "data", event)
        usage = usage or _get_attr(chunk, "usage", None)
        choices = _get_attr(chunk, "choices", [])
        if not isinstance(choices, list):
            continue

        for choice in choices:
            delta = _get_attr(choice, "delta", None)
            if delta is None:
                continue

            chunk_role = _get_attr(delta, "role", None)
            if isinstance(chunk_role, str):
                role = chunk_role

            content = _get_attr(delta, "content", None)
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                _process_thinking(content, text_parts, thinking_parts)

            tool_calls = _get_attr(delta, "tool_calls", None)
            if isinstance(tool_calls, list):
                _accumulate_tool_calls(tool_calls_map, tool_calls)

    messages: list[dict[str, Any]] = []
    if thinking_parts:
        messages.append({"message": {"role": "reasoning", "content": "".join(thinking_parts)}})

    message: dict[str, Any] = {"role": role or "assistant", "content": "".join(text_parts)}
    if tool_calls_map:
        message["tool_calls"] = list(tool_calls_map.values())
    messages.append({"message": message})

    merged_response: dict[str, Any] = {"choices": messages}
    if usage is not None:
        merged_response["usage"] = usage
    return merged_response


class BaseMistralAIStreamHandler(BaseStreamHandler):
    def finalize_stream(self, exception: Optional[BaseException] = None) -> None:
        self.integration.llmobs_set_tags(
            self.primary_span,
            args=list(self.request_args),
            kwargs=self.request_kwargs,
            response=_join_chunks(self.chunks),
            operation="llm",
        )
        self.primary_span.finish()


class MistralAIStreamHandler(BaseMistralAIStreamHandler, StreamHandler):
    def process_chunk(self, chunk: Any, iterator: Any = None) -> None:
        self.chunks.append(chunk)


class MistralAIAsyncStreamHandler(BaseMistralAIStreamHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk: Any, iterator: Any = None) -> None:
        self.chunks.append(chunk)
