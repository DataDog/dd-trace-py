"""Publish context switches around Trio tasks and worker-thread callables."""

from functools import wraps
from importlib.metadata import version
from typing import Any
from typing import Callable
from typing import cast

import trio
import trio.abc
import trio.from_thread
import trio.lowlevel
import trio.to_thread

from ddtrace.internal import core
from ddtrace.internal._context_watcher import CONTEXT_SWITCH_WORKER_INSTRUMENTED
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal._context_watcher import context_switches_require_fallback
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
    """Publish the context entered and left by each Trio task step."""

    def __init__(self) -> None:
        self.enabled = False

    def enable(self) -> None:
        """Allow task hooks to publish until the next disable call."""
        self.enabled = True

    def disable(self) -> None:
        """Make every registered instance inert until each run can retire it safely."""
        self.enabled = False

    def before_task_step(self, task: Any) -> None:
        """Publish task entry inside its copied context or retire before an unmatched entry."""
        if not self.enabled:
            trio.lowlevel.remove_instrument(self)
            return
        task.context.run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)

    def after_task_step(self, task: Any) -> None:
        """Publish ambient restoration before retiring after an in-step disable."""
        core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
        if not self.enabled:
            trio.lowlevel.remove_instrument(self)


_CONTEXT_SWITCH_INSTRUMENT = _ContextSwitchInstrument()


def patch() -> None:
    """Patch Trio when the native context watcher is unavailable."""
    if getattr(trio, "_datadog_patch", False) or not context_switches_require_fallback():
        return

    _CONTEXT_SWITCH_INSTRUMENT.enable()
    wrap(trio.run, _wrapped_run)
    wrap(trio.lowlevel.start_guest_run, _wrapped_run)
    wrap(trio.to_thread.run_sync, _wrapped_run_sync)
    wrap(trio.from_thread.run_sync, _wrapped_from_thread_run_sync)
    try:
        trio.lowlevel.add_instrument(_CONTEXT_SWITCH_INSTRUMENT)
    except RuntimeError:
        # No active Trio run is expected during normal startup patching.
        pass
    trio._datadog_patch = True


def unpatch() -> None:
    """Remove Trio context-switch instrumentation."""
    _CONTEXT_SWITCH_INSTRUMENT.disable()
    if not getattr(trio, "_datadog_patch", False):
        return

    unwrap(trio.run, _wrapped_run)
    unwrap(trio.lowlevel.start_guest_run, _wrapped_run)
    unwrap(trio.to_thread.run_sync, _wrapped_run_sync)
    unwrap(trio.from_thread.run_sync, _wrapped_from_thread_run_sync)
    trio._datadog_patch = False


def _wrapped_run(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Add the singleton once while preserving every caller-provided instrument."""
    instruments = kwargs.get("instruments", ())
    if not any(instrument is _CONTEXT_SWITCH_INSTRUMENT for instrument in instruments):
        kwargs["instruments"] = (*instruments, _CONTEXT_SWITCH_INSTRUMENT)
    return wrapped(*args, **kwargs)


def _wrapped_run_sync(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Publish direct Trio worker boundaries while suppressing immediate AnyIO delegation."""
    sync_fn = cast(Callable[..., Any], get_argument_value(args, kwargs, 0, "sync_fn"))
    if CONTEXT_SWITCH_WORKER_INSTRUMENTED.get():
        return wrapped(*args, **kwargs)

    args, kwargs = set_argument_value(args, kwargs, 0, "sync_fn", wrap_worker_context(sync_fn))
    return wrapped(*args, **kwargs)


def _wrap_from_thread_callback(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Publish callback entry while the task-step instrument owns Trio restoration."""

    @wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
        return fn(*args, **kwargs)

    return wrapped


def _wrapped_from_thread_run_sync(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Wrap callbacks entering Trio from threads without publishing a synthetic exit."""
    fn = cast(Callable[..., Any], get_argument_value(args, kwargs, 0, "fn"))
    args, kwargs = set_argument_value(args, kwargs, 0, "fn", _wrap_from_thread_callback(fn))
    return wrapped(*args, **kwargs)
