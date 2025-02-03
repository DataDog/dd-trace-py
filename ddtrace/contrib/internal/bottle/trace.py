from bottle import HTTPError
from bottle import HTTPResponse
from bottle import request
from bottle import response

import ddtrace
from ddtrace import config
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils.formats import asbool


class TracePlugin(object):
    name = "trace"
    api = 2

    def __init__(self, service="bottle", tracer=None, distributed_tracing=None):
        self.service = config._get_service(default=service)
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

            with core.context_with_data(
                "bottle.request",
                span_name=schematize_url_operation("bottle.request", protocol="http", direction=SpanDirection.INBOUND),
                span_type=SpanTypes.WEB,
                service=self.service,
                resource=resource,
                tags={},
                tracer=self.tracer,
                distributed_headers=request.headers,
                distributed_headers_config=config.bottle,
                headers_case_sensitive=True,
                analytics_sample_rate=config.bottle.get_analytics_sample_rate(use_global_config=True),
            ) as ctx, ctx.span as req_span:
                ctx.set_item("req_span", req_span)
                core.dispatch("web.request.start", (ctx, config.bottle))

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
                    full_route = "/".join([request.script_name.rstrip("/"), route.rule.lstrip("/")])

                    core.dispatch(
                        "web.request.finish",
                        (
                            req_span,
                            config.bottle,
                            method,
                            url,
                            response_code,
                            request.query_string,
                            request.headers,
                            response.headers,
                            full_route,
                            False,
                        ),
                    )

        return wrapped
