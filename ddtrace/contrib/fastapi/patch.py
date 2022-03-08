import fastapi
from fastapi.middleware import Middleware
import fastapi.routing

from ddtrace import Pin
from ddtrace import config
from ddtrace.contrib.asgi.middleware import TraceMiddleware
from ddtrace.contrib.starlette.patch import get_resource
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.wrappers import unwrap as _u
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


def span_modifier(span, scope):
    resource = get_resource(scope)
    if config.fastapi["aggregate_resources"] and resource:
        span.resource = "{} {}".format(scope["method"], resource)


def traced_init(wrapped, instance, args, kwargs):
    mw = kwargs.pop("middleware", [])
    mw.insert(0, Middleware(TraceMiddleware, integration_config=config.fastapi, span_modifier=span_modifier))
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


def unpatch():
    if not getattr(fastapi, "_datadog_patch", False):
        return

    setattr(fastapi, "_datadog_patch", False)

    _u(fastapi.applications.FastAPI, "__init__")
    _u(fastapi.routing, "serialize_response")
