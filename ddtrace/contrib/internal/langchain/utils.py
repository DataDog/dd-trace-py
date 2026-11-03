import inspect
import sys

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils.streaming import AsyncStreamHandler
from ddtrace.internal.utils.streaming import StreamHandler
from ddtrace.internal.utils.streaming import make_traced_stream


log = get_logger(__name__)


class BaseLangchainStreamHandler:
    def _process_chunk(self, chunk):
        self.chunks.append(chunk)
        chunk_callback = self.options.get("chunk_callback", None)
        if chunk_callback:
            chunk_callback(chunk)

    def _event_state(self):
        # Per-stream state a listener keeps between .stream.started and .stream.finally; the two can run in
        # different asyncio tasks, so it travels with the stream rather than with the current context.
        state = getattr(self, "_stream_event_state", None)
        if state is None:
            state = self._stream_event_state = {}
        return state

    def start_stream(self):
        # dispatched lazily from ``TracedStream.__iter__`` /
        # ``TracedAsyncStream.__aiter__`` (via ``BaseStreamHandler.start_stream``),
        # so it only runs when the caller actually starts iterating. Claiming
        # the AI Guard context here, instead of in the .before
        # listener, means a stream that is created but never consumed cannot
        # leak a claim into the next call in the same task. Paired with
        # the ``.stream.finally`` event below.
        self._stream_started = True
        started_event = self.options.get("aiguard_started_event")
        if started_event:
            core.dispatch(started_event, (self._event_state(),))

    def finalize_stream(self, exception=None):
        """Set the joined response on the event, then dispatch the deferred ended event.

        The tracing subscriber sets the LLMObs tags (via the SPAN_FINISHING subscribers) and
        finishes the span.
        """
        ctx = self.options["ctx"]
        on_stream_finished = self.options.get("on_stream_finished", None)
        if on_stream_finished:
            try:
                ctx.event.response = on_stream_finished(self.chunks)
            except Exception:
                log.warning("Error processing streamed LangChain response.", exc_info=True)
        # Dispatch the AI Guard finally event before finishing the span so
        # the active-context counter set by start_stream is released on every
        # exit path: success, exception, early break, aclose, or
        # context-manager exit. close_stream calls finalize_stream at most
        # once from TracedStream iteration cleanup, context-manager exit, and
        # GC. Only pair finally with a start that actually ran: otherwise a
        # never-iterated stream would decrement an enclosing AI Guard context.
        # Use core.dispatch (non-raising) because cleanup must not throw.
        finally_event = self.options.get("aiguard_finally_event")
        if finally_event and getattr(self, "_stream_started", False):
            core.dispatch(finally_event, (self._event_state(),))
        if exception:
            ctx.dispatch_ended_event(type(exception), exception, exception.__traceback__)
        else:
            ctx.dispatch_ended_event()


class LangchainStreamHandler(BaseLangchainStreamHandler, StreamHandler):
    def process_chunk(self, chunk, iterator=None):
        self._process_chunk(chunk)


class LangchainAsyncStreamHandler(BaseLangchainStreamHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk, iterator=None):
        self._process_chunk(chunk)


def shared_stream(
    event,
    func,
    instance,
    args,
    kwargs,
    interface_type,
    on_stream_finished,
    aiguard_before_event=None,
    aiguard_started_event=None,
    aiguard_finally_event=None,
):
    # dispatch_end_event=False defers the ended event until the stream handler calls
    # ctx.dispatch_ended_event() in finalize_stream(). Errors before the wrapped stream is returned
    # must dispatch it manually so the span finishes with the error info.
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            # dispatch AI Guard hook after span is created so blocked requests still emit LLMObs span
            if aiguard_before_event:
                core.dispatch(aiguard_before_event, (instance, args, kwargs), allow_raise=True)
            resp = func(*args, **kwargs)
            chunk_callback = _get_chunk_callback(interface_type, args, kwargs)
            handler_kwargs = dict(
                ctx=ctx,
                on_stream_finished=on_stream_finished,
                chunk_callback=chunk_callback,
                aiguard_started_event=aiguard_started_event,
                aiguard_finally_event=aiguard_finally_event,
            )
            handler_cls = LangchainAsyncStreamHandler if inspect.isasyncgen(resp) else LangchainStreamHandler
            return make_traced_stream(resp, handler_cls(None, span_from_context(ctx), args, kwargs, **handler_kwargs))
        except BaseException:
            # BaseException covers ``DDBlockException`` (parent of ``AIGuardAbortError``),
            # so an AI Guard abort still finishes the LLM span with the error, keeping
            # the link between the AI Guard span (block decision) and the LLM span.
            # No counter cleanup is needed here: ``.stream.started`` is dispatched
            # lazily by ``start_stream`` on iteration entry, which never runs when
            # ``func(...)`` raises before we return a stream wrapper.
            ctx.dispatch_ended_event(*sys.exc_info())
            raise


def _get_chunk_callback(interface_type, args, kwargs):
    results = core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
        "langchain.stream.chunk.callback", (interface_type, args, kwargs)
    )
    callbacks = []
    for result in results.values():
        if result and result.value:
            callbacks.append(result.value)
    return _build_chunk_callback(callbacks)


def _build_chunk_callback(callbacks):
    if not callbacks:
        return _no_op_callback

    def _chunk_callback(chunk):
        for callback in callbacks:
            callback(chunk)
        return chunk

    return _chunk_callback


def _no_op_callback(chunk):
    pass
