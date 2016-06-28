

import logging

# project
from ... import tracer
from ...ext import http, errors


log = logging.getLogger(__name__)


class TraceMiddleware(object):

    def __init__(self):
        # override if necessary (can't initialize though)
        self.tracer = tracer

    def process_request(self, request):
        try:
            service = "django" # FIXME: app name

            span = self.tracer.trace(
                "django.request",
                service=service,
                resource="request", # will be filled by process view
                span_type=http.TYPE)

            span.set_tag(http.METHOD, request.method)
            span.set_tag(http.URL, request.path)
            _set_req_span(request, span)
        except Exception:
            log.exception("error tracing request")

    def process_view(self, request, view_func, *args, **kwargs):
        span = _get_req_span(request)
        if span:
            span.resource = _view_func_name(view_func)

    def process_response(self, request, response):
        try:
            span = _get_req_span(request)
            if span:
                span.set_tag(http.STATUS_CODE, response.status_code)
                span.finish()
        except Exception:
            log.exception("error tracing request")
        finally:
            return response

    def process_exception(self, request, exception):
        try:
            span = _get_req_span(request)
            if span:
                span.set_tag(http.STATUS_CODE, '500')
                span.set_traceback() # will set the exception info
        except Exception:
            log.exception("error processing exception")

def _view_func_name(view_func):
    return "%s.%s" % (view_func.__module__, view_func.__name__)

def _get_req_span(request):
    return getattr(request, '_datadog_request_span', None)

def _set_req_span(request, span):
    return setattr(request, '_datadog_request_span', span)

