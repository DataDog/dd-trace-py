# 3p
from bottle import response, request, HTTPError, HTTPResponse

# stdlib
import ddtrace

# project
from ...constants import ANALYTICS_SAMPLE_RATE_KEY, SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...propagation.http import HTTPPropagator
from ...settings import config
from .. import trace_utils


class TracePlugin(object):
    name = "trace"
    api = 2

    def __init__(self, service="bottle", tracer=None, distributed_tracing=True):
        self.service = ddtrace.config.service or service
        self.tracer = tracer or ddtrace.tracer
        self.distributed_tracing = distributed_tracing

    def apply(self, callback, route):
        def wrapped(*args, **kwargs):
            if not self.tracer or not self.tracer.enabled:
                return callback(*args, **kwargs)

            resource = "{} {}".format(request.method, route.rule)

            # Propagate headers such as x-datadog-trace-id.
            if self.distributed_tracing:
                propagator = HTTPPropagator()
                context = propagator.extract(request.headers)
                if context.trace_id:
                    self.tracer.context_provider.activate(context)

            with self.tracer.trace(
                "bottle.request",
                service=self.service,
                resource=resource,
                span_type=SpanTypes.WEB,
            ) as s:
                s.set_tag(SPAN_MEASURED_KEY)
                # set analytics sample rate with global config enabled
                s.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.bottle.get_analytics_sample_rate(use_global_config=True))

                code = None
                result = None
                try:
                    result = callback(*args, **kwargs)
                    return result
                except (HTTPError, HTTPResponse) as e:
                    # you can interrupt flows using abort(status_code, 'message')...
                    # we need to respect the defined status_code.
                    # we also need to handle when response is raised as is the
                    # case with a 4xx status
                    code = e.status_code
                    raise
                except Exception:
                    # bottle doesn't always translate unhandled exceptions, so
                    # we mark it here.
                    code = 500
                    raise
                finally:
                    if isinstance(result, HTTPResponse):
                        response_code = result.status_code
                    elif code:
                        response_code = code
                    else:
                        # bottle local response has not yet been updated so this
                        # will be default
                        response_code = response.status_code

                    method = request.method
                    url = request.urlparts._replace(query="").geturl()
                    trace_utils.set_http_meta(
                        s,
                        config.bottle,
                        method=method,
                        url=url,
                        status_code=response_code,
                        query=request.query_string,
                        request_headers=request.headers,
                        response_headers=response.headers,
                    )

        return wrapped
