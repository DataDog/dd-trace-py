import grpc

from ddtrace import Pin
from ...propagation.http import HTTPPropagator

class GrpcServerInterceptor(grpc.ServerInterceptor):

    def __init__(self):
        self._pin = Pin.get_from(grpc)
        self._propagator = HTTPPropagator()

    def intercept_service(self, continuation, handler_call_details):
        if not self._pin or not self._pin.enabled():
            return continuation(handler_call_details)

        self.extract_and_set_context(handler_call_details.invocation_metadata)

        with self.start_span(handler_call_details.method) as span:
            try:
                return continuation(handler_call_details)
            except:
                span.set_traceback()
                raise

    def extract_and_set_context(self, metadata):

        context = self._propagator.extract(dict(metadata))
        if context.trace_id:
            self._pin.tracer.context_provider.activate(context)

    def start_span(self, method):
        return self._pin.tracer.trace('grpc.request', service=self._pin.service, resource=method, span_type='grpc')