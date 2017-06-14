from django.http import HttpResponse

try:
    from django.utils.deprecation import MiddlewareMixin
    MiddlewareClass = MiddlewareMixin
except ImportError:
    MiddlewareClass = object


class CatchExceptionMiddleware(MiddlewareClass):
    def process_exception(self, request, exception):
        return HttpResponse(status=500)
