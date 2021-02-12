from ...vendor import debtcollector


try:
    from django.utils.deprecation import MiddlewareMixin

    MiddlewareClass = MiddlewareMixin
except ImportError:
    MiddlewareClass = object


@debtcollector.removals.removed_class(
    "TraceMiddlware", message="Usage of TraceMiddleware is not longer needed, please remove from your settings.py"
)
class TraceMiddleware(MiddlewareClass):
    pass
