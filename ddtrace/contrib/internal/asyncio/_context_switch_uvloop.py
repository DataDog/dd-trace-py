"""Publish python.context.switch around uvloop callbacks."""

import asyncio
from contextvars import copy_context
import sys
from typing import Any
from typing import Callable

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


def _patch(uvloop: Any) -> None:
    if getattr(uvloop.Loop, _PATCH_MARKER, False):
        return

    wrapt.wrap_function_wrapper(uvloop, "Loop.call_soon", _wrapped_schedule_callback)
    wrapt.wrap_function_wrapper(uvloop, "Loop.call_soon_threadsafe", _wrapped_schedule_callback)
    wrapt.wrap_function_wrapper(uvloop, "Loop.call_later", _wrapped_schedule_timer)
    wrapt.wrap_function_wrapper(uvloop, "Loop.run_forever", _wrapped_run_forever)
    setattr(uvloop.Loop, _PATCH_MARKER, True)

    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        if isinstance(running_loop, uvloop.Loop):
            setattr(running_loop, _AMBIENT_CONTEXT_ATTR, copy_context())


def _unpatch(uvloop: Any) -> None:
    if not getattr(uvloop.Loop, _PATCH_MARKER, False):
        return

    unwrap(uvloop.Loop, "run_forever")
    unwrap(uvloop.Loop, "call_later")
    unwrap(uvloop.Loop, "call_soon_threadsafe")
    unwrap(uvloop.Loop, "call_soon")
    delattr(uvloop.Loop, _PATCH_MARKER)


def _wrapped_run_forever(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    setattr(instance, _AMBIENT_CONTEXT_ATTR, copy_context())
    try:
        return wrapped(*args, **kwargs)
    finally:
        delattr(instance, _AMBIENT_CONTEXT_ATTR)
        core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)


def _wrapped_schedule_callback(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    return _wrap_scheduled_callback(wrapped, instance, args, kwargs, 0)


def _wrapped_schedule_timer(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    return _wrap_scheduled_callback(wrapped, instance, args, kwargs, 1)


def _wrap_scheduled_callback(
    wrapped: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    callback_position: int,
) -> Any:
    callback = get_argument_value(args, kwargs, callback_position, "callback")
    if not callable(callback):
        return wrapped(*args, **kwargs)

    def context_switched_callback(
        wrapped_callback: Callable[..., Any],
        _instance: Any,
        callback_args: tuple[Any, ...],
        callback_kwargs: dict[str, Any],
    ) -> Any:
        if not _installed:
            return wrapped_callback(*callback_args, **callback_kwargs)
        try:
            core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
            return wrapped_callback(*callback_args, **callback_kwargs)
        finally:
            ambient_context = getattr(instance, _AMBIENT_CONTEXT_ATTR, None)
            if ambient_context is not None:
                ambient_context.run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)

    callback = wrapt.FunctionWrapper(callback, context_switched_callback)
    args, kwargs = set_argument_value(args, kwargs, callback_position, "callback", callback)
    return wrapped(*args, **kwargs)
