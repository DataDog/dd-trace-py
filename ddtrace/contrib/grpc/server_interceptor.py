import grpc

from ddtrace import Pin
from ddtrace.propagation.http import HTTPPropagator, HTTP_HEADER_TRACE_ID, HTTP_HEADER_PARENT_ID, \
    HTTP_HEADER_SAMPLING_PRIORITY
from .interceptors import UnaryUnaryServerInterceptor, UnaryStreamServerInterceptor


class GrpcServerInterceptor(UnaryUnaryServerInterceptor, UnaryStreamServerInterceptor):
    def __init__(self):
        self._pin = Pin.get_from(grpc)

    def _start_span(self, method, context):
        self._pin.tracer.context_provider.activate(self._get_parent_span(context))
        span = self._pin.tracer.trace('grpc.request', span_type='grpc', service=self._pin.service,
                                      resource=self._get_method_name(method))
        if self._pin.tags:
            span.set_tags(self._pin.tags)
        return span

    @staticmethod
    def _get_method_name(method):
        """
        Retrieves the method name from the fullname
        :param method: Method name in the format of /<ServiceName>/<RPCName>
        :return: The RPC Name
        """
        _, rpc_name = str(method).rsplit('/')[1::]
        return rpc_name

    @staticmethod
    def _get_parent_span(context):
        """
        Retrieves the distributed tracing headers from the context sent by the client
        :param context: The servicer context
        :return: Parent context
        """
        metadata = dict(context.invocation_metadata())
        propagator = HTTPPropagator()
        return propagator.extract({
            HTTP_HEADER_TRACE_ID: metadata.get(HTTP_HEADER_TRACE_ID),
            HTTP_HEADER_PARENT_ID: metadata.get(HTTP_HEADER_PARENT_ID),
            HTTP_HEADER_SAMPLING_PRIORITY: metadata.get(HTTP_HEADER_SAMPLING_PRIORITY)
        })

    def intercept_unary_unary_handler(self, handler, method, request, servicer_context):
        span = self._start_span(method, servicer_context)
        try:
            result = handler(request, servicer_context)
            span.finish()
            return result
        except Exception:
            span.set_traceback()
            span.finish()
            raise

    def intercept_unary_stream_handler(self, handler, method, request, servicer_context):
        span = self._start_span(method, servicer_context)
        try:
            result = handler(request, servicer_context)
            for response in result:
                yield response
        except Exception:
            span.set_traceback()
            raise
        span.finish()
