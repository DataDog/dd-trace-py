import asyncio
from typing import Dict

from ddtrace.contrib.internal.trace_utils import PatchingWrappingContext
from ddtrace.trace import Pin


def get_version():
    # type: () -> str
    return ""


def _supported_versions() -> Dict[str, str]:
    return {"asyncio": "*"}


class CreateTaskWrappingContext(PatchingWrappingContext):
    def __enter__(self) -> "CreateTaskWrappingContext":
        super().__enter__()

        if not (pin := Pin.get_from(asyncio)) or not pin.enabled():
            return self

        # Only wrap the coroutine if we have an active trace context
        if not (dd_active := pin.tracer.current_trace_context()):
            return self

        # Get the original coroutine
        coro = self.get_local("coro")

        # Wrap the coroutine and ensure the current trace context is propagated
        async def traced_coro(*args_c, **kwargs_c):
            if dd_active != pin.tracer.current_trace_context():
                pin.tracer.context_provider.activate(dd_active)
            await coro

        # DEV: try to persist the original function name (useful for debugging)
        tc = traced_coro()
        if hasattr(coro, "__name__"):
            tc.__name__ = coro.__name__
        if hasattr(coro, "__qualname__"):
            tc.__qualname__ = coro.__qualname__

        self.set_local("coro", tc)

        return self


def patch():
    """Patches current loop `create_task()` method to enable spawned tasks to
    parent to the base task context.
    """
    if getattr(asyncio, "_datadog_patch", False):
        return
    asyncio._datadog_patch = True
    Pin().onto(asyncio)
    CreateTaskWrappingContext(asyncio.BaseEventLoop.create_task).wrap()


def unpatch():
    """Remove tracing from patched modules."""

    if getattr(asyncio, "_datadog_patch", False):
        return
    asyncio._datadog_patch = False
    CreateTaskWrappingContext.extract(asyncio.BaseEventLoop.create_task).unwrap()
