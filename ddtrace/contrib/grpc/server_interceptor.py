import grpc

from ddtrace import config
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...propagation.http import HTTPPropagator


def create_server_interceptor(pin):
    def interceptor_function(continuation, handler_call_details):
        if not pin.enabled:
            response = continuation(handler_call_details)
            return response

        if config.grpc.distributed_tracing_enabled:
            headers = dict(handler_call_details.invocation_metadata)
            propagator = HTTPPropagator()
            context = propagator.extract(headers)

            if context.trace_id:
                pin.tracer.context_provider.activate(context)

        with pin.tracer.trace(
                '{}.server'.format(pin.app),
                span_type=config.grpc.span_type,
                service=pin.service,
                resource=handler_call_details.method
        ) as span:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.grpc.get_analytics_sample_rate())

            try:
                response = continuation(handler_call_details)
                return response
            except Exception:
                span.set_traceback()
                raise

    return _ServerInterceptor(interceptor_function)


class _ServerInterceptor(grpc.ServerInterceptor):
    def __init__(self, interceptor_function):
        self._fn = interceptor_function

    def intercept_service(self, continuation, handler_call_details):
        return self._fn(continuation, handler_call_details)
