import asyncio

from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap
from ddtrace.trace import Pin


def get_version():
    # type: () -> str
    return ""


def patch():
    """Patches current loop `create_task()` method to enable spawned tasks to
    parent to the base task context.
    """
    if getattr(asyncio, "_datadog_patch", False):
        return
    asyncio._datadog_patch = True
    Pin().onto(asyncio)
    wrap(asyncio.BaseEventLoop.create_task, _wrapped_create_task_py37)


def unpatch():
    """Remove tracing from patched modules."""

    if getattr(asyncio, "_datadog_patch", False):
        return
    asyncio._datadog_patch = False
    unwrap(asyncio.BaseEventLoop.create_task, _wrapped_create_task_py37)


def _wrapped_create_task_py37(wrapped, args, kwargs):
    """This function ensures the current active trace context is propagated to scheduled tasks.
    By default the trace context is propagated when a task is executed and NOT when it is created.
    """
    pin = Pin.get_from(asyncio)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    # override existing co-rountine to ensure the current trace context is propagated
    coro = get_argument_value(args, kwargs, 1, "coro")
    dd_active = pin.tracer.current_trace_context()

    async def traced_coro(*args_c, **kwargs_c):
        if dd_active and dd_active != pin.tracer.current_trace_context():
            pin.tracer.context_provider.activate(dd_active)
        return await coro

    # DEV: try to persist the original function name (useful for debugging)
    tc = traced_coro()
    if hasattr(coro, "__name__"):
        tc.__name__ = coro.__name__
    if hasattr(coro, "__qualname__"):
        tc.__qualname__ = coro.__qualname__
    args, kwargs = set_argument_value(args, kwargs, 1, "coro", tc)

    return wrapped(*args, **kwargs)
