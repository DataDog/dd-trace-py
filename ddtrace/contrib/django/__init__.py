

import logging
from types import MethodType


# project
from ... import tracer
from ...ext import http, errors
from ...contrib import func_name

# 3p
from django.template import Template


log = logging.getLogger(__name__)


class TraceMiddleware(object):

    def __init__(self):
        # override if necessary (can't initialize though)
        self.tracer = tracer
        self.service = "django"

        try:
            _patch_template(self.tracer)
        except Exception:
            log.exception("error patching template class")

    def process_request(self, request):
        try:
            span = self.tracer.trace(
                "django.request",
                service=self.service,
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
            span.resource = func_name(view_func)

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


def _patch_template(tracer):
    """ will patch the django template render function to include information.
    """

    # FIXME[matt] we're patching the template class here. ideally we'd only
    # patch so we can use multiple tracers at once, but i suspect this is fine
    # in practice.
    attr = '_datadog_original_render'
    if getattr(Template, attr, None):
        log.debug("already patched")
        return

    setattr(Template, attr, Template.render)

    class TracedTemplate(object):

        def render(self, context):
            with tracer.trace('django.template', span_type=http.TEMPLATE) as span:
                try:
                    return Template._datadog_original_render(self, context)
                finally:
                    span.set_tag('django.template_name', context.template_name or 'unknown')

    Template.render = TracedTemplate.render.__func__


def _get_req_span(request):
    return getattr(request, '_datadog_request_span', None)

def _set_req_span(request, span):
    return setattr(request, '_datadog_request_span', span)

