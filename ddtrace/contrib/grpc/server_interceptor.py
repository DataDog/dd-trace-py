import grpc
from ddtrace.vendor import wrapt

from ddtrace import config

from ...compat import to_unicode
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...propagation.http import HTTPPropagator


def create_server_interceptor(pin):
    def interceptor_function(continuation, handler_call_details):
        if not pin.enabled:
            return continuation(handler_call_details)

        rpc_method_handler = continuation(handler_call_details)
        wrapper = _wrapper(pin, handler_call_details)
        return _TracedRpcMethodHandler(rpc_method_handler, wrapper)

    return _ServerInterceptor(interceptor_function)


def _wrapper(pin, handler_call_details):
    def wrapped(func, instance, fargs, fkwargs):
        if config.grpc.distributed_tracing_enabled:
            headers = dict(handler_call_details.invocation_metadata)
            propagator = HTTPPropagator()
            context = propagator.extract(headers)

            if context.trace_id:
                pin.tracer.context_provider.activate(context)

        with pin.tracer.trace(
                'grpc.server',
                span_type='grpc',
                service=pin.service,
                resource=handler_call_details.method,
        ) as span:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.grpc.get_analytics_sample_rate())

            if pin.tags:
                span.set_tags(pin.tags)

            try:
                response = func(*fargs, **fkwargs)
                return response
            except Exception:
                # DEV: access state code from internal gRPC state of context
                # DEV: context is second positional argument to func
                context = fargs[1]
                span.set_tag('rpc_error.status', context._state.code)
                # DEV: details can be encoded as bytes with PY3 so convert first
                span.set_tag('rpc_error.details', to_unicode(context._state.details))
                raise

    return wrapped


class _TracedRpcMethodHandler(wrapt.ObjectProxy):
    def __init__(self, wrapped, wrapper):
        super(_TracedRpcMethodHandler, self).__init__(wrapped)
        self._wrapper = wrapper

    def unary_unary(self, *args, **kwargs):
        return self._wrapper(self.__wrapped__.unary_unary, self.__wrapped__, args, kwargs)

    def unary_stream(self, *args, **kwargs):
        return self._wrapper(self.__wrapped__.unary_stream, self.__wrapped__, args, kwargs)

    def stream_unary(self, *args, **kwargs):
        return self._wrapper(self.__wrapped__.stream_unary, self.__wrapped__, args, kwargs)

    def stream_stream(self, *args, **kwargs):
        return self._wrapper(self.__wrapped__.stream_stream, self.__wrapped__, args, kwargs)


class _ServerInterceptor(grpc.ServerInterceptor):
    def __init__(self, interceptor_function):
        self._fn = interceptor_function

    def intercept_service(self, continuation, handler_call_details):
        return self._fn(continuation, handler_call_details)
