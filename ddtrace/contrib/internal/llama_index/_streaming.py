import inspect
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional
from typing import Union

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations import LlamaIndexIntegration
from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import StreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import make_traced_stream


if TYPE_CHECKING:
    from ddtrace.trace import Span

log = get_logger(__name__)


class _BaseLlamaIndexStreamHandler:
    """Shared finalization logic for sync and async LlamaIndex stream handlers.

    This is a mixin combined with StreamHandler or AsyncStreamHandler,
    which provide the attributes below via BaseStreamHandler.__init__.
    """

    integration: LlamaIndexIntegration
    primary_span: "Span"
    request_args: tuple
    request_kwargs: dict[str, Any]
    chunks: list[Any]

    def finalize_stream(self, exception: Optional[Exception] = None) -> None:
        """Dispatch the deferred ended event with the constructed response.

        The TracingSubscriber's _on_context_ended will set LLMObs tags
        (via on_ended) and finish the span automatically.
        """
        ctx = self.options["ctx"]
        try:
            # LlamaIndex streams accumulate — the last chunk has the full content and usage info.
            ctx.event.response = self.chunks[-1] if self.chunks else None
        except Exception:
            log.warning("Error processing streamed LlamaIndex response.", exc_info=True)
        if exception:
            ctx.dispatch_ended_event(type(exception), exception, exception.__traceback__)
        else:
            ctx.dispatch_ended_event()


class LlamaIndexStreamHandler(_BaseLlamaIndexStreamHandler, StreamHandler):
    def process_chunk(self, chunk: Any, iterator: Any = None) -> None:
        self.chunks.append(chunk)


class LlamaIndexAsyncStreamHandler(_BaseLlamaIndexStreamHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk: Any, iterator: Any = None) -> None:
        self.chunks.append(chunk)


def handle_streamed_response(
    integration: LlamaIndexIntegration,
    resp: Any,
    args: tuple,
    kwargs: dict[str, Any],
    ctx: core.ExecutionContext,
) -> Any:
    """Wrap a sync or async LlamaIndex stream for tracing."""
    handler: Union[LlamaIndexStreamHandler, LlamaIndexAsyncStreamHandler]
    if inspect.isasyncgen(resp):
        handler = LlamaIndexAsyncStreamHandler(integration, ctx.span, args, kwargs, ctx=ctx)
    else:
        handler = LlamaIndexStreamHandler(integration, ctx.span, args, kwargs, ctx=ctx)
    return make_traced_stream(resp, handler)
