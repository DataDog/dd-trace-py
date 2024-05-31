import sys
from typing import Dict
from typing import Tuple

from ddtrace.internal.logger import get_logger
from ddtrace.vendor import wrapt


log = get_logger(__name__)


def tag_streamed_response(integration, resp, args, kwargs, span):
    if _is_async_stream(resp):
        return TracedAnthropicAsyncStream(resp, integration, span, args, kwargs)
    elif _is_stream(resp):
        return TracedAnthropicStream(resp, integration, span, args, kwargs)
    elif _is_stream_manager(resp):
        return TracedAnthropicStreamManager(resp, integration, span, args, kwargs)


def _process_finished_stream(
    integration, span, args, kwargs, streamed_chunks, message_accumulated_via_stream_helper=False
):
    resp_message = {}
    try:
        if message_accumulated_via_stream_helper:
            resp_message = _construct_accumulated_messages(streamed_chunks)
        else:
            resp_message = _construct_unaccumulated_messages(streamed_chunks)
        if integration.is_pc_sampled_span(span):
            _tag_streamed_chat_completion_response(integration, span, resp_message)
        if integration.is_pc_sampled_llmobs(span):
            integration.llmobs_set_tags(
                span,
                response=resp_message,
                args=args,
                kwargs=kwargs,
            )
    except Exception:
        log.warning("Error processing streamed completion/chat response.", exc_info=True)


class BaseTracedAnthropicStream(wrapt.ObjectProxy):
    def __init__(self, wrapped, integration, span, args, kwargs, _is_message_stream_manager=False):
        super().__init__(wrapped)
        n = kwargs.get("n", 1) or 1
        self._dd_span = span
        self._streamed_chunks = [[] for _ in range(n)]
        self._dd_integration = integration
        self._kwargs = kwargs
        self._args = args

        # Anthropic's message stream helper will have accumulated the message for us already.
        # We need to identify if a AnthropicStreamManager was used as we will not have to build up the message
        self._is_message_stream_manager = _is_message_stream_manager


class TracedAnthropicStream(BaseTracedAnthropicStream):
    def __enter__(self):
        self.__wrapped__.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.__wrapped__.__exit__(exc_type, exc_val, exc_tb)

    def __iter__(self):
        return self

    def __next__(self):
        try:
            chunk = self.__wrapped__.__next__()
            self._streamed_chunks.append(chunk)
            return chunk
        except StopIteration:
            _process_finished_stream(
                self._dd_integration,
                self._dd_span,
                self._args,
                self._kwargs,
                self._streamed_chunks,
                self._is_message_stream_manager,
            )
            self._dd_span.finish()
            raise
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            self._dd_span.finish()
            raise

    def __stream_text__(self):
        for chunk in self:
            if chunk.type == "content_block_delta" and chunk.delta.type == "text_delta":
                yield chunk.delta.text


class TracedAnthropicStreamManager(BaseTracedAnthropicStream):
    def __enter__(self):
        self.__wrapped__.__enter__()
        stream = TracedAnthropicStream(
            self.__wrapped__._MessageStreamManager__stream,
            self._dd_integration,
            self._dd_span,
            self._args,
            self._kwargs,
            _is_message_stream_manager=True,
        )
        stream.text_stream = stream.__stream_text__()
        return stream


class TracedAnthropicAsyncStream(BaseTracedAnthropicStream):
    async def __aenter__(self):
        await self.__wrapped__.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            chunk = await self.__wrapped__.__anext__()
            self._streamed_chunks.append(chunk)
            return chunk
        except StopAsyncIteration:
            _process_finished_stream(
                self._dd_integration,
                self._dd_span,
                self._args,
                self._kwargs,
                self._streamed_chunks,
                self._is_message_stream_manager,
            )
            self._dd_span.finish()
            raise
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            self._dd_span.finish()
            raise


def _construct_unaccumulated_messages(streamed_chunks):
    """Iteratively build up a response message. This is for non-accumulated responses from
    calling `Anthropic.messages.Messages.create(stream=true)`. This function does not build up
    the response content for the user, and we will need to do the same using the streamed chunks.
    """
    message = {}
    message_complete = True
    prev_message = None
    for chunk in streamed_chunks:
        # if message wasn't completed, use the message in the function as we are still completing it
        if not message_complete:
            prev_message = message
        else:
            prev_message = None
        message, message_complete = _construct_message_from_streamed_chunks(chunk, prev_message=prev_message)

        if message_complete:
            return message
    return message


