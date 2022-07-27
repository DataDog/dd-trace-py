import fastapi
from fastapi.middleware import Middleware
import fastapi.routing

from ddtrace import Pin
from ddtrace import config
from ddtrace.contrib.asgi.middleware import TraceMiddleware
from ddtrace.contrib.starlette.patch import get_resource
from ddtrace.contrib.starlette.patch import traced_handler
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.vendor.debtcollector import removals
from ddtrace.vendor.wrapt import ObjectProxy
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w


log = get_logger(__name__)

config._add(
    "fastapi",
    dict(
        _default_service="fastapi",
        request_span_name="fastapi.request",
        distributed_tracing=True,
        aggregate_resources=True,
    ),
)


@removals.remove(removal_version="2.0.0", category=DDTraceDeprecationWarning)
def span_modifier(span, scope):
    resource = get_resource(scope)
    if config.fastapi["aggregate_resources"] and resource:
        span.resource = "{} {}".format(scope["method"], resource)


def traced_init(wrapped, instance, args, kwargs):
    mw = kwargs.pop("middleware", [])
    mw.insert(0, Middleware(TraceMiddleware, integration_config=config.fastapi))
    kwargs.update({"middleware": mw})
    wrapped(*args, **kwargs)


async def traced_serialize_response(wrapped, instance, args, kwargs):
    """Wrapper for fastapi.routing.serialize_response function.

    This function is called on all non-Response objects to
    convert them to a serializable form.

    This is the wrapper which calls ``jsonable_encoder``.

    This function does not do the actual encoding from
    obj -> json string  (e.g. json.dumps()). That is handled
    by the Response.render function.

    DEV: We do not wrap ``jsonable_encoder`` because it calls
    itself recursively, so there is a chance the overhead
    added by creating spans will be higher than desired for
    the result.
    """
    pin = Pin.get_from(fastapi)
    if not pin or not pin.enabled:
        return await wrapped(*args, **kwargs)

    with pin.tracer.trace("fastapi.serialize_response"):
        return await wrapped(*args, **kwargs)


def patch():
    if getattr(fastapi, "_datadog_patch", False):
        return

    setattr(fastapi, "_datadog_patch", True)
    Pin().onto(fastapi)
    _w("fastapi.applications", "FastAPI.__init__", traced_init)
    _w("fastapi.routing", "serialize_response", traced_serialize_response)

    # We need to check that Starlette instrumentation hasn't already patched these
    if not isinstance(fastapi.routing.APIRoute.handle, ObjectProxy):
        _w("fastapi.routing", "APIRoute.handle", traced_handler)

    if not isinstance(fastapi.routing.Mount.handle, ObjectProxy):
        _w("starlette.routing", "Mount.handle", traced_handler)


def unpatch():
    if not getattr(fastapi, "_datadog_patch", False):
        return

    setattr(fastapi, "_datadog_patch", False)

    _u(fastapi.applications.FastAPI, "__init__")
    _u(fastapi.routing, "serialize_response")

    # We need to check that Starlette instrumentation hasn't already unpatched these
    if isinstance(fastapi.routing.APIRoute.handle, ObjectProxy):
        _u(fastapi.routing.APIRoute, "handle")

    if isinstance(fastapi.routing.Mount.handle, ObjectProxy):
        _u(fastapi.routing.Mount, "handle")
