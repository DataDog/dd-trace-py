"""Publish context switches around Trio tasks and worker-thread callables."""

from contextvars import Context
from functools import partial
from importlib.metadata import version
from typing import Any
from typing import Callable
from typing import cast

import trio
import trio.abc
import trio.lowlevel
import trio.to_thread

from ddtrace.internal import core
from ddtrace.internal._context_watcher import CONTEXT_SWITCH_WORKER_INSTRUMENTED
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
    """Publish the context entered and left by each Trio task step."""

    def before_task_step(self, task: Any) -> None:
        task.context.run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)

    def after_task_step(self, task: Any) -> None:
        core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)


_CONTEXT_SWITCH_INSTRUMENT = _ContextSwitchInstrument()


def patch() -> None:
    """Patch Trio when the native context watcher is unavailable."""
    if getattr(trio, "_datadog_patch", False) or not context_switches_require_fallback():
        return

    wrap(trio.run, _wrapped_run)
    wrap(trio.lowlevel.start_guest_run, _wrapped_run)
    wrap(trio.to_thread.run_sync, _wrapped_run_sync)
    try:
        trio.lowlevel.add_instrument(_CONTEXT_SWITCH_INSTRUMENT)
    except RuntimeError:
        pass
    trio._datadog_patch = True


def unpatch() -> None:
    """Remove Trio context-switch instrumentation."""
    if not getattr(trio, "_datadog_patch", False):
        return

    try:
        trio.lowlevel.remove_instrument(_CONTEXT_SWITCH_INSTRUMENT)
    except (KeyError, RuntimeError):
        pass
    unwrap(trio.run, _wrapped_run)
    unwrap(trio.lowlevel.start_guest_run, _wrapped_run)
    unwrap(trio.to_thread.run_sync, _wrapped_run_sync)
    trio._datadog_patch = False


def _wrapped_run(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    instruments = kwargs.get("instruments", ())
    kwargs["instruments"] = (*instruments, _CONTEXT_SWITCH_INSTRUMENT)
    return wrapped(*args, **kwargs)


def _run_with_context_switches(func: Callable[..., Any], *args: Any) -> Any:
    """Publish the copied worker context and the empty context restored afterwards."""
    core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
    try:
        return func(*args)
    finally:
        Context().run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)


def _wrapped_run_sync(wrapped: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    sync_fn = cast(Callable[..., Any], get_argument_value(args, kwargs, 0, "sync_fn"))
    if CONTEXT_SWITCH_WORKER_INSTRUMENTED.get():
        return wrapped(*args, **kwargs)

    args, kwargs = set_argument_value(args, kwargs, 0, "sync_fn", partial(_run_with_context_switches, sync_fn))
    return wrapped(*args, **kwargs)
