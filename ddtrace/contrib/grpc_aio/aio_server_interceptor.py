from grpc import aio

from ddtrace import config
from ddtrace.vendor import wrapt

from .. import trace_utils
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ..grpc import constants
from ..grpc.utils import set_grpc_method_meta


def create_aio_server_interceptor(pin):
    async def interceptor_function(continuation, handler_call_details):

        rpc_method_handler = await continuation(handler_call_details)

        # continuation returns an RpcMethodHandler instance if the RPC is
        # considered serviced, or None otherwise
        # https://grpc.github.io/grpc/python/grpc.html#grpc.ServerInterceptor.intercept_service

        if rpc_method_handler:
            return _TracedRpcMethodHandler(pin, handler_call_details, rpc_method_handler)

        return rpc_method_handler

    return _ServerInterceptor(interceptor_function)


class _TracedRpcMethodHandler(wrapt.ObjectProxy):
    def __init__(self, pin, handler_call_details, wrapped):
        super(_TracedRpcMethodHandler, self).__init__(wrapped)
        self._pin = pin
        self._handler_call_details = handler_call_details

    async def _fn(self, method_kind, behavior, args, kwargs):
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
        try:
            response_or_iterator = await behavior(*args, **kwargs)
        except Exception:
            span.set_traceback()
            # https://github.com/grpc/grpc/issues/27409 there seems to be a bug with
            # accessing code and details from server side context
            span.error = 1
            raise
        finally:
            span.finish()

        return response_or_iterator

    async def unary_unary(self, *args, **kwargs):
        return await self._fn(constants.GRPC_METHOD_KIND_UNARY, self.__wrapped__.unary_unary, args, kwargs)

    async def unary_stream(self, *args, **kwargs):
        return await self._fn(constants.GRPC_METHOD_KIND_SERVER_STREAMING, self.__wrapped__.unary_stream, args, kwargs)

    async def stream_unary(self, *args, **kwargs):
        return await self._fn(constants.GRPC_METHOD_KIND_CLIENT_STREAMING, self.__wrapped__.stream_unary, args, kwargs)

    async def stream_stream(self, *args, **kwargs):
        return await self._fn(constants.GRPC_METHOD_KIND_BIDI_STREAMING, self.__wrapped__.stream_stream, args, kwargs)


class _ServerInterceptor(aio.ServerInterceptor):
    def __init__(self, interceptor_function):
        self._fn = interceptor_function

    async def intercept_service(self, continuation, handler_call_details):
        return await self._fn(continuation, handler_call_details)
