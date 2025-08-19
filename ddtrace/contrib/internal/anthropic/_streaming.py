import json
from typing import Any
from typing import Dict
from typing import Tuple

import anthropic

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import StreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import make_traced_stream
from ddtrace.llmobs._utils import _get_attr


log = get_logger(__name__)


def _text_stream_generator(traced_stream):
    for chunk in traced_stream:
        if chunk.type == "content_block_delta" and chunk.delta.type == "text_delta":
            yield chunk.delta.text


async def _async_text_stream_generator(traced_stream):
    async for chunk in traced_stream:
        if chunk.type == "content_block_delta" and chunk.delta.type == "text_delta":
            yield chunk.delta.text


def handle_streamed_response(integration, resp, args, kwargs, span):
    """
    Creates a traced stream with a callback that adds a text_stream attribute
    to the underlying stream object when it is created.

    Overrides the `text_stream` attribute to trace yielded chunks; otherwise,
    the underlying stream will bypass the wrapper tracing code
    """

    def add_text_stream(stream):
        stream.text_stream = _text_stream_generator(stream)

    def add_async_text_stream(stream):
        stream.text_stream = _async_text_stream_generator(stream)

    if _is_stream(resp) or _is_stream_manager(resp):
        traced_stream = make_traced_stream(
            resp, AnthropicStreamHandler(integration, span, args, kwargs), on_stream_created=add_text_stream
        )
        return traced_stream
    elif _is_async_stream(resp) or _is_async_stream_manager(resp):
        traced_stream = make_traced_stream(
            resp,
            AnthropicAsyncStreamHandler(integration, span, args, kwargs),
            on_stream_created=add_async_text_stream,
        )
        return traced_stream


class BaseAnthropicStreamHandler:
    def finalize_stream(self, exception=None):
        _process_finished_stream(
            self.integration, self.primary_span, self.request_args, self.request_kwargs, self.chunks
        )
        self.primary_span.finish()


class AnthropicStreamHandler(BaseAnthropicStreamHandler, StreamHandler):
    def process_chunk(self, chunk, iterator=None):
        self.chunks.append(chunk)


class AnthropicAsyncStreamHandler(BaseAnthropicStreamHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk, iterator=None):
        self.chunks.append(chunk)


def _process_finished_stream(integration, span, args, kwargs, streamed_chunks):
    # builds the response message given streamed chunks and sets according span tags
    try:
        resp_message = _construct_message(streamed_chunks)
        integration.llmobs_set_tags(span, args=[], kwargs=kwargs, response=resp_message)
    except Exception:
        log.warning("Error processing streamed completion/chat response.", exc_info=True)


def _construct_message(streamed_chunks):
    """Iteratively build up a response message from streamed chunks.

    The resulting message dictionary is of form:
      {"content": [{"type": [TYPE], "text": "[TEXT]"}], "role": "...", "finish_reason": "...", "usage": ...}
    """
    message = {"content": []}
    for chunk in streamed_chunks:
        message = _extract_from_chunk(chunk, message)
    return message


def _extract_from_chunk(chunk, message) -> Tuple[Dict[str, str], bool]:
    """Constructs a chat message dictionary from streamed chunks given chunk type"""
    TRANSFORMATIONS_BY_BLOCK_TYPE = {
        "message_start": _on_message_start_chunk,
        "content_block_start": _on_content_block_start_chunk,
        "content_block_delta": _on_content_block_delta_chunk,
        "content_block_stop": _on_content_block_stop_chunk,
        "message_delta": _on_message_delta_chunk,
        "error": _on_error_chunk,
    }
    chunk_type = _get_attr(chunk, "type", "")
    transformation = TRANSFORMATIONS_BY_BLOCK_TYPE.get(chunk_type)
    if transformation is not None:
        message = transformation(chunk, message)

    return message


def _on_message_start_chunk(chunk, message):
    # this is the starting chunk of the message
    chunk_message = _get_attr(chunk, "message", "")
    if chunk_message:
        chunk_role = _get_attr(chunk_message, "role", "")
        chunk_usage = _get_attr(chunk_message, "usage", "")
        if chunk_role:
            message["role"] = chunk_role
        if chunk_usage:
            message["usage"] = {"input_tokens": _get_attr(chunk_usage, "input_tokens", 0)}

            cache_write_tokens = _get_attr(chunk_usage, "cache_creation_input_tokens", None)
            cache_read_tokens = _get_attr(chunk_usage, "cache_read_input_tokens", None)
            if cache_write_tokens is not None:
                message["usage"]["cache_creation_input_tokens"] = cache_write_tokens
            if cache_read_tokens is not None:
                message["usage"]["cache_read_input_tokens"] = cache_read_tokens

    return message


