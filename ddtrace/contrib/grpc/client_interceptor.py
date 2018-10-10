import grpc
import collections

from ddtrace import Pin

class _ClientCallDetails(
        collections.namedtuple(
            '_ClientCallDetails',
            ('method', 'timeout', 'metadata', 'credentials')),
        grpc.ClientCallDetails):
    pass

class _GrpcClientInterceptor(
        grpc.UnaryUnaryClientInterceptor, grpc.UnaryStreamClientInterceptor,
        grpc.StreamUnaryClientInterceptor, grpc.StreamStreamClientInterceptor):

    def __init__(self, host, port):
        pin = Pin.get_from(grpc)
        self._service = pin.service
        self._tracer = pin.tracer
        self._host = host
        self._port = port

    def _start_span(self, method):
        span = self._tracer.trace('grpc.client', span_type='grpc', service=self._service, resource=method)
        span.set_tag('grpc.host', self._host)
        if (self._port is not None):
            span.set_tag('grpc.port', self._port)
        return span

    def _inject_span(self, span, client_call_details):
        metadata = []
        if client_call_details.metadata is not None:
            metadata = list(client_call_details.metadata)
        metadata.append((b'x-datadog-trace-id', str(span.trace_id)))
        metadata.append((b'x-datadog-parent-id', str(span.span_id)))

        if (span.context.sampling_priority) is not None:
            metadata.append((b'x-datadog-sampling-priority', str(span.context.sampling_priority)))
        client_call_details = _ClientCallDetails(
            client_call_details.method, client_call_details.timeout, metadata,
            client_call_details.credentials)
        return client_call_details

    def intercept_unary_unary(self, continuation, client_call_details, request):
        return self.intercept_unary_stream(continuation, client_call_details, request)

    def intercept_unary_stream(self, continuation, client_call_details, request):
        with self._start_span(client_call_details.method) as span:
            new_details = self._inject_span(span, client_call_details)
            try:
                return continuation(new_details, request)
            except:
                span.set_traceback()
                raise

    def intercept_stream_unary(self, continuation, client_call_details, request_iterator):
        return self.intercept_stream_stream(continuation, client_call_details, request_iterator)

    def intercept_stream_stream(self, continuation, client_call_details, request_iterator):
        with self._start_span(client_call_details.method) as span:
            new_details = self._inject_span(span, client_call_details)
            try:
                response_iterator = continuation(new_details, request_iterator)
                return response_iterator
            except:
                span.set_traceback()
                raise

def create(host, port):
    return _GrpcClientInterceptor(host, port)
