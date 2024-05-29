import sys
from typing import AsyncGenerator
from typing import Dict
from typing import Generator
from typing import Tuple

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.vendor import wrapt


log = get_logger(__name__)


def handle_stream_response(integration, resp, args, kwargs, span):
    if _is_async_generator(resp):
        return TracedOpenAIAsyncStream(resp, integration, span, args, kwargs)
    elif _is_generator(resp):
        return TracedOpenAIStream(resp, integration, span, args, kwargs)


def _process_finished_stream(integration, span, args, kwargs, streamed_chunks):
    messages = None
    messages = kwargs.get("messages", None)
    chat_messages = get_argument_value(args, kwargs, 0, "messages")
    try:
        messages = _construct_messages(streamed_chunks)
        if integration.is_pc_sampled_span(span):
            _tag_streamed_chat_completion_response(integration, span, messages)
        if integration.is_pc_sampled_llmobs(span):
            integration.llmobs_set_tags(
                span,
                input_messages=chat_messages,
                formatted_response=messages,
            )
    except Exception:
        log.warning("Error processing streamed completion/chat response.", exc_info=True)


class BaseTracedOpenAIStream(wrapt.ObjectProxy):
    def __init__(self, wrapped, integration, span, args, kwargs):
        super().__init__(wrapped)
        n = kwargs.get("n", 1) or 1
        self._dd_span = span
        self._streamed_chunks = [[] for _ in range(n)]
        self._dd_integration = integration
        self._kwargs = kwargs
        self._args = args


class TracedOpenAIStream(BaseTracedOpenAIStream):
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
            _loop_handler(self._dd_span, chunk, self._streamed_chunks)
            return chunk
        except StopIteration:
            _process_finished_stream(
                self._dd_integration, self._dd_span, self._args, self._kwargs, self._streamed_chunks
            )
            self._dd_span.finish()
            self._dd_integration.metric(self._dd_span, "dist", "request.duration", self._dd_span.duration_ns)
            raise
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            self._dd_span.finish()
            self._dd_integration.metric(self._dd_span, "dist", "request.duration", self._dd_span.duration_ns)
            raise


class TracedOpenAIAsyncStream(BaseTracedOpenAIStream):
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
            _loop_handler(self._dd_span, chunk, self._streamed_chunks)
            return chunk
        except StopAsyncIteration:
            _process_finished_stream(
                self._dd_integration, self._dd_span, self._args, self._kwargs, self._streamed_chunks
            )
            self._dd_span.finish()
            self._dd_integration.metric(self._dd_span, "dist", "request.duration", self._dd_span.duration_ns)
            raise
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            self._dd_span.finish()
            self._dd_integration.metric(self._dd_span, "dist", "request.duration", self._dd_span.duration_ns)
            raise


def _construct_messages(streamed_chunks):
    """Iteratively build up a list of messages."""
    messages = []
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
            messages.append(message)
    return messages


def _construct_message_from_streamed_chunks(chunk, prev_message=None) -> Tuple[Dict[str, str], bool]:
    """Constructs a chat message dictionary from streamed chunks.
    The resulting message dictionary is of form {"content": "...", "role": "...", "finish_reason": "..."}
    """
    message = prev_message if prev_message is not None else {"content": ""}
    message_finished = False
    if getattr(chunk, "message", None):
        # this is the starting chunk
        chunk_content = getattr(chunk.message, "content", "")
        chunk_role = getattr(chunk.message, "role", "")
        if chunk_content:
            message["content"] += chunk_content
        if chunk_role:
            message["role"] = chunk_role
    elif getattr(chunk, "delta", None):
        # delta events contain new content
        content_block = chunk.delta
        if getattr(content_block, "type", "") == "text_delta":
            chunk_content = getattr(content_block, "text", "")
            if chunk_content:
                message["content"] += chunk_content

        elif getattr(chunk, "type", "") == "message_delta":
            # message delta events signal the end of the message
            chunk_finish_reason = getattr(content_block, "stop_reason", "")
            if chunk_finish_reason:
                message["finish_reason"] = content_block.stop_reason
                message["content"] = message["content"].strip()

                chunk_usage = getattr(chunk, "usage", "")
                if chunk_usage:
                    message["usage"] = {"output": chunk_usage.output_tokens}
                message_finished = True

    return message, message_finished


def _tag_streamed_chat_completion_response(integration, span, messages):
    """Tagging logic for streamed chat completions."""
    if messages is None:
        return
    for idx, message in enumerate(messages):
        span.set_tag_str("anthropic.response.completions.%d.content" % idx, integration.trunc(message["content"]))
        span.set_tag_str("anthropic.response.completions.%d.role" % idx, message["role"])
        if message.get("finish_reason") is not None:
            span.set_tag_str("anthropic.response.completions.%d.finish_reason" % idx, message["finish_reason"])


def _loop_handler(span, chunk, streamed_chunks):
    """Sets the anthropic model tag and appends the chunk to the correct index in the streamed_chunks list.

    When handling a streamed chat/completion response, this function is called for each chunk in the streamed response.
    """
    if span.get_tag("anthropic.response.model") is None:
        span.set_tag("anthropic.response.model", chunk.message.model)
    streamed_chunks.append(chunk)


def _is_generator(resp):
    # type: (...) -> bool
    import anthropic

    if isinstance(resp, Generator):
        return True
    if hasattr(anthropic, "Stream") and isinstance(resp, anthropic.Stream):
        return True
    return False


def _is_async_generator(resp):
    # type: (...) -> bool
    import anthropic

    if isinstance(resp, AsyncGenerator):
        return True
    if hasattr(anthropic, "AsyncStream") and isinstance(resp, anthropic.AsyncStream):
        return True
    return False
