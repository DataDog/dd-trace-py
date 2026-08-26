"""Publish python.context.switch on asyncio Context boundaries the native watcher cannot see.

Publishes right after entering a Context and again after it is restored, at the two
places asyncio can hand control to a different Context:

* Handle._run: its callback is temporarily wrapped to publish entry inside the
  Context run and exit after _run restores the loop's ambient Context.
* Eager task construction: the coroutine is instrumented to publish if its first step
  runs inline, before the task constructor returns.

Thread offloads are handled by the default-on futures integration. This fallback only
covers the event loops built into asyncio.
"""

import asyncio
from typing import Any
from typing import Callable

from ddtrace.internal import core
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal._context_watcher import context_switches_require_fallback
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


_installed = False
_original_task = asyncio.Task
_eager_task_factory_code = getattr(getattr(asyncio, "eager_task_factory", None), "__code__", None)
_create_task_coroutine_ids: set[int] = set()
_task_replaced = False


class _ContextSwitchTaskMeta(type):
    def __instancecheck__(cls, instance: Any) -> bool:
        if cls is _ContextSwitchTask:
            return isinstance(instance, _original_task)
        return super().__instancecheck__(instance)

    def __subclasscheck__(cls, subclass: type) -> bool:
        if cls is _ContextSwitchTask:
            return issubclass(subclass, _original_task)
        return super().__subclasscheck__(subclass)

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        if not (_installed and kwargs.get("eager_start") and (args or "coro" in kwargs)):
            return super().__call__(*args, **kwargs)

        coro = get_argument_value(args, kwargs, 0, "coro")
        if not asyncio.iscoroutine(coro) or id(coro) in _create_task_coroutine_ids:
            return super().__call__(*args, **kwargs)

        switch_loop = kwargs.get("loop")
        if switch_loop is None:
            try:
                switch_loop = asyncio.get_running_loop()
            except RuntimeError:
                return super().__call__(*args, **kwargs)
        if not switch_loop.is_running():
            return super().__call__(*args, **kwargs)

        coro, finish_inline_step = _instrument_inline_first_step(coro)
        args, kwargs = set_argument_value(args, kwargs, 0, "coro", coro)
        try:
            return super().__call__(*args, **kwargs)
        finally:
            finish_inline_step()


class _ContextSwitchTask(asyncio.Task[Any], metaclass=_ContextSwitchTaskMeta):  # type: ignore[misc]
    """Intercept direct eager Task construction while the Python fallback is active."""


def install() -> None:
    """Install only the hooks needed by the current runtime capability."""
    global _installed
    global _task_replaced
    if _installed or not context_switches_require_fallback():
        return

    wrap(asyncio.Handle._run, _wrapped_run_handle)  # type: ignore[arg-type]
    _installed = True
    try:
        if _eager_task_factory_code is not None:
            wrap(asyncio.BaseEventLoop.create_task, _wrapped_create_task)  # type: ignore[arg-type]
            if asyncio.Task is _original_task:
                setattr(asyncio, "Task", _ContextSwitchTask)
                _task_replaced = True
    except BaseException:
        uninstall()
        raise


def uninstall() -> None:
    """Remove every installed asyncio fallback hook."""
    global _installed
    global _task_replaced
    if not _installed:
        return

    if _task_replaced and asyncio.Task is _ContextSwitchTask:
        setattr(asyncio, "Task", _original_task)
    _task_replaced = False
    if _eager_task_factory_code is not None:
        unwrap(asyncio.BaseEventLoop.create_task, _wrapped_create_task)
    unwrap(asyncio.Handle._run, _wrapped_run_handle)
    _installed = False


def _wrapped_run_handle(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    handle = args[0]
    original_callback = handle._callback

    def _callback_with_entry_dispatch(*cb_args: Any, **cb_kwargs: Any) -> Any:
        # Dispatching inside the callback reuses the Context.run performed by
        # Handle._run instead of entering the captured Context a second time.
        core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
        try:
            return original_callback(*cb_args, **cb_kwargs)
        except BaseException:
            # Restore the application callback before asyncio formats its failure.
            if handle._callback is _callback_with_entry_dispatch:
                handle._callback = original_callback
            raise

    handle._callback = _callback_with_entry_dispatch
    try:
        return wrapped(*args, **kwargs)
    finally:
        # Cancellation can set _callback to None to release references. Restore
        # only when the callback slot still contains our temporary wrapper.
        if handle._callback is _callback_with_entry_dispatch:
            handle._callback = original_callback
        core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)


def _task_may_run_inline(loop: Any, kwargs: dict[str, Any]) -> bool:
    """Whether create_task can execute the coroutine before returning to its caller."""
    task_factory = loop.get_task_factory()
    return bool(kwargs.get("eager_start")) or (
        _eager_task_factory_code is not None and getattr(task_factory, "__code__", None) is _eager_task_factory_code
    )


def _instrument_inline_first_step(coro: Any) -> tuple[Any, Callable[[], None]]:
    """Instrument a coroutine only if its first step runs before construction returns."""
    construction_pending = True
    switch_published = False

    async def context_switched_coro() -> Any:
        nonlocal switch_published
        if construction_pending:
            switch_published = True
            core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
        return await coro

    def finish_inline_step() -> None:
        nonlocal construction_pending
        construction_pending = False
        if switch_published:
            core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)

    return context_switched_coro(), finish_inline_step


def _wrapped_create_task(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Publish context changes caused by an inline first task step."""
    loop = args[0]
    if not _task_may_run_inline(loop, kwargs):
        return wrapped(*args, **kwargs)

    coro = get_argument_value(args, kwargs, 1, "coro")
    if not asyncio.iscoroutine(coro):
        return wrapped(*args, **kwargs)

    coro, finish_inline_step = _instrument_inline_first_step(coro)
    args, kwargs = set_argument_value(args, kwargs, 1, "coro", coro)
    coro_id = id(coro)
    _create_task_coroutine_ids.add(coro_id)
    try:
        return wrapped(*args, **kwargs)
    finally:
        _create_task_coroutine_ids.discard(coro_id)
        finish_inline_step()
