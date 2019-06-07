import collections
import grpc

from ddtrace import config
from ...propagation.http import HTTPPropagator
from ...constants import ANALYTICS_SAMPLE_RATE_KEY

# DEV: Follows Python interceptors RFC laid out in
# https://github.com/grpc/proposal/blob/master/L13-python-interceptors.md


def create_client_interceptor(pin):
    def interceptor_function(continuation, client_call_details,
                             request_or_iterator):
        if not pin.enabled:
            response = continuation(client_call_details, request_or_iterator)
            return response

        with pin.tracer.trace(
                '{}.client'.format(pin.app),
                span_type=config.grpc.span_type,
                service=pin.service,
                resource=client_call_details.method
        ) as span:
            if pin.tags:
                span.set_tags(pin.tags)

            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.grpc.get_analytics_sample_rate())

            headers = {}
            if config.grpc.distributed_tracing_enabled:
                propagator = HTTPPropagator()
                propagator.inject(span.context, headers)

            metadata = []
            if client_call_details.metadata is not None:
                metadata = list(client_call_details.metadata)
            metadata.extend(headers.items())

            client_call_details = _ClientCallDetails(
                client_call_details.method,
                client_call_details.timeout,
                metadata,
                client_call_details.credentials
            )

            try:
                response = continuation(client_call_details, request_or_iterator)
                return response
            except Exception:
                span.set_traceback()
                raise

    return _ClientInterceptor(interceptor_function)


class _ClientCallDetails(
        collections.namedtuple(
            '_ClientCallDetails',
            ('method', 'timeout', 'metadata', 'credentials')),
        grpc.ClientCallDetails):
    pass


class _ClientInterceptor(
        grpc.UnaryUnaryClientInterceptor, grpc.UnaryStreamClientInterceptor,
        grpc.StreamUnaryClientInterceptor, grpc.StreamStreamClientInterceptor):

    def __init__(self, interceptor_function):
        self._fn = interceptor_function

    def intercept_unary_unary(self, continuation, client_call_details, request):
        return self._fn(continuation, client_call_details, request)

    def intercept_unary_stream(self, continuation, client_call_details, request):
        return self._fn(continuation, client_call_details, request)

    def intercept_stream_unary(self, continuation, client_call_details, request_iterator):
        return self._fn(continuation, client_call_details, request_iterator)

    def intercept_stream_stream(self, continuation, client_call_details, request_iterator):
        return self._fn(continuation, client_call_details, request_iterator)
