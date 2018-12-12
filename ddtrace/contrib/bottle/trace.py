# 3p
from bottle import response, request

# stdlib
import ddtrace
from ddtrace.ext import http, AppTypes

# project
from ...propagation.http import HTTPPropagator

SPAN_TYPE = 'web'


class TracePlugin(object):
    name = 'trace'
    api = 2

    def __init__(self, service='bottle', tracer=None, distributed_tracing=None):
        self.service = service
        self.tracer = tracer or ddtrace.tracer
        self.distributed_tracing = distributed_tracing
        self.tracer.set_service_info(
            service=service,
            app='bottle',
            app_type=AppTypes.web,
        )

    def apply(self, callback, route):

        def wrapped(*args, **kwargs):
            if not self.tracer or not self.tracer.enabled:
                return callback(*args, **kwargs)

            resource = '{} {}'.format(request.method, route.rule)

            # Propagate headers such as x-datadog-trace-id.
            if self.distributed_tracing:
                propagator = HTTPPropagator()
                context = propagator.extract(request.headers)
                if context.trace_id:
                    self.tracer.context_provider.activate(context)

            with self.tracer.trace('bottle.request', service=self.service, resource=resource, span_type=SPAN_TYPE) as s:
                code = 0
                try:
                    return callback(*args, **kwargs)
                except Exception:
                    # bottle doesn't always translate unhandled exceptions, so
                    # we mark it here.
                    code = 500
                    raise
                finally:
                    s.set_tag(http.STATUS_CODE, code or response.status_code)
                    s.set_tag(http.URL, request.path)
                    s.set_tag(http.METHOD, request.method)

        return wrapped
