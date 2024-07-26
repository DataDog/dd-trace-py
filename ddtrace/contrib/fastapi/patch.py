import os

import fastapi
import fastapi.routing

from ddtrace import Pin
from ddtrace import config
from ddtrace.contrib.asgi.middleware import TraceMiddleware
from ddtrace.contrib.starlette.patch import _trace_background_tasks
from ddtrace.contrib.starlette.patch import traced_handler
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.vendor.debtcollector import deprecate
from ddtrace.vendor.wrapt import ObjectProxy
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w


log = get_logger(__name__)

config._add(
    "fastapi",
    dict(
        _default_service=schematize_service_name("fastapi"),
        request_span_name="fastapi.request",
        distributed_tracing=True,
        trace_query_string=None,  # Default to global config
        _trace_asgi_websocket=os.getenv("DD_ASGI_TRACE_WEBSOCKET", default=False),
    ),
)


def _get_version():
    # type: () -> str
    return getattr(fastapi, "__version__", "")


def get_version():
    deprecate(
        "get_version is deprecated",
        message="get_version is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _get_version()


def _wrap_middleware_stack(wrapped, instance, args, kwargs):
    return TraceMiddleware(app=wrapped(*args, **kwargs), integration_config=config.fastapi)


def wrap_middleware_stack(wrapped, instance, args, kwargs):
    deprecate(
        "wrap_middleware_stac is deprecated",
        message="wrap_middleware_stac is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _wrap_middleware_stack(wrapped, instance, args, kwargs)


async def _traced_serialize_response(wrapped, instance, args, kwargs):
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
    if not pin or not pin.enabled():
        return await wrapped(*args, **kwargs)

    with pin.tracer.trace("fastapi.serialize_response"):
        return await wrapped(*args, **kwargs)


async def traced_serialize_response(wrapped, instance, args, kwargs):
    deprecate(
        "traced_serialize_response is deprecated",
        message="traced_serialize_response is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _traced_serialize_response(wrapped, instance, args, kwargs)


def patch():
    if getattr(fastapi, "_datadog_patch", False):
        return

    fastapi._datadog_patch = True
    Pin().onto(fastapi)
    _w("fastapi.applications", "FastAPI.build_middleware_stack", _wrap_middleware_stack)
    _w("fastapi.routing", "serialize_response", _traced_serialize_response)

    if not isinstance(fastapi.BackgroundTasks.add_task, ObjectProxy):
        _w("fastapi", "BackgroundTasks.add_task", _trace_background_tasks(fastapi))

    # We need to check that Starlette instrumentation hasn't already patched these
    if not isinstance(fastapi.routing.APIRoute.handle, ObjectProxy):
        _w("fastapi.routing", "APIRoute.handle", traced_handler)

    if not isinstance(fastapi.routing.Mount.handle, ObjectProxy):
        _w("starlette.routing", "Mount.handle", traced_handler)


def unpatch():
    if not getattr(fastapi, "_datadog_patch", False):
        return

    fastapi._datadog_patch = False

    _u(fastapi.applications.FastAPI, "build_middleware_stack")
    _u(fastapi.routing, "serialize_response")

    # We need to check that Starlette instrumentation hasn't already unpatched these
    if isinstance(fastapi.routing.APIRoute.handle, ObjectProxy):
        _u(fastapi.routing.APIRoute, "handle")

    if isinstance(fastapi.routing.Mount.handle, ObjectProxy):
        _u(fastapi.routing.Mount, "handle")

    if isinstance(fastapi.BackgroundTasks.add_task, ObjectProxy):
        _u(fastapi.BackgroundTasks, "add_task")
