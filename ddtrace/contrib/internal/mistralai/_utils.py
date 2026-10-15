from typing import Any
from typing import Optional

from ddtrace.contrib.internal.stream_handler import AsyncStreamHandler
from ddtrace.contrib.internal.stream_handler import BaseStreamHandler
from ddtrace.contrib.internal.stream_handler import StreamHandler
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _get_attr(o: object, attr: str, default: Any) -> Any:
    # Streamed chunks may be SDK objects or plain dicts.
    if isinstance(o, dict):
        return o.get(attr, default)
    return getattr(o, attr, default)


# Duplicated from ddtrace.llmobs._integrations.mistralai_utils.extract_provider (and
# ddtrace.llmobs._constants.UNKNOWN_MODEL_PROVIDER) so this module does not import LLMObs.
# The LLMObs side recomputes it at span finish; both copies must agree.
_UNKNOWN_MODEL_PROVIDER = "unknown"


def extract_provider(kwargs: dict[str, Any]) -> str:
    server_url = kwargs.get("server_url") or ""
    return "mistral" if not server_url or "mistral" in server_url.lower() else _UNKNOWN_MODEL_PROVIDER


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

    choice_states: dict[int, dict[str, Any]] = {}
    usage = None

    for event in chunks:
        chunk = _get_attr(event, "data", event)
        usage = usage or _get_attr(chunk, "usage", None)
        choices_in_chunk = _get_attr(chunk, "choices", [])
        if not isinstance(choices_in_chunk, list):
            continue
        for choice in choices_in_chunk:
            delta = _get_attr(choice, "delta", None)
            if delta is None:
                continue
            index = _get_attr(choice, "index", 0)
            curr_state = choice_states.setdefault(
                index, {"text_parts": [], "thinking_parts": [], "tool_calls_map": {}, "role": None}
            )

            role = _get_attr(delta, "role", None)
            if isinstance(role, str):
                curr_state["role"] = role

            content = _get_attr(delta, "content", None)
            if isinstance(content, str):
                curr_state["text_parts"].append(content)
            elif isinstance(content, list):
                _process_thinking(content, curr_state["text_parts"], curr_state["thinking_parts"])

            tool_calls = _get_attr(delta, "tool_calls", None)
            if isinstance(tool_calls, list):
                _accumulate_tool_calls(curr_state["tool_calls_map"], tool_calls)

    messages: list[dict[str, Any]] = []
    for index in sorted(choice_states):
        curr_state = choice_states[index]
        if curr_state["thinking_parts"]:
            messages.append({"message": {"role": "reasoning", "content": "".join(curr_state["thinking_parts"])}})
        message: dict[str, Any] = {
            "role": curr_state["role"] or "assistant",
            "content": "".join(curr_state["text_parts"]),
        }
        if curr_state["tool_calls_map"]:
            message["tool_calls"] = list(curr_state["tool_calls_map"].values())
        messages.append({"message": message})

    merged_response: dict[str, Any] = {"choices": messages}
    if usage is not None:
        merged_response["usage"] = usage
    return merged_response


class BaseMistralAIStreamHandler(BaseStreamHandler):
    """Shared finalization for the sync and async MistralAI stream handlers.

    The handlers only need the context to dispatch the deferred ended event, so the
    integration slot BaseStreamHandler keeps for other integrations goes unused.
    """

    def finalize_stream(self, exception: Optional[BaseException] = None) -> None:
        """Merge the chunks onto the event, then dispatch the deferred ended event.

        The TracingSubscriber's _on_context_ended sets the LLMObs tags (via the
        SPAN_FINISHING subscriber) and finishes the span.
        """
        ctx = self.options["ctx"]
        try:
            ctx.event.response = _join_chunks(self.chunks)
        except Exception:
            log.warning("Error processing streamed MistralAI response.", exc_info=True)
        if exception:
            ctx.dispatch_ended_event(type(exception), exception, exception.__traceback__)
        else:
            ctx.dispatch_ended_event()


class MistralAIStreamHandler(BaseMistralAIStreamHandler, StreamHandler):
    def process_chunk(self, chunk: Any, iterator: Any = None) -> None:
        self.chunks.append(chunk)


class MistralAIAsyncStreamHandler(BaseMistralAIStreamHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk: Any, iterator: Any = None) -> None:
        self.chunks.append(chunk)
