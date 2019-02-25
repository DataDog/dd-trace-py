# 3p
from bottle import response, request

# stdlib
import ddtrace
from ddtrace.ext import http

# project
from ...constants import EVENT_SAMPLE_RATE_KEY
from ...propagation.http import HTTPPropagator
from ...settings import config

SPAN_TYPE = 'web'


class TracePlugin(object):
    name = 'trace'
    api = 2

    def __init__(self, service='bottle', tracer=None, distributed_tracing=True):
        self.service = service
        self.tracer = tracer or ddtrace.tracer
        self.distributed_tracing = distributed_tracing

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
                # Configure trace search sample rate
                if config.bottle.event_sample_rate is not None:
                    s.set_tag(EVENT_SAMPLE_RATE_KEY, config.bottle.event_sample_rate)

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
