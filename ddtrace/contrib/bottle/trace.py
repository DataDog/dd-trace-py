# 3p
from bottle import response, request, HTTPError

# stdlib
import ddtrace

# project
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...ext import http
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
                # set analytics sample rate with global config enabled
                s.set_tag(
                    ANALYTICS_SAMPLE_RATE_KEY,
                    config.bottle.get_analytics_sample_rate(use_global_config=True)
                )

                code = 0
                try:
                    return callback(*args, **kwargs)
                except HTTPError as e:
                    # you can interrupt flows using abort(status_code, 'message')...
                    # we need to respect the defined status_code.
                    code = e.status_code
                    raise
                except Exception:
                    # bottle doesn't always translate unhandled exceptions, so
                    # we mark it here.
                    code = 500
                    raise
                finally:
                    response_code = code or response.status_code
                    if 500 <= response_code < 600:
                        s.error = 1

                    s.set_tag(http.STATUS_CODE, response_code)
                    s.set_tag(http.URL, request.urlparts._replace(query='').geturl())
                    s.set_tag(http.METHOD, request.method)
                    if config.bottle.trace_query_string:
                        s.set_tag(http.QUERY_STRING, request.query_string)

        return wrapped
