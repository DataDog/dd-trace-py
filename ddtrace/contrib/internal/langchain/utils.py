import inspect
import sys

from ddtrace.internal import core
from ddtrace.internal._exceptions import DDBlockException
from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import StreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import make_traced_stream


class BaseLangchainStreamHandler:
    def _process_chunk(self, chunk):
        self.chunks.append(chunk)
        chunk_callback = self.options.get("chunk_callback", None)
        if chunk_callback:
            chunk_callback(chunk)

    def finalize_stream(self, exception=None):
        on_span_finish = self.options.get("on_span_finish", None)
        if on_span_finish:
            on_span_finish(self.primary_span, self.chunks)
        self.primary_span.finish()


class LangchainStreamHandler(BaseLangchainStreamHandler, StreamHandler):
    def process_chunk(self, chunk, iterator=None):
        self._process_chunk(chunk)


class LangchainAsyncStreamHandler(BaseLangchainStreamHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk, iterator=None):
        self._process_chunk(chunk)


def shared_stream(
    integration,
    func,
    instance,
    args,
    kwargs,
    interface_type,
    on_span_started,
    on_span_finished,
    **extra_options,
):
    options = {
        "operation_id": f"{instance.__module__}.{instance.__class__.__name__}",
        "interface_type": interface_type,
        "submit_to_llmobs": True,
        "instance": instance,
    }

    options.update(extra_options)

    aiguard_before_event = options.pop("aiguard_before_event", None)
    aiguard_started_event = options.pop("aiguard_started_event", None)
    aiguard_finally_event = options.pop("aiguard_finally_event", None)

    span = integration.trace(**options)
    span.set_tag("langchain.request.stream", "True")
    on_span_started(span)

    try:
        # dispatch AI Guard hook after span is created so blocked requests still emit LLMObs span
        if aiguard_before_event:
            core.dispatch(aiguard_before_event, (instance, args, kwargs), allow_raise=True)
        resp = func(*args, **kwargs)
        chunk_callback = _get_chunk_callback(interface_type, args, kwargs)
        handler_kwargs = dict(
            on_span_finish=on_span_finished,
            chunk_callback=chunk_callback,
        )
        is_async = inspect.isasyncgen(resp)
        if aiguard_started_event and core.has_listeners(aiguard_started_event):
            read_events = (aiguard_started_event, aiguard_finally_event)
            resp = (
                _dispatch_around_async_reads(resp, *read_events)
                if is_async
                else _dispatch_around_reads(resp, *read_events)
            )
        if is_async:
            return make_traced_stream(
                resp,
                LangchainAsyncStreamHandler(integration, span, args, kwargs, **handler_kwargs),
            )
        return make_traced_stream(
            resp,
            LangchainStreamHandler(integration, span, args, kwargs, **handler_kwargs),
        )
    except (DDBlockException, Exception):
        # catch ``DDBlockException`` explicitly (parent of
        # ``AIGuardAbortError``) since it inherits from ``BaseException`` —
        # otherwise the AI Guard abort would slip past ``except Exception:``
        # and the LLM span would never get ``set_exc_info`` / ``finish``,
        # leaving a hole between the AI Guard span (block decision) and the
        # LLM span (no link back to the abort). Nothing to release here: the read
        # events run only while the returned stream is read.
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise


def _dispatch_around_reads(stream, started_event, finally_event):
    """Dispatch started_event before each read of stream and finally_event once the read returns or raises.

    Both run in the frame that reads, never across the caller's loop body, and
    a stream that is created but never read dispatches nothing.
    """
    iterator = iter(stream)
    try:
        while True:
            core.dispatch(started_event, ())
            try:
                item = next(iterator)
            except StopIteration:
                return
            finally:
                core.dispatch(finally_event, ())
            yield item
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            close()


async def _dispatch_around_async_reads(stream, started_event, finally_event):
    """Async twin of _dispatch_around_reads."""
    iterator = stream.__aiter__()
    try:
        while True:
            core.dispatch(started_event, ())
            try:
                item = await iterator.__anext__()
            except StopAsyncIteration:
                return
            finally:
                core.dispatch(finally_event, ())
            yield item
    finally:
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            await aclose()


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
