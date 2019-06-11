import collections
import grpc

from ddtrace import config
from ...propagation.http import HTTPPropagator
from ...constants import ANALYTICS_SAMPLE_RATE_KEY

# DEV: Follows Python interceptors RFC laid out in
# https://github.com/grpc/proposal/blob/master/L13-python-interceptors.md

# DEV: __version__ added in v1.21.4
# https://github.com/grpc/grpc/commit/dd4830eae80143f5b0a9a3a1a024af4cf60e7d02

try:
    _GRPC_VERSION = grpc.__version__
except AttributeError:
    import grpc._grpcio_metadata
    _GRPC_VERSION = grpc._grpcio_metadata.__version__
finally:
    _GRPC_VERSION = tuple(int(v) for v in _GRPC_VERSION.split('.')[:3])


def _tag_rpc_error(span, rpc_error):
    span.set_tag('rpc_error.status', str(rpc_error.code()))
    span.set_tag('rpc_error.details', str(rpc_error.details()))


def create_client_interceptor(pin, host, port):
    def interceptor_function(continuation, client_call_details,
                             request_or_iterator):
        if not pin.enabled:
            response = continuation(client_call_details, request_or_iterator)
            return response

        with pin.tracer.trace(
                'grpc.client',
                span_type='grpc',
                service=pin.service,
                resource=client_call_details.method,
        ) as span:
            span.set_tag('grpc.host', host)
            span.set_tag('grpc.port', port)
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.grpc.get_analytics_sample_rate())

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

            try:
                response = continuation(client_call_details, request_or_iterator)

                # DEV: >=1.80.0 grpc.RpcError is caught and returned as response
                # https://github.com/grpc/grpc/commit/8199aff7a66460fbc4e9a82ade2e95ef076fd8f9
                if _GRPC_VERSION >= (1, 18, 0) and isinstance(response, grpc.RpcError):
                    _tag_rpc_error(span, response)

                return response
            except grpc.RpcError as rpc_error:
                _tag_rpc_error(span, rpc_error)
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
