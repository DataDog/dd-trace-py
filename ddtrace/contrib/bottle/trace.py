import os

# 3p
from bottle import response, request

# stdlib
import ddtrace

# project
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...ext import http, AppTypes
from ...propagation.http import HTTPPropagator
from ...settings import config
from ...utils.formats import get_env

SPAN_TYPE = 'web'

# Configure default configuration
config._add('bottle', dict(
    # Bottle service configuration
    # DEV: Environment variable 'DATADOG_SERVICE_NAME' used for backwards compatibility
    service_name=os.environ.get('DATADOG_SERVICE_NAME') or 'bottle',
    app='bottle',
    app_type=AppTypes.web,

    distributed_tracing_enabled=False,

    # Trace search configuration
    analytics=get_env('bottle', 'analytics', None),
    analytics_sample_rate=get_env('bottle', 'analytics_sample_rate', 1.0),
))


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
                # Set event sample rate for trace search (analytics)
                analytics_sample_rate = config.bottle.get_analytics_sample_rate()
                if analytics_sample_rate:
                    s.set_tag(ANALYTICS_SAMPLE_RATE_KEY, analytics_sample_rate)

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
