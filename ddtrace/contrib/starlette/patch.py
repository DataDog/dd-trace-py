import starlette
from starlette.middleware import Middleware

from ddtrace import config
from ddtrace import tracer
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

    span = tracer.current_root_span()
    # Update root span resource
    if span:
        # We want to make sure that the root span we grab is the one created from automatic instrumentation
        # And not a custom root span
        if span.name == "fastapi.request" or span.name == "starlette.request":
            path = "".join(scope["__dd_paths__"])
            if scope.get("method"):
                span.resource = "{} {}".format(scope["method"], path)
            else:
                span.resource = path
        else:
            log.debug(
                """The Starlette or Fastapi request span is not the current root span,
                therefore the resource name will not be modified."""
            )
    return wrapped(*args, **kwargs)
