import starlette
from ddtrace.contrib.starlette import aggregate_resources
from starlette.middleware import Middleware
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
        aggregate_resources=False,
    ),
)


def patch(routes=[]):
    if getattr(starlette, "_datadog_patch", False):
        return

    aggregate_resources.set_routes(routes)

    setattr(starlette, "_datadog_patch", True)
    _w("starlette.applications", "Starlette.__init__", traced_init)


def unpatch():
    if getattr(starlette, "_datadog_patch", False):
        return

    setattr(starlette, "_datadog_patch", False)

    _u("starlette.applications", "Starlette.__init__")


def traced_init(wrapped, instance, args, kwargs):
    mw = kwargs.pop("middleware", [])
    mw.insert(0, Middleware(TraceMiddleware, integration_config=config.starlette))
    kwargs.update({"middleware": mw})

    wrapped(*args, **kwargs)
