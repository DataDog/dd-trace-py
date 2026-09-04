"""Publish python.context.switch on asyncio Context boundaries when native context watching is unavailable.

Publishes right after entering a Context and again after it is restored, at the
asyncio scheduling points covered by this fallback:

* Handle._run: its callback is temporarily wrapped to publish entry inside the
  Context run and exit after _run restores the loop's ambient Context.
* BaseEventLoop.call_exception_handler on Python before 3.12: publish the
  restored ambient Context before invoking the exception handler.
* Eager task construction through the event loop or asyncio.eager_task_factory:
  the coroutine is instrumented to publish if its first step runs inline, before
  task construction returns.

Thread offloads are handled by the default-on futures integration. This integration only
covers the event loops built into asyncio.
"""

import asyncio
from typing import Any
from typing import Callable

from ddtrace.internal import core
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal._context_watcher import context_switches_require_fallback
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


_installed = False
if PYTHON_VERSION_INFO >= (3, 12):
    _eager_task_factory_code = asyncio.eager_task_factory.__code__  # type: ignore[attr-defined]  # Added in 3.12.
else:
    _eager_task_factory_code = None
_CONTEXT_SWITCH_HANDLE_MARKER = "_dd_context_switch_handle"


def install() -> None:
    """Install only the hooks needed by the current runtime capability."""
    global _installed
    if _installed or not context_switches_require_fallback():
        return

    wrap(asyncio.Handle._run, _wrapped_run_handle)  # type: ignore[arg-type]
    if PYTHON_VERSION_INFO < (3, 12):
        wrap(asyncio.BaseEventLoop.call_exception_handler, _wrapped_call_exception_handler)  # type: ignore[arg-type]
    _installed = True
    if _eager_task_factory_code is not None:
        wrap(asyncio.eager_task_factory, _wrapped_eager_task_factory)  # type: ignore[attr-defined]
        wrap(asyncio.BaseEventLoop.create_task, _wrapped_create_task)  # type: ignore[arg-type]


def uninstall() -> None:
    """Remove every installed asyncio fallback hook."""
    global _installed
    if not _installed:
        return

    if _eager_task_factory_code is not None:
        unwrap(asyncio.BaseEventLoop.create_task, _wrapped_create_task)
        unwrap(asyncio.eager_task_factory, _wrapped_eager_task_factory)  # type: ignore[attr-defined]
    if PYTHON_VERSION_INFO < (3, 12):
        unwrap(asyncio.BaseEventLoop.call_exception_handler, _wrapped_call_exception_handler)
    unwrap(asyncio.Handle._run, _wrapped_run_handle)
    _installed = False


def _wrapped_run_handle(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    handle = args[0]
    original_callback = handle._callback

    def _callback_with_entry_dispatch(*cb_args: Any, **cb_kwargs: Any) -> Any:
        try:
            # Dispatching inside the callback reuses the Context.run performed by
            # Handle._run instead of entering the captured Context a second time.
            core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
            return original_callback(*cb_args, **cb_kwargs)
        finally:
            if getattr(handle._callback, _CONTEXT_SWITCH_HANDLE_MARKER, None) is handle:
                handle._callback = original_callback

    setattr(_callback_with_entry_dispatch, _CONTEXT_SWITCH_HANDLE_MARKER, handle)
    handle._callback = _callback_with_entry_dispatch
    try:
        return wrapped(*args, **kwargs)
    finally:
        # Cancellation can set _callback to None to release references. Restore
        # only when the callback slot still contains our temporary wrapper.
        if getattr(handle._callback, _CONTEXT_SWITCH_HANDLE_MARKER, None) is handle:
            handle._callback = original_callback
        core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)


def _wrapped_call_exception_handler(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Publish the ambient Context before a pre-3.12 exception handler runs."""
    core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
    return wrapped(*args, **kwargs)


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


def _call_with_inline_first_step(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Instrument a task constructor's coroutine while preserving its return value."""
    coro = get_argument_value(args, kwargs, 1, "coro")
    if not asyncio.iscoroutine(coro):
        return wrapped(*args, **kwargs)

    coro, finish_inline_step = _instrument_inline_first_step(coro)
    args, kwargs = set_argument_value(args, kwargs, 1, "coro", coro)
    try:
        return wrapped(*args, **kwargs)
    finally:
        finish_inline_step()


def _wrapped_eager_task_factory(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Publish context changes caused by direct eager task factory calls."""
    return _call_with_inline_first_step(wrapped, args, kwargs)


def _wrapped_create_task(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Publish context changes caused by an inline first task step."""
    task_factory = args[0].get_task_factory()
    eager_factory = (
        _eager_task_factory_code is not None and getattr(task_factory, "__code__", None) is _eager_task_factory_code
    )
    if not kwargs.get("eager_start") and not eager_factory:
        return wrapped(*args, **kwargs)

    return _call_with_inline_first_step(wrapped, args, kwargs)
