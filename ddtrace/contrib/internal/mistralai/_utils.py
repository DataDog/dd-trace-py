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
        if tool_id != "null":
            accumulated["id"] = tool_id

        function = _get_attr(tool_call, "function", None)
        if function is not None:
            name = _get_attr(function, "name", "")
            arguments = _get_attr(function, "arguments", "")
            if name and not accumulated["function"]["name"]:
                accumulated["function"]["name"] = name
            if arguments:
                accumulated["function"]["arguments"] += arguments


def _join_chunks(chunks: list[Any]) -> Optional[dict[str, Any]]:
    if not chunks:
        return None

    text_parts: list[str] = []
    tool_calls_map: dict[int, dict[str, Any]] = {}
    role = None
    usage = None

    try:
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

                if role is None:
                    role = _get_attr(delta, "role", None)

                content = _get_attr(delta, "content", None)
                if isinstance(content, str):
                    text_parts.append(content)
                elif isinstance(content, list):
                    for content_chunk in content:
                        text = _get_attr(content_chunk, "text", None)
                        if text:
                            text_parts.append(text)

                tool_calls = _get_attr(delta, "tool_calls", None)
                if isinstance(tool_calls, list):
                    _accumulate_tool_calls(tool_calls_map, tool_calls)

        message: dict[str, Any] = {"role": role or "assistant", "content": "".join(text_parts)}
        if tool_calls_map:
            message["tool_calls"] = list(tool_calls_map.values())

        merged_response: dict[str, Any] = {"choices": [{"message": message}]}
        if usage is not None:
            merged_response["usage"] = usage
        return merged_response
    except Exception:
        return None


class BaseMistralAIStreamHandler(BaseStreamHandler):
    def finalize_stream(self, exception: Optional[BaseException] = None) -> None:
        self.integration.llmobs_set_tags(
            self.primary_span,
            args=self.request_args,
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
