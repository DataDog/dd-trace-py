"""Publish context switches around Trio tasks and worker-thread callables."""

from contextvars import Context
from importlib.metadata import version
import threading
from typing import Any
from typing import Callable
from typing import cast
import weakref

import trio
import trio.abc
import trio.from_thread
import trio.lowlevel
import trio.to_thread

from ddtrace.internal import core
from ddtrace.internal._context_watcher import CONTEXT_SWITCH_WORKER_INSTRUMENTED
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal._context_watcher import context_switches_require_fallback
from ddtrace.internal._context_watcher import copy_identity
from ddtrace.internal._context_watcher import wrap_worker_context
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


def get_version() -> str:
    return version("trio")


def _supported_versions() -> dict[str, str]:
    return {"trio": ">=0.21.0"}


class _ContextSwitchInstrument(trio.abc.Instrument):  # type: ignore[misc]  # Trio 0.21 has no usable Instrument types.
    """Publish the context entered and left by each Trio task step.

    One instance is built per run (in _wrapped_run, or directly by patch() when it runs inside an
    already-active run) instead of sharing one process-global singleton, so unpatch() disabling
    every instrument it knows about can never retire a *different* run's instrument. enabled still
    exists, per instance, because Trio can only remove an instrument from the thread running its
    owning loop: a foreign-thread unpatch() can only ask this run's own next task step to retire it.
    """

    def __init__(self) -> None:
        self.enabled = True

    def before_task_step(self, task: Any) -> None:
        """Publish task entry inside a copy of its context, or retire before an unmatched entry."""
        if not self.enabled:
            trio.lowlevel.remove_instrument(self)
            return
        if core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
            task.context.copy().run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)

    def after_task_step(self, task: Any) -> None:
        """Publish ambient restoration, then retire if disabled since the matching entry."""
        if core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
            core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
        if not self.enabled:
            trio.lowlevel.remove_instrument(self)


_installed = False
# Every instrument currently owned by a run, so unpatch() can disable all of them regardless of
# which thread runs them. A WeakSet drops an entry on its own once that run (and thus the
# instrument) is garbage collected, so a run that ends without ever being unpatched leaks nothing.
_active_instruments: "weakref.WeakSet[_ContextSwitchInstrument]" = weakref.WeakSet()
_active_instruments_lock = threading.Lock()


def _new_instrument() -> _ContextSwitchInstrument:
    instrument = _ContextSwitchInstrument()
    with _active_instruments_lock:
        _active_instruments.add(instrument)
    return instrument


def _disable_active_instruments() -> None:
    with _active_instruments_lock:
        instruments = list(_active_instruments)
    for instrument in instruments:
        instrument.enabled = False


def patch() -> None:
    """Patch Trio, installing the fallback hooks only when the native context watcher can't cover them."""
    global _installed
    if getattr(trio, "_datadog_patch", False):
        return

    if not _installed and context_switches_require_fallback():
        wrap(trio.run, _wrapped_run)
        wrap(trio.lowlevel.start_guest_run, _wrapped_run)
        wrap(trio.to_thread.run_sync, _wrapped_run_sync)
        wrap(trio.from_thread.run_sync, _wrapped_from_thread_run_sync)
        _installed = True
        try:
            # Instrument the run already active on this thread, if any. Future runs started
            # through the wraps above pick up their own instrument via _wrapped_run instead.
            trio.lowlevel.add_instrument(_new_instrument())
        except RuntimeError:
            pass
    trio._datadog_patch = True


def unpatch() -> None:
    """Remove Trio context-switch instrumentation."""
    global _installed
    if not getattr(trio, "_datadog_patch", False):
        return

    _disable_active_instruments()
    if _installed:
        unwrap(trio.run, _wrapped_run)
        unwrap(trio.lowlevel.start_guest_run, _wrapped_run)
        unwrap(trio.to_thread.run_sync, _wrapped_run_sync)
        unwrap(trio.from_thread.run_sync, _wrapped_from_thread_run_sync)
        _installed = False
    trio._datadog_patch = False


def _wrapped_run(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Add a run-scoped instrument while preserving every caller-provided instrument."""
    kwargs["instruments"] = (*kwargs.get("instruments", ()), _new_instrument())
    return wrapped(*args, **kwargs)


def _wrapped_run_sync(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Publish direct Trio worker boundaries while suppressing immediate AnyIO delegation."""
    sync_fn = cast(Callable[..., Any], get_argument_value(args, kwargs, 0, "sync_fn"))
    if CONTEXT_SWITCH_WORKER_INSTRUMENTED.get():
        return wrapped(*args, **kwargs)

    args, kwargs = set_argument_value(args, kwargs, 0, "sync_fn", wrap_worker_context(sync_fn))
    return wrapped(*args, **kwargs)


def _wrap_from_thread_callback(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Publish callback entry and, on every exit, the ambient restoration for that callback.

    Restoration can't wait for the task-step instrument alone: Trio's entry-queue task drains a
    whole batch of queued callbacks (other from_thread callbacks, run_sync_soon jobs) inside one
    task step, so after_task_step only fires once the batch is done. Publishing here too keeps
    each callback's own restoration from leaking into the next one in the same batch.
    """

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
            core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
        try:
            return fn(*args, **kwargs)
        finally:
            if core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
                Context().run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)

    copy_identity(fn, wrapped)
    return wrapped


def _wrapped_from_thread_run_sync(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Wrap callbacks entering Trio from threads, publishing their own entry and exit."""
    fn = cast(Callable[..., Any], get_argument_value(args, kwargs, 0, "fn"))
    args, kwargs = set_argument_value(args, kwargs, 0, "fn", _wrap_from_thread_callback(fn))
    return wrapped(*args, **kwargs)
