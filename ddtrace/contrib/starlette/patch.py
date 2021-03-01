import starlette
from starlette.middleware import Middleware
from starlette.routing import Match

from ddtrace import config
from ddtrace.contrib.asgi.middleware import TraceMiddleware
from ddtrace.internal.logger import get_logger
from ddtrace.utils.wrappers import unwrap as _u
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w


log = get_logger(__name__)

config._add(
    "starlette",
    dict(
        _default_service="starlette",
        request_span_name="starlette.request",
        distributed_tracing=True,
        aggregate_resources=True,
    ),
)


def get_resource(scope):
    path = None
    routes = scope["app"].routes
    for route in routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            path = route.path
            break
        elif match == Match.PARTIAL and path is None:
            path = route.path
    return path


def span_modifier(span, scope):
    resource = get_resource(scope)
    if config.starlette["aggregate_resources"] and resource:
        span.resource = "{} {}".format(scope["method"], resource)


def traced_init(wrapped, instance, args, kwargs):
    mw = kwargs.pop("middleware", [])
    mw.insert(0, Middleware(TraceMiddleware, integration_config=config.starlette, span_modifier=span_modifier))
    kwargs.update({"middleware": mw})

    wrapped(*args, **kwargs)


def patch():
    if getattr(starlette, "_datadog_patch", False):
        return

    setattr(starlette, "_datadog_patch", True)

    _w("starlette.applications", "Starlette.__init__", traced_init)


def unpatch():
    if not getattr(starlette, "_datadog_patch", False):
        return

    setattr(starlette, "_datadog_patch", False)

    _u(starlette.applications.Starlette, "__init__")
