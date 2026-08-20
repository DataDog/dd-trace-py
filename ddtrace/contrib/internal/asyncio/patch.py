import asyncio
from contextvars import Context
from functools import partial
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace._trace.pin import Pin
from ddtrace.internal import core
from ddtrace.internal.constants import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal.constants import PYTHON_CONTEXT_WATCHER_REGISTERED
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap
from ddtrace.trace import tracer


log = get_logger(__name__)

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
    if core.root.get_item(PYTHON_CONTEXT_WATCHER_REGISTERED) is False:
        wrap(asyncio.Handle._run, _wrapped_run_handle)
        wrap(asyncio.to_thread, _wrapped_to_thread)
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
        _context_switch_instrumentation_patched = False


def _dispatch_context_switch(loop: Optional[Any], context: Optional[Context] = None, **details: Any) -> None:
    """Publish a context switch, reporting a listener failure rather than raising it at the call site.

    core.dispatch re-raises BaseException, and these dispatches sit outside the guards asyncio wraps
    application code in, so an escaping listener failure would tear the event loop down or take the
    place of the result of the call being instrumented. A to_thread worker runs off the loop and has
    none to report to, so it passes loop as None and the failure is logged instead.
    """
    try:
        if context is None:
            core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
        else:
            context.run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as exc:
        if loop is None:
            log.warning("Exception in ddtrace context switch listener", exc_info=True)
        else:
            loop.call_exception_handler(
                {"message": "Exception in ddtrace context switch listener", "exception": exc, **details}
            )


def _wrapped_run_handle(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if not core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
        return wrapped(*args, **kwargs)

    handle = args[0]
    original_callback = handle._callback

    def _callback_with_entry_dispatch(*cb_args: Any, **cb_kwargs: Any) -> Any:
        # Dispatching here, inside the callback, means it runs as part of the single
        # context.run() that the real _run performs below, instead of a redundant one of
        # our own just to enter handle._context before the real call does.
        _dispatch_context_switch(handle._loop, handle=handle)
        return original_callback(*cb_args, **cb_kwargs)

    handle._callback = _callback_with_entry_dispatch
    try:
        return wrapped(*args, **kwargs)
    finally:
        # A callback that cancels its own handle (or another timer's) sets _callback to
        # None to drop references; only restore ours if nothing else touched the slot.
        if handle._callback is _callback_with_entry_dispatch:
            handle._callback = original_callback
        _dispatch_context_switch(handle._loop, handle=handle)


def _run_with_context_switches(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Publish the copied worker context on entry and detach it before exit.

    The worker thread is pooled and reused, so its exit dispatch has to report an empty
    context rather than the (still-active) worker context -- otherwise the next job run on
    that thread would appear to inherit this one's active span.

    func is positional-only, like in asyncio.to_thread, so that a target taking its own func
    keyword argument still works.
    """
    _dispatch_context_switch(None)
    try:
        return func(*args, **kwargs)
    finally:
        _dispatch_context_switch(None, Context())


def _wrapped_to_thread(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if not args or not core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
        # A missing target is asyncio.to_thread's own TypeError to raise.
        return wrapped(*args, **kwargs)

    args = (partial(_run_with_context_switches, args[0]),) + args[1:]
    return wrapped(*args, **kwargs)


def _may_start_eagerly(loop: Any, kwargs: dict[str, Any]) -> bool:
    """Whether this create_task call could run the coroutine's first step inline.

    An eager first step bypasses Handle._run, so its context switch has to be published from
    inside the coroutine instead. Eager start needs a task factory or an explicit eager_start
    (3.14+); this is deliberately a superset, and _wrapped_create_task only keeps the switches
    of the calls that really did start eagerly.
    """
    return (
        _context_switch_instrumentation_patched
        and core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT)
        and (bool(kwargs.get("eager_start")) or loop.get_task_factory() is not None)
    )


def _wrapped_create_task(wrapped, args, kwargs):
    """This function ensures the current active trace context is propagated to scheduled tasks.
    By default the trace context is propagated when a task is executed and NOT when it is created.
    """
    pin = Pin.get_from(asyncio)
    pin_enabled = bool(pin and pin.enabled())

    loop = args[0]
    may_start_eagerly = _may_start_eagerly(loop, kwargs)
    if not pin_enabled and not may_start_eagerly:
        return wrapped(*args, **kwargs)

    # Get current trace context
    task_data: dict[str, Any] = {}
    dd_active = None
    if pin_enabled:
        core.dispatch("asyncio.create_task", (task_data,))
        dd_active = tracer.current_trace_context()
    if not dd_active and not may_start_eagerly:
        return wrapped(*args, **kwargs)

    # Get the coroutine
    coro = get_argument_value(args, kwargs, 1, "coro")

    # True only while wrapped() runs, which is exactly when an eager first step happens.
    eager_start_pending = may_start_eagerly
    eager_switch_published = False

    # Wrap the coroutine and ensure the current trace context is propagated
    async def traced_coro(*args_c, **kwargs_c):
        nonlocal eager_switch_published
        if eager_start_pending:
            eager_switch_published = True
            _dispatch_context_switch(loop)
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
        eager_start_pending = False
        if eager_switch_published:
            # Restore the caller's context for listeners after the eager step ran inline.
            _dispatch_context_switch(loop)
