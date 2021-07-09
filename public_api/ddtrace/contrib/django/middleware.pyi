from django.utils.deprecation import MiddlewareMixin

MiddlewareClass = MiddlewareMixin
MiddlewareClass = object

class TraceMiddleware(MiddlewareClass): ...
