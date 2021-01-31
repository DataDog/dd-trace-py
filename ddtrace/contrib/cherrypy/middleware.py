"""
Datadog trace code for cherrypy.
"""

# stdlib
import logging

# project
from ddtrace import config
from .. import trace_utils
from ... import compat
from ...ext import errors, SpanTypes
from ...propagation.http import HTTPPropagator

# 3p
import cherrypy
from cherrypy.lib.httputil import valid_status

log = logging.getLogger(__name__)

SPAN_NAME = "cherrypy.request"


class TraceTool(cherrypy.Tool):
    def __init__(self, app, tracer, service, use_distributed_tracing):
        self.app = app
        self._tracer = tracer
        self._service = service
        self._use_distributed_tracing = use_distributed_tracing

        cherrypy.Tool.__init__(self, "on_start_resource", self._on_start_resource, priority=95)

    def _setup(self):
        cherrypy.Tool._setup(self)
        cherrypy.request.hooks.attach("on_end_request", self._on_end_request, priority=5)
        cherrypy.request.hooks.attach("after_error_response", self._after_error_response, priority=5)

    def _on_start_resource(self):
        if self._use_distributed_tracing:
            propagator = HTTPPropagator()
            context = propagator.extract(cherrypy.request.headers)
            # Only need to active the new context if something was propagated
            if context.trace_id:
                self._tracer.context_provider.activate(context)

        try:
            cherrypy.request._datadog_span = self._tracer.trace(
                SPAN_NAME,
                service=self._service,
                span_type=SpanTypes.WEB,
            )
        except Exception:
            log.debug("cherrypy: error tracing request", exc_info=True)

    def _after_error_response(self):
        error_type = cherrypy._cperror._exc_info()[0]
        error_msg = str(cherrypy._cperror._exc_info()[1])
        error_tb = cherrypy._cperror.format_exc()

        self.close_span(error_type, error_msg, error_tb)

    def _on_end_request(self):
        self.close_span()

    def close_span(self, error_type=None, error_msg=None, error_tb=None):
        span = getattr(cherrypy.request, "_datadog_span", None)

        if not span or not span.sampled:
            return

        if error_type:
            span.error = 1
            span.set_tag(errors.ERROR_TYPE, error_type)
            span.set_tag(errors.ERROR_MSG, error_msg)
            span.set_tag(errors.STACK, error_tb)
        else:
            span.error = 0

        # Let users specify their own resource in middleware if they so desire.
        # See case https://github.com/DataDog/dd-trace-py/issues/353
        if span.resource == SPAN_NAME:
            path_info = cherrypy.request.path_info
            span.resource = compat.to_unicode(path_info).lower()

        method = cherrypy.request.method
        url = cherrypy.request.base + cherrypy.request.path_info
        status_code, _, _ = valid_status(cherrypy.response.status)

        trace_utils.set_http_meta(
            span,
            config.cherrypy,
            method=method,
            url=compat.to_unicode(url),
            status_code=status_code,
            request_headers=cherrypy.request.headers,
            response_headers=cherrypy.response.headers,
        )
        span.finish()

        # Clear our span just in case.
        cherrypy.request._datadog_span = None


class TraceMiddleware(object):
    def __init__(self, app, tracer, service="cherrypy", use_signals=True, distributed_tracing=False):
        self.app = app

        # save our traces.
        self._tracer = tracer
        self._service = service
        self._use_distributed_tracing = distributed_tracing
        self.use_signals = use_signals

        self.app.tools.tracer = TraceTool(self.app, self._tracer, self._service, self._use_distributed_tracing)
