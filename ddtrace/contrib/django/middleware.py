import logging

# project
from ... import tracer
from ...ext import http
from ...contrib import func_name
from .templates import patch_template
from .db import patch_db

# 3p
from django.apps import apps
from django.conf import settings


log = logging.getLogger(__name__)


class TraceMiddleware(object):

    def __init__(self):
        # override if necessary (can't initialize though)
        self.tracer = tracer
        self.service = getattr(settings, 'DATADOG_SERVICE', 'django')

        self.tracer.set_service_info(
            service=self.service,
            app='django',
            app_type=http.APP_TYPE_WEB,
        )

        try:
            patch_template(self.tracer)
        except Exception:
            log.exception("error patching template class")

    def process_request(self, request):
        try:
            patch_db(self.tracer)  # ensure that connections are always patched.

            span = self.tracer.trace(
                "django.request",
                service=self.service,
                resource="unknown",  # will be filled by process view
                span_type=http.TYPE,
            )

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

                if apps.is_installed("django.contrib.auth"):
                    span = _set_auth_tags(span, request)

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



def _get_req_span(request):
    """ Return the datadog span from the given request. """
    return getattr(request, '_datadog_request_span', None)

def _set_req_span(request, span):
    """ Set the datadog span on the given request. """
    return setattr(request, '_datadog_request_span', span)


def _set_auth_tags(span, request):
    """ Patch any available auth tags from the request onto the span. """
    user = getattr(request, 'user', None)
    if not user:
        return

    if hasattr(user, 'is_authenticated'):
        span.set_tag('django.user.is_authenticated', user.is_authenticated())

    uid = getattr(user, 'pk', None)
    if uid:
        span.set_tag('django.user.id', uid)

    uname = getattr(user, 'username', None)
    if uname:
        span.set_tag('django.user.name', uname)

    return span
