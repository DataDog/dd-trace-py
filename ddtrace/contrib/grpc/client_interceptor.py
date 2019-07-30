import collections
import grpc

from ddtrace import config
from ddtrace.ext import errors
from ...propagation.http import HTTPPropagator
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from . import constants
from .utils import parse_method_path

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


def _tag_rpc_error(span, code, details):
    span.set_tag('rpc_error.status', code)
    span.set_tag('rpc_error.details', details)


def create_client_interceptor(pin, host, port):
    return _ClientInterceptor(pin, host, port)


class _ClientCallDetails(
        collections.namedtuple(
            '_ClientCallDetails',
            ('method', 'timeout', 'metadata', 'credentials')),
        grpc.ClientCallDetails):
    pass


class _ClientInterceptor(
        grpc.UnaryUnaryClientInterceptor, grpc.UnaryStreamClientInterceptor,
        grpc.StreamUnaryClientInterceptor, grpc.StreamStreamClientInterceptor):

    def __init__(self, pin, host, port):
        self._pin = pin
        self._host = host
        self._port = port

    def _intercept_client_call(self, method_kind, client_call_details):
        tracer = self._pin.tracer
        span = tracer.trace(
            'grpc',
            span_type='grpc',
            service=self._pin.service,
            resource=client_call_details.method,
        )

        # tags for method details
        method_path = client_call_details.method
        method_package, method_service, method_name = parse_method_path(method_path)
        span.set_tag(constants.GRPC_METHOD_PATH_KEY, method_path)
        span.set_tag(constants.GRPC_METHOD_PACKAGE_KEY, method_package)
        span.set_tag(constants.GRPC_METHOD_SERVICE_KEY, method_service)
        span.set_tag(constants.GRPC_METHOD_NAME_KEY, method_name)
        span.set_tag(constants.GRPC_METHOD_KIND_KEY, method_kind)
        span.set_tag(constants.GRPC_HOST_KEY, self._host)
        span.set_tag(constants.GRPC_PORT_KEY, self._port)
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.grpc.get_analytics_sample_rate())

        # inject tags from pin
        if self._pin.tags:
            span.set_tags(self._pin.tags)

        # propogate distributed tracing headers if available
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
            client_call_details.credentials,
        )

        return span, client_call_details

    def _response_done_callback(self, span):
        def callback(response):
            exception = response.exception()
            if exception is not None:
                code = response.code()
                details = response.details()
                span.error = 1
                span.set_tag(errors.ERROR_MSG, details)
                span.set_tag(errors.ERROR_TYPE, code)

            span.finish()

        return callback

    def intercept_unary_unary(self, continuation, client_call_details, request):
        span, client_call_details = self._intercept_client_call(
            constants.GRPC_METHOD_KIND_UNARY,
            client_call_details,
        )
        try:
            response = continuation(client_call_details, request)
            response.add_done_callback(self._response_done_callback(span))
        except grpc.RpcError as rpc_error:
            # DEV: grpcio<1.80.0 grpc.RpcError is raised rather than returned as response
            # https://github.com/grpc/grpc/commit/8199aff7a66460fbc4e9a82ade2e95ef076fd8f9
            code = rpc_error.code()
            details = rpc_error.details()
            span.error = 1
            span.set_tag(errors.ERROR_MSG, details)
            span.set_tag(errors.ERROR_TYPE, code)
            span.finish()
            raise

        return response

    def intercept_unary_stream(self, continuation, client_call_details, request):
        span, client_call_details = self._intercept_client_call(
            constants.GRPC_METHOD_KIND_SERVER_STREAMING,
            client_call_details,
        )
        response_iterator = continuation(client_call_details, request)
        response_iterator.add_done_callback(self._response_done_callback(span))
        return response_iterator

    def intercept_stream_unary(self, continuation, client_call_details, request_iterator):
        span, client_call_details = self._intercept_client_call(
            constants.GRPC_METHOD_KIND_CLIENT_STREAMING,
            client_call_details,
        )
        response = continuation(client_call_details, request_iterator)
        response.add_done_callback(self._response_done_callback(span))
        return response

    def intercept_stream_stream(self, continuation, client_call_details, request_iterator):
        span, client_call_details = self._intercept_client_call(
            constants.GRPC_METHOD_KIND_BIDI_STREAMING,
            client_call_details,
        )
        response_iterator = continuation(client_call_details, request_iterator)
        response_iterator.add_done_callback(self._response_done_callback(span))
        return response_iterator
