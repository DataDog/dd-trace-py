import logging

# project
from .conf import settings
from .compat import user_is_authenticated

from ...ext import http
from ...contrib import func_name
from ...propagation.http import HTTPPropagator

# 3p
from django.core.exceptions import MiddlewareNotUsed
from django.conf import settings as django_settings
import django

try:
    from django.utils.deprecation import MiddlewareMixin
    MiddlewareClass = MiddlewareMixin
except ImportError:
    MiddlewareClass = object

log = logging.getLogger(__name__)

EXCEPTION_MIDDLEWARE = 'ddtrace.contrib.django.TraceExceptionMiddleware'
TRACE_MIDDLEWARE = 'ddtrace.contrib.django.TraceMiddleware'
MIDDLEWARE = 'MIDDLEWARE'
MIDDLEWARE_CLASSES = 'MIDDLEWARE_CLASSES'

def get_middleware_insertion_point():
    """Returns the attribute name and collection object for the Django middleware.
    If middleware cannot be found, returns None for the middleware collection."""
    middleware = getattr(django_settings, MIDDLEWARE, None)
    # Prioritise MIDDLEWARE over ..._CLASSES, but only in 1.10 and later.
    if middleware and django.VERSION >= (1, 10):
        return MIDDLEWARE, middleware
    return MIDDLEWARE_CLASSES, getattr(django_settings, MIDDLEWARE_CLASSES, None)

def insert_trace_middleware():
    middleware_attribute, middleware = get_middleware_insertion_point()
    if middleware is not None and TRACE_MIDDLEWARE not in set(middleware):
        setattr(django_settings, middleware_attribute, type(middleware)((TRACE_MIDDLEWARE,)) + middleware)

def remove_trace_middleware():
    _, middleware = get_middleware_insertion_point()
    if middleware and TRACE_MIDDLEWARE in set(middleware):
        middleware.remove(TRACE_MIDDLEWARE)

def insert_exception_middleware():
    middleware_attribute, middleware = get_middleware_insertion_point()
    if middleware is not None and EXCEPTION_MIDDLEWARE not in set(middleware):
        setattr(django_settings, middleware_attribute, middleware + type(middleware)((EXCEPTION_MIDDLEWARE,)))

def remove_exception_middleware():
    _, middleware = get_middleware_insertion_point()
    if middleware and EXCEPTION_MIDDLEWARE in set(middleware):
        middleware.remove(EXCEPTION_MIDDLEWARE)

class InstrumentationMixin(MiddlewareClass):
    """
    Useful mixin base class for tracing middlewares
    """
    def __init__(self, get_response=None):
        # disable the middleware if the tracer is not enabled
        # or if the auto instrumentation is disabled
        self.get_response = get_response
        if not settings.AUTO_INSTRUMENT:
            raise MiddlewareNotUsed


class TraceExceptionMiddleware(InstrumentationMixin):
    """
    Middleware that traces exceptions raised
    """
    def process_exception(self, request, exception):
        try:
            span = _get_req_span(request)
            if span:
                span.set_tag(http.STATUS_CODE, '500')
                span.set_traceback() # will set the exception info
        except Exception:
            log.debug("error processing exception", exc_info=True)


class TraceMiddleware(InstrumentationMixin):
    """
    Middleware that traces Django requests
    """
    def process_request(self, request):
        tracer = settings.TRACER
        if settings.DISTRIBUTED_TRACING:
            propagator = HTTPPropagator()
            context = propagator.extract(request.META)
            # Only need to active the new context if something was propagated
            if context.trace_id:
                tracer.context_provider.activate(context)
        try:
            span = tracer.trace(
                'django.request',
                service=settings.DEFAULT_SERVICE,
                resource='unknown',  # will be filled by process view
                span_type=http.TYPE,
            )

            span.set_tag(http.METHOD, request.method)
            span.set_tag(http.URL, request.path)
            _set_req_span(request, span)
        except Exception:
            log.debug('error tracing request', exc_info=True)

    def process_view(self, request, view_func, *args, **kwargs):
        span = _get_req_span(request)
        if span:
            span.resource = func_name(view_func)

    def process_response(self, request, response):
        try:
            span = _get_req_span(request)
            if span:
                if response.status_code < 500 and span.error:
                    # remove any existing stack trace since it must have been
                    # handled appropriately
                    span._remove_exc_info()

                span.set_tag(http.STATUS_CODE, response.status_code)
                span = _set_auth_tags(span, request)
                span.finish()
        except Exception:
            log.debug("error tracing request", exc_info=True)
        finally:
            return response


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
        return span

    if hasattr(user, 'is_authenticated'):
        span.set_tag('django.user.is_authenticated', user_is_authenticated(user))

    uid = getattr(user, 'pk', None)
    if uid:
        span.set_tag('django.user.id', uid)

    uname = getattr(user, 'username', None)
    if uname:
        span.set_tag('django.user.name', uname)

    return span
