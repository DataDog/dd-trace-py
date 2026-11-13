from typing import Any
from typing import Optional

from ddtrace.contrib.internal.stream_handler import AsyncStreamHandler
from ddtrace.contrib.internal.stream_handler import StreamHandler


class BaseVertexAIStreamHandler:
    """Mixed into StreamHandler/AsyncStreamHandler, which provide chunks and options."""

    chunks: list[Any]
    options: dict[str, Any]

    def _process_chunk(self, chunk):
        # only keep track of the first chunk for chat messages since
        # it is modified during the streaming process
        if not self.options.get("is_chat", False) or not self.chunks:
            self.chunks.append(chunk)

    def finalize_stream(self, exception: Optional[BaseException] = None) -> None:
        """Dispatch the deferred ended event with the streamed chunks, which finishes the span."""
        ctx = self.options["ctx"]
        ctx.event.response = self.chunks
        if exception:
            ctx.dispatch_ended_event(type(exception), exception, exception.__traceback__)
        else:
            ctx.dispatch_ended_event()


class VertexAIStreamHandler(BaseVertexAIStreamHandler, StreamHandler):
    def process_chunk(self, chunk, iterator=None):
        self._process_chunk(chunk)


class VertexAIAsyncStreamHandler(BaseVertexAIStreamHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk, iterator=None):
        self._process_chunk(chunk)
