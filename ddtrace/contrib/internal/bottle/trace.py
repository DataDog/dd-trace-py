from bottle import HTTPError
from bottle import HTTPResponse
from bottle import request
from bottle import response

from ddtrace import config
from ddtrace.contrib._events.web_framework import WebRequestEvent
from ddtrace.internal import core
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.formats import asbool
from ddtrace.trace import tracer
from ddtrace.vendor.debtcollector import deprecate


class TracePlugin(object):
    name = "trace"
    api = 2

    def __init__(self, service="bottle", tracer=None, distributed_tracing=None):
        if tracer is not None:
            deprecate(
                "The tracer parameter is deprecated",
                message="The global tracer will be used instead.",
                category=DDTraceDeprecationWarning,
                removal_version="5.0.0",
            )
        self.service = config._get_service(default=service)
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
            if not tracer or not tracer.enabled:
                return callback(*args, **kwargs)

            with core.context_with_event(
                WebRequestEvent(
                    url_operation="bottle.request",
                    component=config.bottle.integration_name,
                    service=self.service,
                    resource="{} {}".format(request.method, route.rule),
                    activate_distributed_headers=True,
                    integration_config=config.bottle,
                    request_headers=request.headers,
                    request_method=request.method,
                    request_query=request.query_string,
                    request_url=request.urlparts._replace(query="").geturl(),
                    request_route="/".join([request.script_name.rstrip("/"), route.rule.lstrip("/")]),
                )
            ) as ctx:
                ctx.set_item("headers_case_sensitive", True)

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

                    event: WebRequestEvent = ctx.event
                    event.response_headers = response.headers
                    event.response_status_code = response_code

        return wrapped
