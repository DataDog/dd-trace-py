import sys

import starlette
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount
from starlette.routing import Route
from starlette.routing import WebSocketRoute
from starlette.staticfiles import StaticFiles

from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap
from ddtrace.pin import Pin
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w


def homepage(request):
    return PlainTextResponse("Hello, world!")


routes = [
    Route("/", homepage),
]

app = Starlette(routes=routes)


def patch_byte_code():
    setattr(starlette, "_datadog_patch", True)
    Pin().onto(starlette)
    _update_patching(wrap, "starlette.routing", "Route", "handle", traced_handler_byte)


def unpatch_byte_code():
    Pin().onto(starlette)
    _update_patching(unwrap, "starlette.routing", "Route", "handle", traced_handler_byte)
    setattr(starlette, "_datadog_patch", False)


def _update_patching(operation, module_str, cls, func_name, wrapper):
    module = sys.modules[module_str]
    func = getattr(getattr(module, cls), func_name)
    operation(func, wrapper)


def traced_handler_byte(func, args, kwargs):
    pin = Pin.get_from(starlette)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    with pin.tracer.trace(
        name="starlette.request",
        resource=resource,
        service=trace_utils.int_service(pin, config.starlette),
        span_type="starlette",
    ) as span:
        return func(*args, **kwargs)


def test_byte_code_wrap(benchmark):
    def func():
        for i in range(1000):
            patch_byte_code()
            unpatch_byte_code()

    benchmark(func)


def patch():
    setattr(starlette, "_datadog_patch", True)
    _w("starlette.routing", "Route.handle", traced_handler)


def unpatch():
    setattr(starlette, "_datadog_patch", False)
    _u(starlette.routing.Route, "handle")


def traced_handler(wrapped, instance, args, kwargs):
    return wrapped(*args, **kwargs)


def test_normal_wrap(benchmark):
    def func():
        for i in range(1000):
            patch()
            unpatch()

    benchmark(func)
