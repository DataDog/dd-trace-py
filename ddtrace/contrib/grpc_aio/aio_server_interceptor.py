from typing import Awaitable
from typing import Callable
from typing import Union

import grpc
from grpc import aio
from grpc.aio._typing import RequestIterableType
from grpc.aio._typing import RequestType
from grpc.aio._typing import ResponseIterableType
from grpc.aio._typing import ResponseType

from ddtrace import Pin
from ddtrace import Span
from ddtrace import config
from ddtrace.vendor import wrapt

from .. import trace_utils
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ..grpc import constants
from ..grpc.utils import set_grpc_method_meta

Continuation = Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]]


def create_aio_server_interceptor(pin):
    # type: (Pin) -> _ServerInterceptor
    async def interceptor_function(
        continuation,  # type: Continuation
        handler_call_details,  # type: grpc.HandlerCallDetails
    ):
        # type: (...) -> Awaitable[Union[grpc.RpcMethodHandler, _TracedRpcMethodHandler]]
        rpc_method_handler = await continuation(handler_call_details)

        # continuation returns an RpcMethodHandler instance if the RPC is
        # considered serviced, or None otherwise
        # https://grpc.github.io/grpc/python/grpc.html#grpc.ServerInterceptor.intercept_service

        if rpc_method_handler:
            return _TracedRpcMethodHandler(pin, handler_call_details, rpc_method_handler)

        return rpc_method_handler

    return _ServerInterceptor(interceptor_function)


async def _wrap_stream_response(
    behavior,  # type: Callable[[Union[RequestIterableType, RequestType], aio.ServicerContext], ResponseIterableType]
    request_or_iterator,  # type: Union[RequestIterableType, RequestType]
    servicer_context,  # type: aio.ServicerContext
    span,  # type: Span
):
    # type: (...) -> RequestIterableType
    try:
        call = behavior(request_or_iterator, servicer_context)
        async for response in call:
            yield response
    except Exception:
        span.set_traceback()
        # https://github.com/grpc/grpc/issues/27409 there seems to be a bug with
        # accessing code and details from server side context
        span.error = 1
        raise
    finally:
        span.finish()


async def _wrap_unary_response(
    behavior,  # type: Callable[[Union[RequestIterableType, RequestType], aio.ServicerContext], Awaitable[ResponseType]]
    request_or_iterator,  # type: Union[RequestIterableType, RequestType]
    servicer_context,  # type: aio.ServicerContext
    span,  # type: Span
):
    # type: (...) -> None
    try:
        return await behavior(request_or_iterator, servicer_context)
    except Exception:
        span.set_traceback()
        # https://github.com/grpc/grpc/issues/27409 there seems to be a bug with
        # accessing code and details from server side context
        span.error = 1
        raise
    finally:
        span.finish()


class _TracedRpcMethodHandler(wrapt.ObjectProxy):
    def __init__(self, pin, handler_call_details, wrapped):
        # type: (Pin, grpc.HandlerCallDetails, grpc.RpcMethodHandler) -> None
        super(_TracedRpcMethodHandler, self).__init__(wrapped)
        self._pin = pin
        self._handler_call_details = handler_call_details

    def _create_span(self, method_kind):
        # type: (str) -> Span
        tracer = self._pin.tracer
        headers = dict(self._handler_call_details.invocation_metadata)

        trace_utils.activate_distributed_headers(tracer, int_config=config.grpc_aio_server, request_headers=headers)

        span = tracer.trace(
            "grpc",
            span_type=SpanTypes.GRPC,
            service=trace_utils.int_service(self._pin, config.grpc_aio_server),
            resource=self._handler_call_details.method,
        )
        span.set_tag(SPAN_MEASURED_KEY)

        set_grpc_method_meta(span, self._handler_call_details.method, method_kind)
        span._set_str_tag(constants.GRPC_SPAN_KIND_KEY, constants.GRPC_SPAN_KIND_VALUE_SERVER)

        sample_rate = config.grpc_aio_server.get_analytics_sample_rate()
        if sample_rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)

        if self._pin.tags:
            span.set_tags(self._pin.tags)

        return span

    async def unary_unary(self, request, context):
        # type: (RequestType, aio.ServicerContext) -> ResponseType
        span = self._create_span(constants.GRPC_METHOD_KIND_UNARY)
        return await _wrap_unary_response(self.__wrapped__.unary_unary, request, context, span)

    async def unary_stream(self, request, context):
        # type: (RequestType, aio.ServicerContext) -> ResponseIterableType
        span = self._create_span(constants.GRPC_METHOD_KIND_SERVER_STREAMING)
        async for response in _wrap_stream_response(self.__wrapped__.unary_stream, request, context, span):
            yield response

    async def stream_unary(self, request_iterator, context):
        # type: (RequestIterableType, aio.ServicerContext) -> ResponseType
        span = self._create_span(constants.GRPC_METHOD_KIND_CLIENT_STREAMING)
        return await _wrap_unary_response(self.__wrapped__.stream_unary, request_iterator, context, span)

    async def stream_stream(self, request_iterator, context):
        # type: (RequestIterableType, aio.ServicerContext) -> ResponseIterableType
        span = self._create_span(constants.GRPC_METHOD_KIND_BIDI_STREAMING)
        async for response in _wrap_stream_response(self.__wrapped__.stream_stream, request_iterator, context, span):
            yield response


class _ServerInterceptor(aio.ServerInterceptor):
    def __init__(self, interceptor_function):
        self._fn = interceptor_function

    async def intercept_service(
        self,
        continuation,  # type: Continuation
        handler_call_details,  # type: grpc.HandlerCallDetails
    ):
        # type: (...) -> Union[grpc.RpcMethodHandler, _TracedRpcMethodHandler]
        return await self._fn(continuation, handler_call_details)
