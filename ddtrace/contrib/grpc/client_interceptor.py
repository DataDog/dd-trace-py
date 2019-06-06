import collections
import grpc

from ddtrace import config
from ...propagation.http import HTTPPropagator


def client_interceptor_function(pin):
    def interceptor_function(client_call_details, request_iterator,
                             request_streaming, response_streaming):
        if not pin.enabled:
            return client_call_details, request_iterator, None

        with pin.tracer.trace(
                pin.app,
                span_type='grpc',
                service=pin.service,
                resource=client_call_details.method
        ) as span:
            if pin.tags:
                span.set_tags(pin.tags)

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

            return client_call_details, request_iterator, None

    return _GenericClientInterceptor(interceptor_function)


class _ClientCallDetails(
        collections.namedtuple(
            '_ClientCallDetails',
            ('method', 'timeout', 'metadata', 'credentials')),
        grpc.ClientCallDetails):
    pass


class _GenericClientInterceptor(
        grpc.UnaryUnaryClientInterceptor, grpc.UnaryStreamClientInterceptor,
        grpc.StreamUnaryClientInterceptor, grpc.StreamStreamClientInterceptor):

    def __init__(self, interceptor_function):
        self._fn = interceptor_function

    def intercept_unary_unary(self, continuation, client_call_details, request):
        new_details, new_request_iterator, postprocess = self._fn(
            client_call_details, iter((request,)), False, False)
        response = continuation(new_details, next(new_request_iterator))
        return postprocess(response) if postprocess else response

    def intercept_unary_stream(self, continuation, client_call_details,
                               request):
        new_details, new_request_iterator, postprocess = self._fn(
            client_call_details, iter((request,)), False, True)
        response_it = continuation(new_details, next(new_request_iterator))
        return postprocess(response_it) if postprocess else response_it

    def intercept_stream_unary(self, continuation, client_call_details,
                               request_iterator):
        new_details, new_request_iterator, postprocess = self._fn(
            client_call_details, request_iterator, True, False)
        response = continuation(new_details, new_request_iterator)
        return postprocess(response) if postprocess else response

    def intercept_stream_stream(self, continuation, client_call_details,
                                request_iterator):
        new_details, new_request_iterator, postprocess = self._fn(
            client_call_details, request_iterator, True, True)
        response_it = continuation(new_details, new_request_iterator)
        return postprocess(response_it) if postprocess else response_it
