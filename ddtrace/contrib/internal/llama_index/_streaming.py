from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import StreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import make_traced_stream


log = get_logger(__name__)


class _BaseLlamaIndexStreamHandler:
    """Shared finalization logic for sync and async LlamaIndex stream handlers.

    This is a mixin that expects to be combined with StreamHandler or
    AsyncStreamHandler, which provide integration, primary_span, request_args,
    request_kwargs, and chunks attributes via BaseStreamHandler.__init__.
    """

    _is_chat: bool = True

    def finalize_stream(self, exception: Optional[Exception] = None) -> None:
        """Process accumulated chunks and finish the span."""
        _process_finished_stream(
            self.integration,  # type: ignore[attr-defined]
            self.primary_span,  # type: ignore[attr-defined]
            self.request_args,  # type: ignore[attr-defined]
            self.request_kwargs,  # type: ignore[attr-defined]
            self.chunks,  # type: ignore[attr-defined]
            is_chat=self._is_chat,
        )
        self.primary_span.finish()  # type: ignore[attr-defined]


class LlamaIndexStreamHandler(_BaseLlamaIndexStreamHandler, StreamHandler):
    def process_chunk(self, chunk: Any, iterator: Any = None) -> None:
        self.chunks.append(chunk)


class LlamaIndexAsyncStreamHandler(_BaseLlamaIndexStreamHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk: Any, iterator: Any = None) -> None:
        self.chunks.append(chunk)


def handle_streamed_response(integration, resp, args, kwargs, span, is_chat=True):
    """Wrap a sync LlamaIndex stream for tracing."""
    handler = LlamaIndexStreamHandler(integration, span, args, kwargs)
    handler._is_chat = bool(is_chat)
    return make_traced_stream(resp, handler)


def handle_async_streamed_response(integration, resp, args, kwargs, span, is_chat=True):
    """Wrap an async LlamaIndex stream for tracing."""
    handler = LlamaIndexAsyncStreamHandler(integration, span, args, kwargs)
    handler._is_chat = bool(is_chat)
    return make_traced_stream(resp, handler)


def _process_finished_stream(integration, span, args, kwargs, streamed_chunks, is_chat=True):
    """Process chunks from a finished stream and set LLMObs tags on the span."""
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
