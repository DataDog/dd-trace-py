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
from ...utils.formats import asbool, get_env

# 3p
import cherrypy
from cherrypy.lib.httputil import valid_status

log = logging.getLogger(__name__)


# Configure default configuration
config._add(
    "cherrypy",
    dict(
        distributed_tracing=asbool(get_env("cherrypy", "distributed_tracing", default=True)),
    ),
)

SPAN_NAME = "cherrypy.request"


class TraceTool(cherrypy.Tool):
    def __init__(self, app, tracer, service, use_distributed_tracing):
        self.app = app
        self._tracer = tracer
        self._service = service
        self._use_distributed_tracing = use_distributed_tracing

        # CherryPy uses priority to determine which tools act first on each event. The lower the number, the higher
        # the priority. See: https://docs.cherrypy.org/en/latest/extend.html#tools-ordering
        cherrypy.Tool.__init__(self, "on_start_resource", self._on_start_resource, priority=95)

    def _setup(self):
        cherrypy.Tool._setup(self)
        cherrypy.request.hooks.attach("on_end_request", self._on_end_request, priority=5)
        cherrypy.request.hooks.attach("after_error_response", self._after_error_response, priority=5)

    def _on_start_resource(self):
        if config.cherrypy.get("distributed_tracing", False) or self._use_distributed_tracing:
            propagator = HTTPPropagator()
            context = propagator.extract(cherrypy.request.headers)
            # Only need to active the new context if something was propagated
            if context.trace_id:
                self._tracer.context_provider.activate(context)

        cherrypy.request._datadog_span = self._tracer.trace(
            SPAN_NAME,
            service=trace_utils.int_service(None, config.cherrypy, default=self._service),
            span_type=SpanTypes.WEB,
        )

    def _after_error_response(self):
        span = getattr(cherrypy.request, "_datadog_span", None)

        if not span:
            log.warning("cherrypy: tracing tool after_error_response hook called, but no active span found")
            return

        if not span.sampled:
            return

        span.error = 1
        span.set_tag(errors.ERROR_TYPE, cherrypy._cperror._exc_info()[0])
        span.set_tag(errors.ERROR_MSG, str(cherrypy._cperror._exc_info()[1]))
        span.set_tag(errors.STACK, cherrypy._cperror.format_exc())

        self._close_span(span)

    def _on_end_request(self):
        span = getattr(cherrypy.request, "_datadog_span", None)

        if not span:
            log.warning("cherrypy: tracing tool on_end_request hook called, but no active span found")
            return

        if not span.sampled:
            return

        self._close_span(span)

    def _close_span(self, span):
        # Let users specify their own resource in middleware if they so desire.
        # See case https://github.com/DataDog/dd-trace-py/issues/353
        if span.resource == SPAN_NAME:
            # In the future, mask virtual path components in a
            # URL e.g. /dispatch/abc123 becomes /dispatch/{{test_value}}/
            # Following investigation, this should be possible using
            # [find_handler](https://docs.cherrypy.org/en/latest/_modules/cherrypy/_cpdispatch.html#Dispatcher.find_handler)
            # but this may not be as easy as `cherrypy.request.dispatch.find_handler(cherrypy.request.path_info)` as
            # this function only ever seems to return an empty list for the virtual path components.

            # For now, default resource is method and path:
            #   GET /
            #   POST /save
            resource = "{} {}".format(cherrypy.request.method, cherrypy.request.path_info)
            span.resource = compat.to_unicode(resource)

        url = compat.to_unicode(cherrypy.request.base + cherrypy.request.path_info)
        status_code, _, _ = valid_status(cherrypy.response.status)

        trace_utils.set_http_meta(
            span,
            config.cherrypy,
            method=cherrypy.request.method,
            url=url,
            status_code=status_code,
            request_headers=cherrypy.request.headers,
            response_headers=cherrypy.response.headers,
        )

        span.finish()

        # Clear our span just in case.
        cherrypy.request._datadog_span = None


class TraceMiddleware(object):
    def __init__(self, app, tracer, service="cherrypy", use_signals=True, distributed_tracing=True):
        self.app = app

        # save our traces.
        self._tracer = tracer
        self._service = service
        self._use_distributed_tracing = distributed_tracing
        self.use_signals = use_signals

        self.app.tools.tracer = TraceTool(self.app, self._tracer, self._service, self._use_distributed_tracing)
