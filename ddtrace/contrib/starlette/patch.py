import starlette
from starlette.middleware import Middleware

from ddtrace import config
from ddtrace import tracer
from ddtrace.contrib.asgi.middleware import TraceMiddleware
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.wrappers import unwrap as _u
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


def traced_init(wrapped, instance, args, kwargs):
    mw = kwargs.pop("middleware", [])
    mw.insert(0, Middleware(TraceMiddleware, integration_config=config.starlette))
    kwargs.update({"middleware": mw})

    wrapped(*args, **kwargs)


def patch():
    if getattr(starlette, "_datadog_patch", False):
        return

    setattr(starlette, "_datadog_patch", True)

    _w("starlette.applications", "Starlette.__init__", traced_init)

    handlers = [
        "Mount",
        "Route",
    ]

    # Wrap all of the handlers
    for handler in handlers:
        _w("starlette.routing", "{}.handle".format(handler), traced_handler)


def unpatch():
    if not getattr(starlette, "_datadog_patch", False):
        return

    setattr(starlette, "_datadog_patch", False)

    _u(starlette.applications.Starlette, "__init__")

    handlers = [
        starlette.routing.Mount,
        starlette.routing.Route,
    ]

    # Unwrap all of the handlers
    for handler in handlers:
        _u(handler, "handle")


def traced_handler(wrapped, instance, args, kwargs):
    def _wrap(scope, receive, send):
        # Since handle can be called multiple times for one request, we take the path of each instance
        # Then combine them at the end to get the correct resource name
        if "__dd_paths__" in scope:
            scope["__dd_paths__"].append(instance.path)

        else:
            scope["__dd_paths__"] = [instance.path]

        method = scope["method"] if "method" in scope else ""

        span = tracer.current_root_span()
        # Update root span resource
        if span:
            span.resource = "{} {}".format(method, "".join(scope["__dd_paths__"]))
        return wrapped(*args, **kwargs)

    return _wrap(*args, **kwargs)