def _construct_accumulated_messages(streamed_chunks):
    """Iteratively build up a response message. This is for accumulated responses from
    calling `Anthropic.messages.Messages.stream` helper method. This function does build up
    the response content for the user, and we only need to look at message_start blocks for the content.
    """
    resp = {"content": []}
    for chunk in streamed_chunks:
        if chunk and chunk.type == "message_start" and chunk.message:
            content_text = ""
            for content in chunk.message.content:
                if content.type == "text":
                    content_text += content.text
                    content_type = "text"
                elif content.type == "image":
                    content_text = "([IMAGE DETECTED])"
                    content_type = "image"
            resp["content"].append({"text": content_text, "type": content_type})
            resp.update(
                {
                    "role": chunk.message.role,
                    "finish_reason": chunk.message.stop_reason,
                    "usage": {
                        "prompt": chunk.message.usage.input_tokens,
                        "completion": chunk.message.usage.output_tokens,
                    },
                }
            )
            content = ""
            content_type = None
    return resp


def _construct_message_from_streamed_chunks(chunk, prev_message=None) -> Tuple[Dict[str, str], bool]:
    """Constructs a chat message dictionary from streamed chunks.

    The resulting message dictionary is of form:
      {"content": [{"type": [TYPE], "text": "[TEXT]"}], "role": "...", "finish_reason": "...", "usage": ...}
    """
    message = prev_message if prev_message is not None else {"content": []}
    message_finished = False
    if getattr(chunk, "type", "") == "message_start":
        # this is the starting chunk of the message
        chunk_role = getattr(chunk.message, "role", "")
        chunk_usage = getattr(chunk.message, "usage", "")
        if chunk_role:
            message["role"] = chunk_role
        if chunk_usage:
            message["usage"] = {}
            message["usage"]["prompt"] = getattr(chunk_usage, "input_tokens", 0)
            message["usage"]["completion"] = getattr(chunk_usage, "output_tokens", 0)
    elif getattr(chunk, "type", "") == "content_block_start":
        # this is the start to a message.content block (possible 1 of several content blocks)
        message["content"].append({"type": "text", "text": ""})
    elif getattr(chunk, "delta", None):
        # delta events contain new content for the current message.content block
        content_block = chunk.delta
        if getattr(content_block, "type", "") == "text_delta":
            chunk_content = getattr(content_block, "text", "")
            if chunk_content:
                message["content"][-1]["text"] += chunk_content

        elif getattr(chunk, "type", "") == "message_delta":
            # message delta events signal the end of the message
            chunk_finish_reason = getattr(content_block, "stop_reason", "")
            if chunk_finish_reason:
                message["finish_reason"] = content_block.stop_reason
                message["content"][-1]["text"] = message["content"][-1]["text"].strip()

                chunk_usage = getattr(chunk, "usage", {})
                if chunk_usage:
                    message_usage = message.get("usage", {"completion": 0, "prompt": 0})
                    message_usage["completion"] += getattr(chunk_usage, "output_tokens", 0)
                    message_usage["prompt"] += getattr(chunk_usage, "input_tokens", 0)
                    message["usage"] = message_usage
                message_finished = True

    return message, message_finished


def _tag_streamed_chat_completion_response(integration, span, message):
    """Tagging logic for streamed chat completions."""
    if message is None:
        return
    for idx, content in enumerate(message["content"]):
        span.set_tag_str("anthropic.response.completions.%d.type" % idx, str(integration.trunc(content["type"])))
        span.set_tag_str("anthropic.response.completions.%d.content" % idx, str(integration.trunc(content["text"])))
        span.set_tag_str("anthropic.response.completions.role", str(message["role"]))
        if message.get("finish_reason", None) is not None:
            span.set_tag_str("anthropic.response.completions.finish_reason", str(message["finish_reason"]))


def _is_stream(resp):
    # type: (...) -> bool
    import anthropic

    if hasattr(anthropic, "Stream") and isinstance(resp, anthropic.Stream):
        return True
    return False


def _is_async_stream(resp):
    # type: (...) -> bool
    import anthropic

    if hasattr(anthropic, "AsyncStream") and isinstance(resp, anthropic.AsyncStream):
        return True
    return False


def _is_stream_manager(resp):
    # type: (...) -> bool
    import anthropic

    return isinstance(resp, anthropic.lib.streaming._messages.MessageStreamManager)
