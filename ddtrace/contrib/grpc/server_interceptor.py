import grpc
import sys
from ddtrace.vendor import wrapt

from ddtrace import config

from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...propagation.http import HTTPPropagator
from . import constants
from .utils import parse_method_path


def create_server_interceptor(pin):
    def interceptor_function(continuation, handler_call_details):
        if not pin.enabled:
            return continuation(handler_call_details)

        rpc_method_handler = continuation(handler_call_details)
        return _TracedRpcMethodHandler(pin, handler_call_details, rpc_method_handler)

    return _ServerInterceptor(interceptor_function)


def _wrap_response_iterator(response_iterator, span):
    try:
        for response in response_iterator:
            yield response
    except Exception:
        exc_type, exc_val, exc_tb = sys.exc_info()
        span.set_exc_info(exc_type, exc_val, exc_tb)
        raise
    finally:
        span.finish()


class _TracedRpcMethodHandler(wrapt.ObjectProxy):
    def __init__(self, pin, handler_call_details, wrapped):
        super(_TracedRpcMethodHandler, self).__init__(wrapped)
        self._pin = pin
        self._handler_call_details = handler_call_details

    def _fn(self, method_kind, func, args, kwargs):
        if config.grpc.distributed_tracing_enabled:
            headers = dict(self._handler_call_details.invocation_metadata)
            propagator = HTTPPropagator()
            context = propagator.extract(headers)

            if context.trace_id:
                self._pin.tracer.context_provider.activate(context)

        tracer = self._pin.tracer
        span = tracer.trace(
            'grpc',
            span_type='grpc',
            service=self._pin.service,
            resource=self._handler_call_details.method,
        )

        method_path = self._handler_call_details.method
        method_package, method_service, method_name = parse_method_path(method_path)
        span.set_tag(constants.GRPC_METHOD_PATH_KEY, method_path)
        span.set_tag(constants.GRPC_METHOD_PACKAGE_KEY, method_package)
        span.set_tag(constants.GRPC_METHOD_SERVICE_KEY, method_service)
        span.set_tag(constants.GRPC_METHOD_NAME_KEY, method_name)
        span.set_tag(constants.GRPC_METHOD_KIND_KEY, method_kind)
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.grpc.get_analytics_sample_rate())

        if self._pin.tags:
            span.set_tags(self._pin.tags)

        try:
            response_or_iterator = func(*args, **kwargs)

            if self.__wrapped__.response_streaming:
                response_or_iterator = _wrap_response_iterator(response_or_iterator, span)
        except Exception:
            exc_type, exc_val, exc_tb = sys.exc_info()
            span.set_exc_info(exc_type, exc_val, exc_tb)
            raise
        finally:
            if not self.__wrapped__.response_streaming:
                span.finish()

        return response_or_iterator

    def unary_unary(self, *args, **kwargs):
        return self._fn(
            constants.GRPC_METHOD_KIND_UNARY,
            self.__wrapped__.unary_unary,
            args,
            kwargs
        )

    def unary_stream(self, *args, **kwargs):
        return self._fn(
            constants.GRPC_METHOD_KIND_SERVER_STREAMING,
            self.__wrapped__.unary_stream,
            args,
            kwargs
        )

    def stream_unary(self, *args, **kwargs):
        return self._fn(
            constants.GRPC_METHOD_KIND_CLIENT_STREAMING,
            self.__wrapped__.stream_unary,
            args,
            kwargs
        )

    def stream_stream(self, *args, **kwargs):
        return self._fn(
            constants.GRPC_METHOD_KIND_BIDI_STREAMING,
            self.__wrapped__.stream_stream,
            args,
            kwargs
        )


class _ServerInterceptor(grpc.ServerInterceptor):
    def __init__(self, interceptor_function):
        self._fn = interceptor_function

    def intercept_service(self, continuation, handler_call_details):
        return self._fn(continuation, handler_call_details)
