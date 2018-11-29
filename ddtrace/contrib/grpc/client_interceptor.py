import grpc

from ddtrace import Pin
from .propagation import inject_span


class GrpcClientInterceptor(
        grpc.UnaryUnaryClientInterceptor, grpc.UnaryStreamClientInterceptor,
        grpc.StreamUnaryClientInterceptor, grpc.StreamStreamClientInterceptor):
    """Intercept calls on a channel. It creates span as well as doing the propagation
    Derived from https://github.com/grpc/grpc/blob/d0cb61eada9d270b9043ec866b55c88617d362be/examples/python/interceptors/headers/generic_client_interceptor.py#L19
    """  # noqa

    def __init__(self, host, port):
        self._pin = Pin.get_from(grpc)
        self._host = host
        self._port = port

    def _start_span(self, method):
        span = self._pin.tracer.trace('grpc.client', span_type='grpc', service=self._pin.service, resource=method)
        span.set_tag('grpc.host', self._host)
        if (self._port is not None):
            span.set_tag('grpc.port', self._port)
        if self._pin.tags:
            span.set_tags(self._pin.tags)
        return span

    def intercept_unary_unary(self, continuation, client_call_details, request):
        return self.intercept_unary_stream(continuation, client_call_details, request)

    def intercept_unary_stream(self, continuation, client_call_details, request):
        if not self._pin or not self._pin.enabled():
            return continuation(client_call_details, request)
        with self._start_span(client_call_details.method) as span:
            new_details = inject_span(span, client_call_details)
            try:
                return continuation(new_details, request)
            except Exception:
                span.set_traceback()
                raise

    def intercept_stream_unary(self, continuation, client_call_details, request_iterator):
        return self.intercept_stream_stream(continuation, client_call_details, request_iterator)

    def intercept_stream_stream(self, continuation, client_call_details, request_iterator):
        if not self._pin or not self._pin.enabled():
            return continuation(client_call_details, request_iterator)
        with self._start_span(client_call_details.method) as span:
            new_details = inject_span(span, client_call_details)
            try:
                return continuation(new_details, request_iterator)
            except Exception:
                span.set_traceback()
                raise
