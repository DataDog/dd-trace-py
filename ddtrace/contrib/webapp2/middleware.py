import re

from ddtrace.propagation.http import HTTPPropagator
from ddtrace.ext import errors, http

import webapp2


class Webapp2TraceMiddleware(object):
    """Webapp2 datadog tracing WSGI middleware"""
    def __init__(self, app, tracer, service='webapp2', distributed_tracing=True):
        self.app = app
        self._service = service
        self._distributed_tracing = distributed_tracing
        self._tracer = tracer

    def distributed_tracing(self, request):
        """Setup distributed tracing from a request if enabled"""
        if self._distributed_tracing:
            # retrieve distributed tracing headers
            propagator = HTTPPropagator()
            context = propagator.extract(request.headers)
            # only need to active the new context if something was propagated
            if context.trace_id:
                self._tracer.context_provider.activate(context)

    def __call__(self, environ, start_response):
        request = webapp2.Request(environ)
        self.distributed_tracing(request)

        with self._tracer.trace('webapp2.request', service=self._service) as span:
            span.span_type = http.TYPE
            span.set_tag(http.URL, request.url)
            span.set_tag(http.METHOD, request.method)

            if not span.sampled:
                return self.app(environ, start_response)

            span.resource = self._get_resource_from_request(request)

            def _start_response(status, *args, **kwargs):
                """Patched response callback to get the status_code."""
                http_code = int(status.split()[0])
                span.set_tag(http.STATUS_CODE, http_code)
                if http_code >= 400:
                    span.error = 1
                return start_response(status, *args, **kwargs)

            response = self.app(environ, _start_response)

            status_code = span.get_tag(http.STATUS_CODE)
            if status_code >= 400:
                span.set_tag(errors.ERROR_MSG, response[0])

            return response

    def _get_resource_from_request(self, request):
        """Return the name of the resource which processes a request."""
        try:
            request_handler = self.app.router.match(request)[0].handler
        except webapp2.exc.HTTPNotFound:
            return request.url

        try:
            # XXX wish there was an easier way to get the class/method
            # name of the handler without using a regexp on its string
            # representation
            return re.match(
                "<class \'(.*)\'>", str(request_handler)).group(1)
        except AttributeError:
            return None
