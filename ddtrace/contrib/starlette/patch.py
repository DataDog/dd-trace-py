import starlette
from starlette.middleware import Middleware
from starlette.routing import Match

from ddtrace import config
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w
from ddtrace.contrib.asgi.middleware import TraceMiddleware
from ddtrace.utils.wrappers import unwrap as _u
from ddtrace.internal.logger import get_logger


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


def traced_init(wrapped, instance, args, kwargs):
    global routes
    routes = kwargs["routes"]
    mw = kwargs.pop("middleware", [])
    mw.insert(0, Middleware(TraceMiddleware, integration_config=config.starlette))
    kwargs.update({"middleware": mw})

    wrapped(*args, **kwargs)


def get_resource(scope):
    path = None
    global routes
    for route in routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            path = route.path
            break
        elif match == Match.PARTIAL and path is None:
            path = route.path
    return path


def call_decorator(func):
    def call_wrapper(*args, **kwargs):
        resource = get_resource(args[1])
        if config.starlette["aggregate_resources"]:
            args[1]["resource"] = resource
        r = func(*args, **kwargs)
        return r

    return call_wrapper


def patch():
    if getattr(starlette, "_datadog_patch", False):
        return

    setattr(starlette, "_datadog_patch", True)

    _w("starlette.applications", "Starlette.__init__", traced_init)
    TraceMiddleware.__call__ = call_decorator(TraceMiddleware.__call__)


def unpatch():
    if getattr(starlette, "_datadog_patch", False):
        return

    setattr(starlette, "_datadog_patch", False)

    _u("starlette.applications", "Starlette.__init__")
