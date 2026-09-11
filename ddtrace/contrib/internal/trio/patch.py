"""Publish context switches around Trio task and thread boundaries.

The instrumentation has three paths. A run-scoped Instrument publishes task ContextVars at every scheduler
transition. The to_thread wrapper publishes the copied ContextVars while a worker executes. The
from_thread wrappers publish the copied ContextVars while a worker callback executes back in Trio.
"""

from contextvars import Context
from functools import partial
from importlib.metadata import version
import inspect
from typing import Any
from typing import Callable
from typing import cast

import trio
from trio._core._run import Task
import trio.abc
import trio.from_thread
import trio.lowlevel
import trio.to_thread

from ddtrace.internal import core
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal._context_watcher import context_switches_require_fallback
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


def get_version() -> str:
    return version("trio")


def _supported_versions() -> dict[str, str]:
    return {"trio": ">=0.21.0"}


class _ContextSwitchInstrument(trio.abc.Instrument):  # type: ignore[misc]
    """Publish the ContextVar state around each Trio task step."""

    def before_task_step(self, task: Task) -> None:
        # Instruments run before Trio enters task.context, so publish from that task's Context.
        if core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
            task.context.copy().run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)

    def after_task_step(self, task: Task) -> None:
        # Trio has restored the run-loop context after the task yielded.
        if core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
            core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)


def patch() -> None:
    """Patch Trio only when the native context watcher cannot observe its switches."""
    if getattr(trio, "_datadog_patch", False) or not context_switches_require_fallback():
        return

    wrap(trio.run, _wrapped_run)
    wrap(trio.lowlevel.start_guest_run, _wrapped_run)
    wrap(trio.to_thread.run_sync, _wrapped_run_sync)
    wrap(trio.from_thread.run, _wrapped_from_thread_run)
    wrap(trio.from_thread.run_sync, _wrapped_from_thread_run)
    trio._datadog_patch = True


def unpatch() -> None:
    """Remove Trio context-switch instrumentation for subsequently started runs."""
    if not getattr(trio, "_datadog_patch", False):
        return

    unwrap(trio.run, _wrapped_run)
    unwrap(trio.lowlevel.start_guest_run, _wrapped_run)
    unwrap(trio.to_thread.run_sync, _wrapped_run_sync)
    unwrap(trio.from_thread.run, _wrapped_from_thread_run)
    unwrap(trio.from_thread.run_sync, _wrapped_from_thread_run)
    trio._datadog_patch = False


def _wrapped_run(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Add the context instrument without replacing caller-provided instruments."""
    instruments = get_argument_value(args, kwargs, len(args), "instruments", optional=True) or ()
    args, kwargs = set_argument_value(
        args, kwargs, len(args), "instruments", (*instruments, _ContextSwitchInstrument()), override_unset=True
    )
    return wrapped(*args, **kwargs)


def _run_with_context_switches(func: Callable[..., Any], *args: Any) -> Any:
    """Publish the copied worker context and clear native thread state on exit."""
    if core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
        core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
    try:
        return func(*args)
    finally:
        if core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
            Context().run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)


async def _run_async_with_context_switches(func: Callable[..., Any], *args: Any) -> Any:
    """Publish context around a callback coroutine while preserving its asynchronous shape."""
    if core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
        core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
    try:
        return await func(*args)
    finally:
        if core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
            Context().run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)


def _wrapped_run_sync(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Publish direct Trio worker boundaries."""
    sync_fn = cast(Callable[..., Any], get_argument_value(args, kwargs, 0, "sync_fn"))
    args, kwargs = set_argument_value(args, kwargs, 0, "sync_fn", partial(_run_with_context_switches, sync_fn))
    return wrapped(*args, **kwargs)


def _wrapped_from_thread_run(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Publish callback boundaries for both synchronous and asynchronous Trio re-entry."""
    fn = cast(Callable[..., Any], get_argument_value(args, kwargs, 0, "fn"))
    context_runner = _run_async_with_context_switches if inspect.iscoroutinefunction(fn) else _run_with_context_switches
    args, kwargs = set_argument_value(args, kwargs, 0, "fn", partial(context_runner, fn))
    return wrapped(*args, **kwargs)
