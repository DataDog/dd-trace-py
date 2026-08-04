"""Publish Python context changes at uvloop scheduling boundaries.

The builtin asyncio integration wraps asyncio.Handle._run, but uvloop uses its
own Cython Handle with no equivalent Python hook, so the public uvloop.Loop
scheduling methods are patched instead. uvloop enters the callback's captured
contextvars.Context itself, so wrapped callbacks already run in it and only have
to emit the context switch event.

Limitation: Protocol callbacks such as data_received are invoked from C without
passing through any of these methods and therefore publish nothing.
"""

import contextvars
import functools
from types import ModuleType
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import cast

from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.constants import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal.context_watcher import _await_with_context_switch
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value


_RUN_CONTEXT_ATTRIBUTE = "_datadog_context_switch_run_context"
_MISSING_ATTRIBUTE = object()
_patched_attributes: dict[str, tuple[object, object]] = {}
# Position and keyword of the callback argument of each scheduling method.
_CALLBACK_ARGUMENT = {
    "call_soon": (0, "callback"),
    "call_soon_threadsafe": (0, "callback"),
    "call_later": (1, "callback"),
    "call_at": (1, "callback"),
    "add_reader": (1, "callback"),
    "add_writer": (1, "callback"),
    "add_signal_handler": (1, "callback"),
}


def _wrap_callback(loop: Any, callback: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(callback)
    def trampoline(*args: Any) -> Any:
        try:
            core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
            return callback(*args)
        finally:
            # Emit from the Context uvloop restores once the callback returns, which
            # is the one the loop was started in.
            run_context = getattr(loop, _RUN_CONTEXT_ATTRIBUTE, None)
            if run_context is None:
                core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
            else:
                run_context.run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)

    return trampoline


def _callback_scheduler(pos: int, kw: str) -> Callable[..., Any]:
    def schedule(wrapped: Callable[..., Any], loop: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        if not core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
            return wrapped(*args, **kwargs)

        callback = get_argument_value(args, kwargs, pos, kw, optional=True)
        if callback is None:
            return wrapped(*args, **kwargs)
        args, kwargs = set_argument_value(args, kwargs, pos, kw, _wrap_callback(loop, callback))
        return wrapped(*args, **kwargs)

    return schedule


def _wrapped_create_task(wrapped: Callable[..., Any], loop: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Emit context switches around create_task itself for custom task factories.

    Task steps are scheduled through call_soon and covered by the callback
    wrapper, but a custom factory may run the coroutine eagerly before
    create_task returns.
    """
    if not core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT) or loop.get_task_factory() is None:
        return wrapped(*args, **kwargs)

    coro = cast(Awaitable[Any], get_argument_value(args, kwargs, 0, "coro"))
    args, kwargs = set_argument_value(args, kwargs, 0, "coro", _await_with_context_switch(coro))
    try:
        return wrapped(*args, **kwargs)
    finally:
        core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)


def _wrapped_run_forever(wrapped: Callable[..., Any], loop: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Expose the loop Context to callbacks and emit its restoration once the loop stops."""
    if hasattr(loop, _RUN_CONTEXT_ATTRIBUTE):
        return wrapped(*args, **kwargs)

    setattr(loop, _RUN_CONTEXT_ATTRIBUTE, contextvars.copy_context())
    try:
        return wrapped(*args, **kwargs)
    finally:
        delattr(loop, _RUN_CONTEXT_ATTRIBUTE)
        core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)


def patch(module: ModuleType) -> None:
    if _patched_attributes:
        return

    for name, (pos, kw) in _CALLBACK_ARGUMENT.items():
        original = module.Loop.__dict__.get(name, _MISSING_ATTRIBUTE)
        wrap(module, f"Loop.{name}", _callback_scheduler(pos, kw))
        _patched_attributes[name] = (original, module.Loop.__dict__[name])
    for name, wrapper in (("create_task", _wrapped_create_task), ("run_forever", _wrapped_run_forever)):
        original = module.Loop.__dict__.get(name, _MISSING_ATTRIBUTE)
        wrap(module, f"Loop.{name}", wrapper)
        _patched_attributes[name] = (original, module.Loop.__dict__[name])


def unpatch(module: ModuleType) -> None:
    for name, (original, installed) in _patched_attributes.items():
        if module.Loop.__dict__.get(name, _MISSING_ATTRIBUTE) is not installed:
            continue
        if original is _MISSING_ATTRIBUTE:
            delattr(module.Loop, name)
        else:
            setattr(module.Loop, name, original)
    _patched_attributes.clear()
