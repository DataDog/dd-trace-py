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

    def start_stream(self):
        # AIDEV-NOTE: dispatched lazily from ``TracedStream.__iter__`` /
        # ``TracedAsyncStream.__aiter__`` (via ``BaseStreamHandler.start_stream``),
        # so it only runs when the caller actually starts iterating. Bumping
        # the AI Guard depth counter here — instead of in the ``.before``
        # listener — means a stream that is created but never consumed cannot
        # leak the counter into the next call in the same task. Paired with
        # the ``.stream.finally`` event below.
        started_event = self.options.get("aiguard_started_event")
        if started_event:
            core.dispatch(started_event, ())

    def finalize_stream(self, exception=None):
        on_span_finish = self.options.get("on_span_finish", None)
        if on_span_finish:
            on_span_finish(self.primary_span, self.chunks)
        # AIDEV-NOTE: dispatch the AI Guard ``.finally`` event before finishing
        # the span so the active-context counter set by ``start_stream`` is
        # released on every iteration-exit path — success, exception, early
        # ``break``, or ``aclose()`` — since ``finalize_stream`` is called
        # from ``TracedStream.__iter__`` / ``__aiter__``'s ``finally`` block.
        # Use ``core.dispatch`` (non-raising) because cleanup must not throw.
        finally_event = self.options.get("aiguard_finally_event")
        if finally_event:
            core.dispatch(finally_event, ())
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
    aiguard_finally_event = options.get("aiguard_finally_event")

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
            aiguard_started_event=aiguard_started_event,
            aiguard_finally_event=aiguard_finally_event,
        )
        if inspect.isasyncgen(resp):
            return make_traced_stream(
                resp,
                LangchainAsyncStreamHandler(integration, span, args, kwargs, **handler_kwargs),
            )
        return make_traced_stream(
            resp,
            LangchainStreamHandler(integration, span, args, kwargs, **handler_kwargs),
        )
    except (DDBlockException, Exception):
        # AIDEV-NOTE: catch ``DDBlockException`` explicitly (parent of
        # ``AIGuardAbortError``) since it inherits from ``BaseException`` —
        # otherwise the AI Guard abort would slip past ``except Exception:``
        # and the LLM span would never get ``set_exc_info`` / ``finish``,
        # leaving a hole between the AI Guard span (block decision) and the
        # LLM span (no link back to the abort). No counter cleanup is needed
        # here: the ``.stream.started`` event is dispatched lazily from the
        # iteration-scoped generator wrappers below, which never run when
        # ``func(...)`` raises before we return a wrapper.
        span.set_exc_info(*sys.exc_info())
        span.finish()
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
