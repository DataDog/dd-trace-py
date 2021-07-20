import sys

from ddtrace import _events
from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanTypes

from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...internal.compat import iteritems


class TraceMiddleware(object):
    def __init__(self, tracer, service="falcon", distributed_tracing=None):
        # store tracing references
        self.tracer = tracer
        self.service = service
        if distributed_tracing is not None:
            config.falcon["distributed_tracing"] = distributed_tracing

    def process_request(self, req, resp):
        # Falcon uppercases all header names.
        headers = dict((k.lower(), v) for k, v in iteritems(req.headers))
        trace_utils.activate_distributed_headers(self.tracer, int_config=config.falcon, request_headers=headers)

        span = self.tracer.trace(
            "falcon.request",
            service=self.service,
            span_type=SpanTypes.WEB,
        )
        span.set_tag(SPAN_MEASURED_KEY)

        # set analytics sample rate with global config enabled
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.falcon.get_analytics_sample_rate(use_global_config=True))

        _events.HTTPRequest(
            span=span,
            method=req.method,
            url=req.url,
            query=req.query_string or None,
            headers=req.headers,
            integration=config.falcon.integration_name,
        ).emit()

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

        # FIXME[matt] falcon does not map errors or unmatched routes
        # to proper status codes, so we we have to try to infer them
        # here. See https://github.com/falconry/falcon/issues/606
        if resource is None:
            status = "404"
            span.resource = "%s 404" % req.method
            _events.HTTPResponse(
                span=span, status_code=status, headers=resp._headers, integration=config.falcon.integration_name
            ).emit()
            span.finish()
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

        # Emit span hook for this response
        # DEV: Emit before closing so they can overwrite `span.resource` if they want
        config.falcon.hooks.emit("request", span, req, resp)

        _events.HTTPResponse(
            span=span, status_code=status, headers=resp._headers, integration=config.falcon.integration_name
        ).emit()

        # Close the span
        span.finish()


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