def _on_content_block_start_chunk(chunk, message):
    # this is the start to a message.content block (possibly 1 of several content blocks)
    chunk_content_block = _get_attr(chunk, "content_block", "")
    if chunk_content_block:
        chunk_content_block_type = _get_attr(chunk_content_block, "type", "")
        if chunk_content_block_type == "text":
            chunk_content_block_text = _get_attr(chunk_content_block, "text", "")
            message["content"].append({"type": "text", "text": chunk_content_block_text})
        elif chunk_content_block_type == "tool_use":
            chunk_content_block_name = _get_attr(chunk_content_block, "name", "")
            message["content"].append({"type": "tool_use", "name": chunk_content_block_name, "input": ""})
    return message


def _on_content_block_delta_chunk(chunk, message):
    # delta events contain new content for the current message.content block
    delta_block = _get_attr(chunk, "delta", "")
    if delta_block:
        chunk_content_text = _get_attr(delta_block, "text", "")
        if chunk_content_text:
            message["content"][-1]["text"] += chunk_content_text

        chunk_content_json = _get_attr(delta_block, "partial_json", "")
        if chunk_content_json and _get_attr(delta_block, "type", "") == "input_json_delta":
            # we have a json content block, most likely a tool input dict
            message["content"][-1]["input"] += chunk_content_json
    return message


def _on_content_block_stop_chunk(chunk, message):
    # this is the start to a message.content block (possibly 1 of several content blocks)
    content_type = _get_attr(message["content"][-1], "type", "")
    if content_type == "tool_use":
        input_json = _get_attr(message["content"][-1], "input", "{}")
        message["content"][-1]["input"] = json.loads(input_json)
    return message


def _on_message_delta_chunk(chunk, message):
    # message delta events signal the end of the message
    delta_block = _get_attr(chunk, "delta", "")
    chunk_finish_reason = _get_attr(delta_block, "stop_reason", "")
    if chunk_finish_reason:
        message["finish_reason"] = chunk_finish_reason

    chunk_usage = _get_attr(chunk, "usage", {})
    if chunk_usage:
        message_usage = message.get("usage", {"output_tokens": 0, "input_tokens": 0})
        message_usage["output_tokens"] = _get_attr(chunk_usage, "output_tokens", 0)

        cache_creation_tokens = _get_attr(chunk_usage, "cache_creation_input_tokens", None)
        cache_read_tokens = _get_attr(chunk_usage, "cache_read_input_tokens", None)
        if cache_creation_tokens is not None:
            message_usage["cache_creation_input_tokens"] = cache_creation_tokens
        if cache_read_tokens is not None:
            message_usage["cache_read_input_tokens"] = cache_read_tokens

        message["usage"] = message_usage

    return message


def _on_error_chunk(chunk, message):
    if _get_attr(chunk, "error"):
        message["error"] = {}
        if _get_attr(chunk.error, "type"):
            message["error"]["type"] = chunk.error.type
        if _get_attr(chunk.error, "message"):
            message["error"]["message"] = chunk.error.message
    return message


def _is_stream(resp: Any) -> bool:
    if hasattr(anthropic, "Stream") and isinstance(resp, anthropic.Stream):
        return True
    return False


def _is_async_stream(resp: Any) -> bool:
    if hasattr(anthropic, "AsyncStream") and isinstance(resp, anthropic.AsyncStream):
        return True
    return False


def _is_stream_manager(resp: Any) -> bool:
    if hasattr(anthropic, "MessageStreamManager") and isinstance(resp, anthropic.MessageStreamManager):
        return True
    return False


def _is_async_stream_manager(resp: Any) -> bool:
    if hasattr(anthropic, "AsyncMessageStreamManager") and isinstance(resp, anthropic.AsyncMessageStreamManager):
        return True
    return False


def is_streaming_operation(resp: Any) -> bool:
    return _is_stream(resp) or _is_async_stream(resp) or _is_stream_manager(resp) or _is_async_stream_manager(resp)
