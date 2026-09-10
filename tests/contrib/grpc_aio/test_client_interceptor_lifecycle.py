import asyncio

import grpc
from grpc import aio
import pytest

from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib.internal.grpc import constants
from ddtrace.contrib.internal.grpc.aio_client_interceptor import _UnaryUnaryClientInterceptor
from ddtrace.contrib.internal.grpc.aio_client_interceptor import _done_callback_stream


class _CancelledDuringCodeCall:
    def __init__(self):
        self._code_calls = 0

    async def code(self):
        self._code_calls += 1
        if self._code_calls == 1:
            raise asyncio.CancelledError()
        return grpc.StatusCode.CANCELLED

    async def details(self):
        return "Locally cancelled by application!"


class _CompletedErrorStreamCall:
    def done(self):
        return True

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
