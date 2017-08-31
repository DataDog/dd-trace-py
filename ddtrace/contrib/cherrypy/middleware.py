"""
Datadog trace code for cherrypy.
"""

# stdlib
import logging

# project
from ... import compat
from ...ext import http, errors, AppTypes

# 3p
import cherrypy

log = logging.getLogger(__name__)

class TraceTool(cherrypy.Tool):
    def __init__(self, app, tracer, service):
        self.app = app
        self._tracer = tracer
        self._service = service
        cherrypy.Tool.__init__(self, 'on_start_resource',
                           self._on_start_resource,
                           priority=95)

    def _setup(self):
        cherrypy.Tool._setup(self)
        cherrypy.request.hooks.attach('on_end_request',
                                  self._on_end_request,
                                  priority=5)
        cherrypy.request.hooks.attach('after_error_response',
                                  self._after_error_response,
                                  priority=5)

    def _on_start_resource(self):
        cherrypy.request._datadog_span = self._tracer.trace("cherrypy.request", service=self._service, span_type=http.TYPE, )

    def _after_error_response(self):
        error_type = cherrypy._cperror._exc_info()[0]
        error_msg = str(cherrypy._cperror._exc_info()[1])
        error_tb = cherrypy._cperror.format_exc()

        self.close_span(error_type, error_msg, error_tb)


    def _on_end_request(self):
        self.close_span()

    def close_span(self, error_type=None, error_msg=None, error_tb=None):
        method = cherrypy.request.method
        url = cherrypy.request.base + cherrypy.request.path_info
        path_info = cherrypy.request.path_info
        status_code = cherrypy.response.status

        span = getattr(cherrypy.request, '_datadog_span', None)
        if span:
            if span.sampled:
                error = 0
                if error_type:
                    error = 1
                    span.set_tag(errors.ERROR_TYPE, error_type)
                    span.set_tag(errors.ERROR_MSG, error_msg)
                    span.set_tag(errors.STACK, error_tb)

            # the endpoint that matched the request is None if an exception
            # happened so we fallback to a common resource
            span.resource = compat.to_unicode(path_info).lower()
            span.set_tag(http.URL, compat.to_unicode(url))
            span.set_tag(http.STATUS_CODE, status_code)
            span.set_tag(http.METHOD, method)
            span.error = error
            span.finish()
            # Clear our span just in case.
            cherrypy.request._datadog_span = None



class TraceMiddleware(object):

    def __init__(self, app, tracer, service="cherrypy", use_signals=True):
        self.app = app

        # save our traces.
        self._tracer = tracer
        self._service = service

        self._tracer.set_service_info(
            service=service,
            app="cherrypy",
            app_type=AppTypes.web,
        )

        self.app.tools.tracer = TraceTool(self.app, self._tracer, self._service)

