"""Publish python.context.switch around uvloop callbacks.

uvloop enters a callback's Context from Cython rather than through Context.run, so there
is no equivalent of asyncio's Handle._run to hook. Each callback scheduled through the
loop's Python API is wrapped instead, publishing on entry and again on the way out, the
second time through a snapshot of the loop's ambient Context because uvloop does not leave
the callback's Context until the wrapper returns.

That covers Loop.call_soon, Loop.call_soon_threadsafe, Loop.call_later and Loop.call_at.
It does not cover callbacks uvloop schedules from Cython: add_reader, add_writer and
add_signal_handler build their Handle directly, capturing the Context once at registration.
"""

import asyncio
from contextvars import Context
from contextvars import copy_context
import sys
from types import ModuleType
from typing import Any
from typing import Callable
from typing import Optional
from weakref import WeakSet

import wrapt

from ddtrace.internal import core
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.wrappers import unwrap


_installed = False
_AMBIENT_CONTEXT_ATTR = "_dd_context_switch_ambient_context"
_PATCH_MARKER = "_dd_context_switch_patch"
_MISSING = object()
# Loops whose ambient Context was captured by _patch rather than by _wrapped_run_forever.
# Their run_forever is already in flight and unwrapped, so only uninstalling drops these.
_late_patched_loops: "WeakSet[Any]" = WeakSet()


def install() -> None:
    global _installed
    if _installed:
        return

    _installed = True
    ModuleWatchdog.register_module_hook("uvloop", _patch)


def uninstall() -> None:
    global _installed
    if not _installed:
        return

    _installed = False
    ModuleWatchdog.unregister_module_hook("uvloop", _patch)
    uvloop = sys.modules.get("uvloop")
    if uvloop is not None:
        _unpatch(uvloop)


def _patch(uvloop: ModuleType) -> None:
    if getattr(uvloop.Loop, _PATCH_MARKER, False):
        return

    wrapt.wrap_function_wrapper(uvloop, "Loop.call_soon", _wrapped_schedule_callback)
    # Needs its own wrapper: it builds its Handle instead of going through call_soon.
    wrapt.wrap_function_wrapper(uvloop, "Loop.call_soon_threadsafe", _wrapped_schedule_callback)
    # call_at is not wrapped because uvloop implements it by calling call_later, so it
    # already reaches this wrapper. Wrapping both would publish every call_at twice.
    wrapt.wrap_function_wrapper(uvloop, "Loop.call_later", _wrapped_schedule_timer)
    wrapt.wrap_function_wrapper(uvloop, "Loop.run_forever", _wrapped_run_forever)
    setattr(uvloop.Loop, _PATCH_MARKER, True)

    # Patching cannot reach the run_forever frame of a loop that is already running, so
    # capture its ambient Context here to cover the rest of that run.
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    if isinstance(running_loop, uvloop.Loop):
        setattr(running_loop, _AMBIENT_CONTEXT_ATTR, copy_context())
        _late_patched_loops.add(running_loop)


def _unpatch(uvloop: ModuleType) -> None:
    if not getattr(uvloop.Loop, _PATCH_MARKER, False):
        return

    unwrap(uvloop.Loop, "run_forever")
    unwrap(uvloop.Loop, "call_later")
    unwrap(uvloop.Loop, "call_soon_threadsafe")
    unwrap(uvloop.Loop, "call_soon")
    delattr(uvloop.Loop, _PATCH_MARKER)

    for loop in list(_late_patched_loops):
        _drop_ambient_context(loop)
    _late_patched_loops.clear()


def _drop_ambient_context(loop: Any) -> None:
    try:
        delattr(loop, _AMBIENT_CONTEXT_ATTR)
    except AttributeError:
        pass


def _wrapped_run_forever(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    # A nested run_forever raises before running anything, so restore what was already there
    # instead of dropping the snapshot the outer, still-running frame depends on.
    previous = getattr(instance, _AMBIENT_CONTEXT_ATTR, _MISSING)
    setattr(instance, _AMBIENT_CONTEXT_ATTR, copy_context())
    try:
        return wrapped(*args, **kwargs)
    finally:
        try:
            if previous is _MISSING:
                _drop_ambient_context(instance)
            else:
                setattr(instance, _AMBIENT_CONTEXT_ATTR, previous)
        finally:
            core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)


def _wrapped_schedule_callback(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """Wrap the callback of call_soon and call_soon_threadsafe, whose first argument it is."""
    return _wrap_scheduled_callback(wrapped, instance, args, kwargs, 0)


def _wrapped_schedule_timer(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """Wrap the callback of call_later, which takes a delay before it."""
    return _wrap_scheduled_callback(wrapped, instance, args, kwargs, 1)


def _wrap_scheduled_callback(
    wrapped: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    callback_position: int,
) -> Any:
    # Leave malformed calls alone so uvloop still raises its own TypeError.
    callback = get_argument_value(args, kwargs, callback_position, "callback", optional=True)
    if not callable(callback):
        return wrapped(*args, **kwargs)

    def context_switched_callback(
        wrapped_callback: Callable[..., Any],
        _instance: Any,
        callback_args: tuple[Any, ...],
        callback_kwargs: dict[str, Any],
    ) -> Any:
        # Callbacks scheduled before uninstall() still run afterwards; they must not publish.
        if not _installed:
            return wrapped_callback(*callback_args, **callback_kwargs)
        try:
            core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
            return wrapped_callback(*callback_args, **callback_kwargs)
        finally:
            # uvloop leaves the callback's Context only after this wrapper returns, so the
            # restore has to be published from inside the loop's ambient Context.
            ambient_context: Optional[Context] = getattr(instance, _AMBIENT_CONTEXT_ATTR, None)
            if ambient_context is not None:
                ambient_context.run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)

    callback = wrapt.FunctionWrapper(callback, context_switched_callback)
    args, kwargs = set_argument_value(args, kwargs, callback_position, "callback", callback)
    return wrapped(*args, **kwargs)
