import sys
from typing import Any
from typing import Dict
from typing import List

from ddtrace.internal.logger import get_logger

log = get_logger(__name__)

def tag_request(span, kwargs):
    if "metadata" in kwargs and "headers" in kwargs["metadata"] and "host" in kwargs["metadata"]["headers"]:
        span.set_tag_str("litellm.request.host", kwargs["metadata"]["headers"]["host"])

class BaseTracedLiteLLMStream:
    def __init__(self, generator, integration, span, args, kwargs, is_completion=False):
        n = kwargs.get("n", 1) or 1
        self._generator = generator
        self._dd_integration = integration
        self._dd_span = span
        self._args = args
        self._kwargs = kwargs
        self._streamed_chunks = [[] for _ in range(n)]
        self._is_completion = is_completion


class TracedLiteLLMStream(BaseTracedLiteLLMStream):
    def __enter__(self):
        self._generator.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._generator.__exit__(exc_type, exc_val, exc_tb)

    def __iter__(self):
        exception_raised = False
        try:
            for chunk in self._generator:
                self._extract_token_chunk(chunk)
                yield chunk
                _loop_handler(chunk, self._streamed_chunks)
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            exception_raised = True
            raise
        finally:
            if not exception_raised:
                _process_finished_stream(
                    self._dd_integration, self._dd_span, self._kwargs, self._streamed_chunks, self._is_completion
                )
            self._dd_span.finish()

class TracedLiteLLMAsyncStream(BaseTracedLiteLLMStream):
    async def __aenter__(self):
        await self._generator.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._generator.__aexit__(exc_type, exc_val, exc_tb)

    async def __aiter__(self):
        exception_raised = False
        try:
            async for chunk in self._generator:
                yield chunk
                _loop_handler(chunk, self._streamed_chunks)
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            exception_raised = True
            raise
        finally:
            if not exception_raised:
                _process_finished_stream(
                    self._dd_integration, self._dd_span, self._kwargs, self._streamed_chunks, self._is_completion
                )
            self._dd_span.finish()

def _loop_handler(chunk, streamed_chunks):
    """Appends the chunk to the correct index in the streamed_chunks list.

    When handling a streamed chat/completion response, this function is called for each chunk in the streamed response.
    """
    for choice in chunk.choices:
        streamed_chunks[choice.index].append(choice)
    if getattr(chunk, "usage", None):
        streamed_chunks[0].insert(0, chunk)


def _process_finished_stream(integration, span, kwargs, streamed_chunks, is_completion=False):
    try:
        if is_completion:
            formatted_completions = [_construct_completion_from_streamed_chunks(choice) for choice in streamed_chunks]
        else:
            formatted_completions = [_construct_message_from_streamed_chunks(choice) for choice in streamed_chunks]
        operation = "completion" if is_completion else "chat"
        integration.llmobs_set_tags(span, args=[], kwargs=kwargs, response=formatted_completions, operation=operation)
    except Exception:
        log.warning("Error processing streamed completion/chat response.", exc_info=True)


def _construct_completion_from_streamed_chunks(streamed_chunks: List[Any]) -> Dict[str, str]:
    """Constructs a completion dictionary of form {"text": "...", "finish_reason": "..."} from streamed chunks."""
    if not streamed_chunks:
        return {"text": ""}
    completion = {"text": "".join(c.text for c in streamed_chunks if getattr(c, "text", None))}
    if streamed_chunks[-1].finish_reason is not None:
        completion["finish_reason"] = streamed_chunks[-1].finish_reason
    if hasattr(streamed_chunks[0], "usage"):
        completion["usage"] = streamed_chunks[0].usage
    return completion


def _construct_tool_call_from_streamed_chunk(stored_tool_calls, tool_call_chunk=None, function_call_chunk=None):
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


def _construct_message_from_streamed_chunks(streamed_chunks: List[Any]) -> Dict[str, str]:
    """Constructs a chat completion message dictionary from streamed chunks.
    The resulting message dictionary is of form:
    {"content": "...", "role": "...", "tool_calls": [...], "finish_reason": "..."}
    """
    message = {"content": "", "tool_calls": []}
    for chunk in streamed_chunks:
        if getattr(chunk, "usage", None):
            message["usage"] = chunk.usage
        if not hasattr(chunk, "delta"):
            continue
        if getattr(chunk, "index", None) and not message.get("index"):
            message["index"] = chunk.index
        if getattr(chunk.delta, "role") and not message.get("role"):
            message["role"] = chunk.delta.role
        if getattr(chunk, "finish_reason", None) and not message.get("finish_reason"):
            message["finish_reason"] = chunk.finish_reason
        chunk_content = getattr(chunk.delta, "content", "")
        if chunk_content:
            message["content"] += chunk_content
            continue
        function_call = getattr(chunk.delta, "function_call", None)
        if function_call:
            _construct_tool_call_from_streamed_chunk(message["tool_calls"], function_call_chunk=function_call)
        tool_calls = getattr(chunk.delta, "tool_calls", None)
        if not tool_calls:
            continue
        for tool_call in tool_calls:
            _construct_tool_call_from_streamed_chunk(message["tool_calls"], tool_call_chunk=tool_call)
    if message["tool_calls"]:
        message["tool_calls"].sort(key=lambda x: x.get("index", 0))
    else:
        message.pop("tool_calls", None)
    message["content"] = message["content"].strip()
    return message
