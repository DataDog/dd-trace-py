from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import StreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import make_traced_stream


log = get_logger(__name__)


class _BaseLlamaIndexStreamHandler:
    """Shared finalization logic for sync and async LlamaIndex stream handlers."""

    def finalize_stream(self, exception: Optional[Exception] = None) -> None:
        _process_finished_stream(
            self.integration,
            self.primary_span,
            self.request_args,
            self.request_kwargs,
            self.chunks,
            is_chat=getattr(self, "_is_chat", True),
        )
        self.primary_span.finish()


class LlamaIndexStreamHandler(_BaseLlamaIndexStreamHandler, StreamHandler):
    def process_chunk(self, chunk: Any, iterator: Any = None) -> None:
        self.chunks.append(chunk)


class LlamaIndexAsyncStreamHandler(_BaseLlamaIndexStreamHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk: Any, iterator: Any = None) -> None:
        self.chunks.append(chunk)


def handle_streamed_response(integration, resp, args, kwargs, span, is_chat=True):
    handler = LlamaIndexStreamHandler(integration, span, args, kwargs)
    handler._is_chat = is_chat
    return make_traced_stream(resp, handler)


def handle_async_streamed_response(integration, resp, args, kwargs, span, is_chat=True):
    handler = LlamaIndexAsyncStreamHandler(integration, span, args, kwargs)
    handler._is_chat = is_chat
    return make_traced_stream(resp, handler)


def _process_finished_stream(integration, span, args, kwargs, streamed_chunks, is_chat=True):
    try:
        resp = _construct_response(streamed_chunks, is_chat)
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp)
    except Exception:
        log.warning("Error processing streamed LlamaIndex response.", exc_info=True)


def _construct_response(streamed_chunks, is_chat=True):
    """Construct a response-like object from accumulated stream chunks.

    For chat streams, each chunk is a ChatResponse with message.content and raw.
    For completion streams, each chunk is a CompletionResponse with text and raw.
    The last chunk typically has the full accumulated content and usage info.
    """
    if not streamed_chunks:
        return None

    # LlamaIndex streams accumulate - the last chunk has the full content
    return streamed_chunks[-1]
