"""
Datadog trace code for cherrypy.
"""

import logging
from typing import cast

import cherrypy
from cherrypy.lib.httputil import valid_status

from ddtrace import config
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal.trace_utils import set_service_and_source
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.deprecations import deprecate
from ddtrace.internal.utils.formats import asbool


log = logging.getLogger(__name__)

# Configure default configuration
config._add(
    "cherrypy",
    dict(
        distributed_tracing=asbool(env.get("DD_CHERRYPY_DISTRIBUTED_TRACING", default=True)),
        _default_service="cherrypy",
    ),
)


def get_version() -> str:
    return getattr(cherrypy, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"cherrypy": ">=17.0.0"}


class TraceTool(cherrypy.Tool):
    def __init__(self, app, service, use_distributed_tracing=None):
        self.app = app
        config.cherrypy["service"] = schematize_service_name(service)
        if use_distributed_tracing is not None:
            self.use_distributed_tracing = use_distributed_tracing

        # CherryPy uses priority to determine which tools act first on each event. The lower the number, the higher
        # the priority. See: https://docs.cherrypy.org/en/latest/extend.html#tools-ordering
        cherrypy.Tool.__init__(self, "on_start_resource", self._on_start_resource, priority=95)

    @property
    def use_distributed_tracing(self):
        return config.cherrypy.distributed_tracing

    @use_distributed_tracing.setter
    def use_distributed_tracing(self, use_distributed_tracing):
        config.cherrypy["distributed_tracing"] = asbool(use_distributed_tracing)

    def _setup(self):
        cherrypy.Tool._setup(self)
        cherrypy.request.hooks.attach("on_end_request", self._on_end_request, priority=5)
        cherrypy.request.hooks.attach("after_error_response", self._after_error_response, priority=5)

    def _on_start_resource(self):
        service = trace_utils.int_service(
            None,
            config.cherrypy,
            default="cherrypy",
        )
        url = str(cherrypy.request.base + cherrypy.request.path_info)

        event = WebFrameworkRequestEvent(
            http_operation="cherrypy.request",
            component=config.cherrypy.integration_name,
            integration_config=config.cherrypy,
            request_method=cherrypy.request.method,
            request_url=url,
            request_headers=cherrypy.request.headers,
            # Preserve existing behavior: CherryPy did not trace its query
            # string.
            query="",
            request_route=None,
            activate_distributed_headers=True,
            headers_case_sensitive=True,
        )

        with core.context_with_event(
            event,
            dispatch_end_event=False,
        ) as ctx:
            request_span = span_from_context(ctx)

            if request_span is not None:
                # Apply the service after event creation to preserve CherryPy
                # as the service source.
                set_service_and_source(
                    request_span,
                    service,
                    config.cherrypy,
                )

            cherrypy.request._datadog_context = ctx
            cherrypy.request._datadog_span = request_span

    def _after_error_response(self):
        ctx = getattr(cherrypy.request, "_datadog_context", None)

        if ctx is None:
            log.warning("cherrypy: tracing tool after_error_response hook called, but no active context found")
            return

        span = span_from_context(ctx)
        if span is None:
            return

        exc_info = cherrypy._cperror._exc_info()
        span.error = 1
        span._set_attribute(ERROR_TYPE, str(exc_info[0]))
        span._set_attribute(ERROR_MSG, str(exc_info[1]))
        span._set_attribute(
            ERROR_STACK,
            cherrypy._cperror.format_exc(),
        )

        self._close_request(ctx)

    def _on_end_request(self):
        ctx = getattr(cherrypy.request, "_datadog_context", None)

        if ctx is None:
            log.warning("cherrypy: tracing tool on_end_request hook called, but no active context found")
            return

        self._close_request(ctx)

    def _close_request(self, ctx):
        try:
            span = span_from_context(ctx)
            if span is None:
                return

            event = cast(WebFrameworkRequestEvent, ctx.event)

            # Let users customize the resource during request handling.
            # Comparing against event.operation_name also works under schema
            # v1.
            if span.resource == event.operation_name:
                span.resource = "{} {}".format(
                    cherrypy.request.method,
                    cherrypy.request.path_info,
                )

            status_code, _, _ = valid_status(cherrypy.response.status)
            event.response_status_code = status_code
            event.response_headers = cherrypy.response.headers

            ctx.dispatch_ended_event()
        finally:
            cherrypy.request._datadog_context = None
            cherrypy.request._datadog_span = None


class TraceMiddleware:
    def __init__(self, app, tracer=None, service="cherrypy", distributed_tracing=None):
        self.app = app
        if tracer is not None:
            deprecate(
                "The tracer parameter is deprecated",
                message="The global tracer will be used instead.",
                category=DDTraceDeprecationWarning,
                removal_version="5.0.0",
            )

        self.app.tools.tracer = TraceTool(
            app,
            service,
            distributed_tracing,
        )
