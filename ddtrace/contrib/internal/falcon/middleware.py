import sys

from ddtrace import config
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.deprecations import deprecate


class TraceMiddleware(object):
    def __init__(self, tracer=None, service=None, distributed_tracing=None):
        if service is None:
            service = schematize_service_name("falcon")

        if tracer is not None:
            deprecate(
                "The tracer parameter is deprecated",
                message="The global tracer will be used instead.",
                category=DDTraceDeprecationWarning,
                removal_version="5.0.0",
            )

        self.service = service

        # A Falcon application may contain multiple TraceMiddleware instances,
        # so each instance must store its own execution context.
        self._request_context_key = "ddtrace.falcon.request_context.{}".format(id(self))

        if distributed_tracing is not None:
            config.falcon["distributed_tracing"] = distributed_tracing

    def process_request(self, req, resp):
        event = WebFrameworkRequestEvent(
            http_operation="falcon.request",
            component=config.falcon.integration_name,
            integration_config=config.falcon,
            service=self.service,
            request_method=req.method,
            request_url=req.url,
            # Preserve the header mapping passed to set_http_meta before this
            # migration. Distributed propagation normalizes header names
            # independently.
            request_headers=req.headers,
            query=req.query_string,
            request_route=None,
            allow_default_resource=True,
            activate_distributed_headers=True,
            headers_case_sensitive=False,
        )

        with core.context_with_event(
            event,
            dispatch_end_event=False,
        ) as ctx:
            req.env[self._request_context_key] = ctx

    def process_resource(self, req, resp, resource, params):
        ctx = req.env.get(self._request_context_key)
        if ctx is None:
            return

        span = span_from_context(ctx)
        if span is None:
            return

        # Set the resource on the live span before handler execution.
        span.resource = "%s %s" % (req.method, _name(resource))

        # Prevent the subscriber from replacing a resource customized by the
        # resource handler.
        event: WebFrameworkRequestEvent = ctx.event
        event.set_resource = False

    def process_response(self, req, resp, resource, req_succeeded=None):
        # req_succeeded is unavailable in Falcon 1.0.
        # TODO[manu]: drop the support at some point
        ctx = req.env.pop(self._request_context_key, None)
        if ctx is None:
            return

        try:
            span = span_from_context(ctx)
            if span is None:
                return

            event: WebFrameworkRequestEvent = ctx.event
            status = resp.status.partition(" ")[0]

            # Falcon does not always map errors or unmatched routes to the
            # proper status code, so retain the existing inference.
            if resource is None:
                status = "404"
                span.resource = "%s 404" % req.method
            else:
                err_type = sys.exc_info()[0]
                if err_type is not None:
                    if req_succeeded is None or req_succeeded is False:
                        status = _detect_and_set_status_error(err_type, span)

                event.request_route = (req.root_path or "") + (req.uri_template or "")
                event.response_headers = resp._headers

            event.response_status_code = int(status)
        finally:
            ctx.dispatch_ended_event()


def _is_404(err_type):
    return "HTTPNotFound" in err_type.__name__


def _detect_and_set_status_error(err_type, span):
    """Detect the HTTP status code and set the traceback on the span."""
    if not _is_404(err_type):
        span.set_traceback()
        return "500"

    return "404"


def _name(r):
    return "%s.%s" % (r.__module__, r.__class__.__name__)
