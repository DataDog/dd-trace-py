import starlette
from starlette.middleware import Middleware

from ddtrace import config
from ddtrace.contrib.asgi.middleware import TraceMiddleware
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.vendor.wrapt import ObjectProxy
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

    # We need to check that Fastapi instrumentation hasn't already patched these
    if not isinstance(starlette.routing.Route.handle, ObjectProxy):
        _w("starlette.routing", "Route.handle", traced_handler)
    if not isinstance(starlette.routing.Mount.handle, ObjectProxy):
        _w("starlette.routing", "Mount.handle", traced_handler)


def unpatch():
    if not getattr(starlette, "_datadog_patch", False):
        return

    setattr(starlette, "_datadog_patch", False)

    _u(starlette.applications.Starlette, "__init__")

    # We need to check that Fastapi instrumentation hasn't already unpatched these
    if isinstance(starlette.routing.Route.handle, ObjectProxy):
        _u(starlette.routing.Route, "handle")

    if isinstance(starlette.routing.Mount.handle, ObjectProxy):
        _u(starlette.routing.Mount, "handle")


def traced_handler(wrapped, instance, args, kwargs):
    # Since handle can be called multiple times for one request, we take the path of each instance
    # Then combine them at the end to get the correct resource name
    scope = get_argument_value(args, kwargs, 0, "scope")

    if "__dd_paths__" in scope:
        scope["__dd_paths__"].append(instance.path)

    else:
        scope["__dd_paths__"] = [instance.path]

    if "datadog" not in scope:
        return wrapped(*args, **kwargs)

    # The scope["datadog"] object gets replaced with each new call to the handler
    # Therefore we need to check if the current request span is the first one, and if it is, place it in __dd__
    # So that it's not replaced and can be given the correct resource name
    if "__dd__" not in scope:
        scope["__dd__"] = {"__dd_first_request_span__": scope["datadog"].get("request_span")}

    request_span = scope["__dd__"].get("__dd_first_request_span__")

    if request_span:
        path = "".join(scope["__dd_paths__"])
        if scope.get("method"):
            request_span.resource = "{} {}".format(scope["method"], path)
        else:
            request_span.resource = path

    return wrapped(*args, **kwargs)
