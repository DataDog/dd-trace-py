import asyncio
from contextvars import Context
from functools import partial
import sys
from types import ModuleType
from typing import Any
from typing import Callable
from typing import NoReturn
from typing import cast

from ddtrace._trace.pin import Pin
from ddtrace.internal import core
from ddtrace.internal.constants import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal.context_watcher import _run_with_context_switches
from ddtrace.internal.context_watcher import is_context_watcher_registered
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap
from ddtrace.trace import tracer


_context_switch_instrumentation_patched = False


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
    global _context_switch_instrumentation_patched
    # The native watcher is preferred on CPython 3.14, but compatibility
    # instrumentation remains the fallback when it is unavailable or disabled.
    if core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT) and not is_context_watcher_registered():
        wrap(asyncio.Handle._run, _wrapped_run_handle)
        wrap(asyncio.to_thread, _wrapped_to_thread)
        ModuleWatchdog.register_module_hook("uvloop", _patch_uvloop)
        _context_switch_instrumentation_patched = True


def unpatch():
    """Remove tracing from patched modules."""

    if not getattr(asyncio, "_datadog_patch", False):
        return
    asyncio._datadog_patch = False
    unwrap(asyncio.BaseEventLoop.create_task, _wrapped_create_task)
    global _context_switch_instrumentation_patched
    if _context_switch_instrumentation_patched:
        unwrap(asyncio.Handle._run, _wrapped_run_handle)
        unwrap(asyncio.to_thread, _wrapped_to_thread)
        ModuleWatchdog.unregister_module_hook("uvloop", _patch_uvloop)
        module = sys.modules.get("uvloop")
        if module is not None:
            from ddtrace.contrib.internal.asyncio import _uvloop

            _uvloop.unpatch(module)
        _context_switch_instrumentation_patched = False


def _patch_uvloop(module: ModuleType) -> None:
    from ddtrace.contrib.internal.asyncio import _uvloop

    _uvloop.patch(module)


def _wrapped_run_handle(
    wrapped: Callable[[asyncio.Handle], None], args: tuple[asyncio.Handle], kwargs: dict[str, NoReturn]
) -> None:
    if not core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
        return wrapped(*args, **kwargs)

    ctx: Context = args[0]._context  # type: ignore[attr-defined]
    ctx.run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)
    try:
        return wrapped(*args, **kwargs)
    finally:
        core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)


def _wrapped_to_thread(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if not core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
        return wrapped(*args, **kwargs)

    func = cast(Callable[..., Any], get_argument_value(args, kwargs, 0, "func"))
    args, kwargs = set_argument_value(args, kwargs, 0, "func", partial(_run_with_context_switches, func))
    return wrapped(*args, **kwargs)


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
    publish_context_switch = (
        _context_switch_instrumentation_patched
        and core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT)
        and args[0].get_task_factory() is not None
    )
    if not dd_active and not publish_context_switch:
        return wrapped(*args, **kwargs)

    # Get the coroutine
    coro = get_argument_value(args, kwargs, 1, "coro")

    # Wrap the coroutine and ensure the current trace context is propagated
    async def traced_coro(*args_c, **kwargs_c):
        if publish_context_switch:
            core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
        if dd_active:
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

    try:
        return wrapped(*args, **kwargs)
    finally:
        if publish_context_switch:
            core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
