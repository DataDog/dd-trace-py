import fastapi
from fastapi.middleware import Middleware

from ddtrace import config
from ddtrace.contrib.asgi.middleware import TraceMiddleware
from ddtrace.contrib.starlette.patch import get_resource
from ddtrace.internal.logger import get_logger
from ddtrace.utils.wrappers import unwrap as _u
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


def patch():
    if getattr(fastapi, "_datadog_patch", False):
        return

    setattr(fastapi, "_datadog_patch", True)
    _w("fastapi.applications", "FastAPI.__init__", traced_init)


def unpatch():
    if not getattr(fastapi, "_datadog_patch", False):
        return

    setattr(fastapi, "_datadog_patch", False)

    _u(fastapi.applications.FastAPI, "__init__")
