import sys

from ddtrace import config
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.schema import SpanDirection
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema import schematize_url_operation


class TraceMiddleware(object):
    def __init__(self, tracer, service=None, distributed_tracing=None):
        if service is None:
            service = schematize_service_name("falcon")
        # store tracing references
        self.tracer = tracer
        self.service = service
        if distributed_tracing is not None:
            config.falcon["distributed_tracing"] = distributed_tracing

    def process_request(self, req, resp):
        # Falcon uppercases all header names.
        headers = dict((k.lower(), v) for k, v in req.headers.items())

        with core.context_with_data(
            "falcon.request",
            span_name=schematize_url_operation("falcon.request", protocol="http", direction=SpanDirection.INBOUND),
            span_type=SpanTypes.WEB,
            service=self.service,
            tags={},
            tracer=self.tracer,
            distributed_headers=headers,
            integration_config=config.falcon,
            activate_distributed_headers=True,
            headers_case_sensitive=True,
        ) as ctx:
            req_span = ctx.span
            ctx.set_item("req_span", req_span)
            core.dispatch("web.request.start", (ctx, config.falcon))

            core.dispatch(
                "web.request.finish",
                (req_span, config.falcon, req.method, req.url, None, req.query_string, req.headers, None, None, False),
            )

    def process_resource(self, req, resp, resource, params):
        span = self.tracer.current_span()
        if not span:
            return  # unexpected
        span.resource = "%s %s" % (req.method, _name(resource))

    def process_response(self, req, resp, resource, req_succeeded=None):
        # req_succeded is not a kwarg in the API, but we need that to support
        # Falcon 1.0 that doesn't provide this argument
        span = self.tracer.current_span()
        if not span:
            return  # unexpected

        status = resp.status.partition(" ")[0]

        # falcon does not map errors or unmatched routes
        # to proper status codes, so we have to try to infer them
        # here.
        if resource is None:
            status = "404"
            span.resource = "%s 404" % req.method
            core.dispatch("web.request.finish", (span, config.falcon, None, None, status, None, None, None, None, True))
            return

        err_type = sys.exc_info()[0]
        if err_type is not None:
            if req_succeeded is None:
                # backward-compatibility with Falcon 1.0; any version
                # greater than 1.0 has req_succeded in [True, False]
                # TODO[manu]: drop the support at some point
                status = _detect_and_set_status_error(err_type, span)
            elif req_succeeded is False:
                # Falcon 1.1+ provides that argument that is set to False
                # if get an Exception (404 is still an exception)
                status = _detect_and_set_status_error(err_type, span)

        route = req.root_path or "" + req.uri_template

        # Emit span hook for this response
        # DEV: Emit before closing so they can overwrite `span.resource` if they want
        config.falcon.hooks.emit("request", span, req, resp)

        core.dispatch(
            "web.request.finish", (span, config.falcon, None, None, status, None, None, resp._headers, route, True)
        )


def _is_404(err_type):
    return "HTTPNotFound" in err_type.__name__


def _detect_and_set_status_error(err_type, span):
    """Detect the HTTP status code from the current stacktrace and
    set the traceback to the given Span
    """
    if not _is_404(err_type):
        span.set_traceback()
        return "500"
    elif _is_404(err_type):
        return "404"


def _name(r):
    return "%s.%s" % (r.__module__, r.__class__.__name__)
