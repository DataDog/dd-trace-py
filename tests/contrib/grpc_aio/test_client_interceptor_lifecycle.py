import asyncio

import grpc
from grpc import aio
import pytest

from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib.internal.grpc import constants
from ddtrace.contrib.internal.grpc.aio_client_interceptor import _done_callback_stream
from ddtrace.contrib.internal.grpc.aio_client_interceptor import _StreamStreamClientInterceptor
from ddtrace.contrib.internal.grpc.aio_client_interceptor import _UnaryStreamClientInterceptor
from ddtrace.contrib.internal.grpc.aio_client_interceptor import _UnaryUnaryClientInterceptor
from ddtrace.contrib.internal.grpc.patch import patch
from ddtrace.contrib.internal.grpc.patch import unpatch


class _CancelledDuringCodeCall:
    def __init__(self):
        self._code_calls = 0

    def cancel(self):
        return True

    async def code(self):
        self._code_calls += 1
        if self._code_calls == 1:
            raise asyncio.CancelledError()
        return grpc.StatusCode.CANCELLED

    async def details(self):
        return "Locally cancelled by application!"


class _CompletedStreamCall:
    def add_done_callback(self, callback):
        callback(self)

    def done(self):
        return True

    def __repr__(self):
        return 'status = StatusCode.OK, details = "complete"'


class _CompletedErrorStreamCall(_CompletedStreamCall):
    def __repr__(self):
        return 'status = StatusCode.INVALID_ARGUMENT, details = "stream failed"'

    async def code(self):
        return grpc.StatusCode.INVALID_ARGUMENT

    async def details(self):
        return "stream failed"


def _written_spans(tracer):
    return tracer._span_aggregator.writer.spans


async def test_unary_cancellation_sets_error_metadata_before_finishing(tracer):
    interceptor = _UnaryUnaryClientInterceptor("localhost", 50051)
    span = tracer.trace("grpc")
    call = _CancelledDuringCodeCall()

    async def continuation():
        return call

    with pytest.raises(asyncio.CancelledError):
        await interceptor._wrap_unary_response(continuation, span)

    assert span.error == 1
    assert span.get_tag(constants.GRPC_STATUS_CODE_KEY) == "StatusCode.CANCELLED"
    assert span.get_tag(ERROR_TYPE) == "StatusCode.CANCELLED"
    assert span.get_tag(ERROR_MSG) == "Locally cancelled by application!"
    assert span in _written_spans(tracer)


@pytest.mark.parametrize(
    "interceptor,intercept_method,request_arg",
    [
        (_UnaryStreamClientInterceptor("localhost", 50051), "intercept_unary_stream", object()),
        (_StreamStreamClientInterceptor("localhost", 50051), "intercept_stream_stream", iter([object()])),
    ],
)
async def test_streaming_uniterated_call_finishes_client_span(tracer, interceptor, intercept_method, request_arg):
    call = _CompletedStreamCall()

    async def continuation(client_call_details, request):
        return call

    client_call_details = aio.ClientCallDetails(
        b"/helloworld.Hello/SayHelloTwice",
        None,
        None,
        None,
        None,
    )
    await getattr(interceptor, intercept_method)(continuation, client_call_details, request_arg)

    client_spans = [span for span in _written_spans(tracer) if span.service == "grpc-aio-client"]
    assert len(client_spans) == 1
    assert client_spans[0].get_tag(constants.GRPC_STATUS_CODE_KEY) == "StatusCode.OK"


async def test_unconsumed_non_ok_stream_callback_finishes_with_error_metadata(tracer):
    span = tracer.trace("grpc")
    call = _CompletedErrorStreamCall()

    _done_callback_stream(span)(call)
    await asyncio.sleep(0)

    assert span.error == 1
    assert span.get_tag(constants.GRPC_STATUS_CODE_KEY) == "StatusCode.INVALID_ARGUMENT"
    assert span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
    assert span.get_tag(ERROR_MSG) == "stream failed"
    assert span in _written_spans(tracer)


@pytest.fixture(autouse=True)
def patch_grpc_aio():
    patch()
    yield
    unpatch()


async def test_unary_cancellation_does_not_wait_for_server(tracer):
    started = asyncio.Event()
    release = asyncio.Event()

    async def unary(request, context):
        started.set()
        await release.wait()
        return b"response"

    server = aio.server()
    server.add_generic_rpc_handlers(
        (grpc.method_handlers_generic_handler("test.Service", {"Unary": grpc.unary_unary_rpc_method_handler(unary)}),)
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            call = channel.unary_unary("/test.Service/Unary")(b"request")

            async def consume():
                return await call

            consumer = asyncio.create_task(consume())
            try:
                await asyncio.wait_for(started.wait(), 5)
                assert call.cancel()
                done, _ = await asyncio.wait((consumer,), timeout=5)
                cancelled_promptly = consumer in done and consumer.cancelled()
            finally:
                release.set()
                await asyncio.gather(consumer, return_exceptions=True)
    finally:
        await server.stop(None)

    assert cancelled_promptly, "cancellation waited for the server to finish"
    spans = [span for span in _written_spans(tracer) if span.service == "grpc-aio-client"]
    assert len(spans) == 1
    assert spans[0].get_tag(constants.GRPC_STATUS_CODE_KEY) == "StatusCode.CANCELLED"


async def test_unconsumed_exception_stream_keeps_error_metadata(tracer):
    async def stream(request, context):
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "stream failed")
        yield b"unreachable"

    server = aio.server()
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                "test.Service", {"Stream": grpc.unary_stream_rpc_method_handler(stream)}
            ),
        )
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            call = channel.unary_stream("/test.Service/Stream")(b"request")
            assert await call.code() == grpc.StatusCode.INVALID_ARGUMENT
            await asyncio.sleep(0)
            await asyncio.sleep(0)
    finally:
        await server.stop(None)

    spans = [span for span in tracer._span_aggregator.writer.spans if span.service == "grpc-aio-client"]
    assert len(spans) == 1
    assert spans[0].error == 1
    assert spans[0].get_tag(constants.GRPC_STATUS_CODE_KEY) == "StatusCode.INVALID_ARGUMENT"
    assert spans[0].get_tag(ERROR_MSG) == "stream failed"
