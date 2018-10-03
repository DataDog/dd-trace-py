from .patch import patch_companion_services
from ..conf import settings
from ..middleware import TraceMiddleware as DjangoTraceMiddleware, \
                         TraceExceptionMiddleware as DjangoTraceExceptionMiddleware

EXCEPTION_MIDDLEWARE = 'ddtrace.contrib.django.legacy.TraceExceptionMiddleware'
TRACE_MIDDLEWARE = 'ddtrace.contrib.django.legacy.TraceMiddleware'


class BaseTraceMiddleware(object):
    """
    A base middleware that works as an entry point to configure tracing in legacy django apps, not having the
    concept of application configuration object.
    """
    def __init__(self, proxied_middleware):
        self.middleware = proxied_middleware
        self.enabled = settings.AUTO_INSTRUMENT
        # We use the middleware initialization as an hook to replace the 1.8+ app config
        patch_companion_services()


class TraceMiddleware(BaseTraceMiddleware):
    """
    Middleware that traces requests to the django app
    """
    def __init__(self):
        super(TraceMiddleware, self).__init__(DjangoTraceMiddleware())

    def process_request(self, request):
        """
        A request processor which is bypassed if auto instrumenting is disabled
        """
        return self.middleware.process_request(request) if self.enabled else None

    def process_view(self, request, view_func, *args, **kwargs):
        """
        A view processor which is bypassed if auto instrumenting is disabled
        """
        return self.middleware.process_view(request, view_func, *args, **kwargs) \
            if self.enabled \
            else None

    def process_response(self, request, response):
        """
        A response processor which is bypassed if auto instrumenting is disabled
        """
        return self.middleware.process_response(request, response) if self.enabled else None


class TraceExceptionMiddleware(BaseTraceMiddleware):
    """
    Middleware that traces exceptions raised
    """
    def __init__(self):
        super(TraceExceptionMiddleware, self).__init__(DjangoTraceExceptionMiddleware())

    def process_exception(self, request, exception):
        """
        An exception processor which is bypassed if auto instrumenting is disabled
        """
        return self.middleware.process_exception(request, exception) if self.enabled else None
