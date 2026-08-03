import asyncio
from contextvars import Context
import sys
from types import ModuleType
from typing import Any
from typing import Callable
from typing import NoReturn

from ddtrace._trace.pin import Pin
from ddtrace.internal import core
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap
from ddtrace.trace import tracer


_CONTEXT_SWITCH_EVENT = "python.context.switch"


def get_version() -> str:
    return ""


def _supported_versions() -> dict[str, str]:
    return {"asyncio": "*"}


def patch():
    """Patches current loop `create_task()` method to enable spawned tasks to
    parent to the base task context.
    """
    if getattr(asyncio, "_datadog_patch", False):
        return
    asyncio._datadog_patch = True
    Pin().onto(asyncio)
    wrap(asyncio.BaseEventLoop.create_task, _wrapped_create_task)
    # CPython 3.14+ publishes context switches through the native watcher, so
    # wrapping event-loop scheduling APIs there would only add overhead.
    if sys.implementation.name != "cpython" or sys.version_info < (3, 14):
        wrap(asyncio.Handle._run, _wrapped_run_handle)
        ModuleWatchdog.register_module_hook("uvloop", _patch_uvloop)


def unpatch():
    """Remove tracing from patched modules."""

    if not getattr(asyncio, "_datadog_patch", False):
        return
    asyncio._datadog_patch = False
    unwrap(asyncio.BaseEventLoop.create_task, _wrapped_create_task)
    if sys.implementation.name != "cpython" or sys.version_info < (3, 14):
        unwrap(asyncio.Handle._run, _wrapped_run_handle)
        ModuleWatchdog.unregister_module_hook("uvloop", _patch_uvloop)
        module = sys.modules.get("uvloop")
        if module is not None:
            from ddtrace.contrib.internal.asyncio import _uvloop

            _uvloop.unpatch(module)


def _patch_uvloop(module: ModuleType) -> None:
    from ddtrace.contrib.internal.asyncio import _uvloop

    _uvloop.patch(module)


def _wrapped_run_handle(
    wrapped: Callable[[asyncio.Handle], None], args: tuple[asyncio.Handle], kwargs: dict[str, NoReturn]
) -> None:
    if not core.has_listeners(_CONTEXT_SWITCH_EVENT):
        return wrapped(*args, **kwargs)

    ctx: Context = args[0]._context  # type: ignore[attr-defined]
    ctx.run(core.dispatch, _CONTEXT_SWITCH_EVENT)
    try:
        return wrapped(*args, **kwargs)
    finally:
        core.dispatch(_CONTEXT_SWITCH_EVENT)


def _wrapped_create_task(wrapped, args, kwargs):
    """This function ensures the current active trace context is propagated to scheduled tasks.
    By default the trace context is propagated when a task is executed and NOT when it is created.
    """
    pin = Pin.get_from(asyncio)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    # Get current trace context
    task_data: dict[str, Any] = {}
    core.dispatch("asyncio.create_task", (task_data,))

    dd_active = tracer.current_trace_context()
    # Only wrap the coroutine if we have an active trace context
    if not dd_active:
        return wrapped(*args, **kwargs)

    # Get the coroutine
    coro = get_argument_value(args, kwargs, 1, "coro")

    # Wrap the coroutine and ensure the current trace context is propagated
    async def traced_coro(*args_c, **kwargs_c):
        if dd_active != tracer.current_trace_context():
            tracer.context_provider.activate(dd_active)
        core.dispatch("asyncio.execute_task", (task_data,))
        return await coro

    # DEV: try to persist the original function name (useful for debugging)
    tc = traced_coro()
    if hasattr(coro, "__name__"):
        tc.__name__ = coro.__name__
    if hasattr(coro, "__qualname__"):
        tc.__qualname__ = coro.__qualname__
    args, kwargs = set_argument_value(args, kwargs, 1, "coro", tc)

    return wrapped(*args, **kwargs)
