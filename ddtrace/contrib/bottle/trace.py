from bottle import HTTPError
from bottle import WebResponse
from bottle import request
from bottle import response

import ddtrace
from ddtrace import _events
from ddtrace import config

from .. import trace_utils
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...utils.formats import asbool


class TracePlugin(object):
    name = "trace"
    api = 2

    def __init__(self, service="bottle", tracer=None, distributed_tracing=None):
        self.service = config.service or service
        self.tracer = tracer or ddtrace.tracer
        if distributed_tracing is not None:
            config.bottle.distributed_tracing = distributed_tracing

    @property
    def distributed_tracing(self):
        return config.bottle.distributed_tracing

    @distributed_tracing.setter
    def distributed_tracing(self, distributed_tracing):
        config.bottle["distributed_tracing"] = asbool(distributed_tracing)

    def apply(self, callback, route):
        def wrapped(*args, **kwargs):
            if not self.tracer or not self.tracer.enabled:
                return callback(*args, **kwargs)

            resource = "{} {}".format(request.method, route.rule)

            trace_utils.activate_distributed_headers(
                self.tracer, int_config=config.bottle, request_headers=request.headers
            )

            with self.tracer.trace(
                "bottle.request",
                service=self.service,
                resource=resource,
                span_type=SpanTypes.WEB,
            ) as s:
                s.set_tag(SPAN_MEASURED_KEY)
                # set analytics sample rate with global config enabled
                s.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.bottle.get_analytics_sample_rate(use_global_config=True))

                method = request.method
                url = request.urlparts._replace(query="").geturl()

                _events.WebRequest(
                    span=s,
                    method=method,
                    url=url,
                    headers=request.headers,
                    query=request.query_string,
                    integration=config.bottle.integration_name,
                ).emit()

                code = None
                result = None
                try:
                    result = callback(*args, **kwargs)
                    return result
                except (HTTPError, WebResponse) as e:
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
                    if isinstance(result, WebResponse):
                        response_code = result.status_code
                    elif code:
                        response_code = code
                    else:
                        # bottle local response has not yet been updated so this
                        # will be default
                        response_code = response.status_code

                    _events.WebResponse(
                        span=s,
                        status_code=response_code,
                        headers=response.headers,
                        integration=config.bottle.integration_name,
                    ).emit()

        return wrapped
