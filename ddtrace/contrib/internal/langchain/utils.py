import inspect
import sys

from ddtrace.internal import core
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
    pin,
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
        "pin": pin,
        "operation_id": f"{instance.__module__}.{instance.__class__.__name__}",
        "interface_type": interface_type,
        "submit_to_llmobs": True,
        "instance": instance,
    }

    options.update(extra_options)

    span = integration.trace(**options)
    span.set_tag("langchain.request.stream", True)
    on_span_started(span)

    try:
        resp = func(*args, **kwargs)
        chunk_callback = _get_chunk_callback(interface_type, args, kwargs)
        if inspect.isasyncgen(resp):
            return make_traced_stream(
                resp,
                LangchainAsyncStreamHandler(
                    integration, span, args, kwargs, on_span_finish=on_span_finished, chunk_callback=chunk_callback
                ),
            )
        return make_traced_stream(
            resp,
            LangchainStreamHandler(
                integration, span, args, kwargs, on_span_finish=on_span_finished, chunk_callback=chunk_callback
            ),
        )
    except Exception:
        # error with the method call itself
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise


def _get_chunk_callback(interface_type, args, kwargs):
    results = core.dispatch_with_results("langchain.stream.chunk.callback", (interface_type, args, kwargs))
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
