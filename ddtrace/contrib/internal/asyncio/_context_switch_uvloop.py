"""Publish python.context.switch around uvloop callbacks.

uvloop enters a callback's Context from Cython instead of going through Context.run, so
there is no equivalent of asyncio's Handle._run to hook. The callback itself is replaced at
schedule time instead: it publishes on entry and again on the way out, the second time from
a copy of the loop's ambient Context, because uvloop does not leave the callback's Context
until the wrapper has returned.

Covered: every callback that crosses the loop's Python scheduling API, and the exception
handler, which uvloop runs inside the failed callback's Context rather than the ambient
one. call_at needs no wrapper of its own, uvloop implements it by calling call_later.

Not covered: callbacks uvloop dispatches entirely from Cython, in particular the transport
and protocol methods such as data_received, which never pass through Python scheduling.
Callbacks of a loop that was already running when this shim was installed are also skipped:
that run's ambient Context cannot be reached from Python. A process whose loop runs once
and forever therefore stays uninstrumented until it restarts.

In loop debug mode, the "created at" line of a Handle repr and of the slow-callback warning
points at this module instead of at the code that scheduled the callback.
"""

import asyncio
from contextvars import Context
from contextvars import copy_context
import sys
from types import ModuleType
from typing import Any
from typing import Callable
from typing import Optional

import wrapt

from ddtrace.internal import core
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value


_installed = False
_AMBIENT_CONTEXT_ATTR = "_dd_context_switch_ambient_context"
_PATCH_MARKER = "_dd_context_switch_patch"
log = get_logger(__name__)


def install() -> None:
    """Track uvloop imports so fallback runtimes can observe its scheduling boundaries."""
    global _installed
    if _installed:
        return

    _installed = True
    ModuleWatchdog.register_module_hook("uvloop", _patch)


def uninstall() -> None:
    """Undo this module's process-wide wrappers before fallback shutdown."""
    global _installed
    if not _installed:
        return

    try:
        ModuleWatchdog.unregister_module_hook("uvloop", _patch)
        uvloop = sys.modules.get("uvloop")
        if uvloop is not None:
            _unpatch(uvloop)
    finally:
        # Cleared last, so that a wrapper left in place by a failed removal still stops publishing.
        _installed = False


def _patch(uvloop: ModuleType) -> None:
    """Install wrappers after uvloop imports without importing it proactively."""
    if getattr(uvloop.Loop, _PATCH_MARKER, False):
        return

    # Marked before wrapping, so that _unpatch can undo a patch that fails halfway through.
    setattr(uvloop.Loop, _PATCH_MARKER, True)
    try:
        wrapt.wrap_function_wrapper(uvloop, "Loop.call_soon", _wrapped_call_soon)
        wrapt.wrap_function_wrapper(uvloop, "Loop.call_soon_threadsafe", _wrapped_call_soon_threadsafe)
        wrapt.wrap_function_wrapper(uvloop, "Loop.call_later", _wrapped_call_later)
        wrapt.wrap_function_wrapper(uvloop, "Loop.add_reader", _wrapped_add_reader)
        wrapt.wrap_function_wrapper(uvloop, "Loop.add_writer", _wrapped_add_writer)
        wrapt.wrap_function_wrapper(uvloop, "Loop.add_signal_handler", _wrapped_add_signal_handler)
        wrapt.wrap_function_wrapper(uvloop, "Loop.call_exception_handler", _wrapped_call_exception_handler)
        wrapt.wrap_function_wrapper(uvloop, "Loop.run_forever", _wrapped_run_forever)
    except Exception:
        # A uvloop that renamed a method loses its context switches, not the whole asyncio
        # integration: this hook runs without a caller-side guard when uvloop imports first.
        log.debug("failed to instrument uvloop context switches", exc_info=True)
        _unpatch(uvloop)
        return

    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    if isinstance(running_loop, uvloop.Loop):
        log.debug("uvloop context-switch instrumentation skips the run in progress; it starts at the next run")


def _unpatch(uvloop: ModuleType) -> None:
    """Remove this module's wrappers, leaving anything wrapped on top of them alone."""
    if not getattr(uvloop.Loop, _PATCH_MARKER, False):
        return

    # Every method is unwrapped before the marker is decided, rather than stopping at the
    # first refusal. The marker stays behind for whatever could not be removed, so that a
    # later _patch does not wrap those methods a second time.
    removed = [
        _unwrap_own_wrapper(uvloop.Loop, "call_soon", _wrapped_call_soon),
        _unwrap_own_wrapper(uvloop.Loop, "call_soon_threadsafe", _wrapped_call_soon_threadsafe),
        _unwrap_own_wrapper(uvloop.Loop, "call_later", _wrapped_call_later),
        _unwrap_own_wrapper(uvloop.Loop, "add_reader", _wrapped_add_reader),
        _unwrap_own_wrapper(uvloop.Loop, "add_writer", _wrapped_add_writer),
        _unwrap_own_wrapper(uvloop.Loop, "add_signal_handler", _wrapped_add_signal_handler),
        _unwrap_own_wrapper(uvloop.Loop, "call_exception_handler", _wrapped_call_exception_handler),
        _unwrap_own_wrapper(uvloop.Loop, "run_forever", _wrapped_run_forever),
    ]
    if all(removed):
        delattr(uvloop.Loop, _PATCH_MARKER)


def _unwrap_own_wrapper(owner: Any, method: str, wrapper: Callable[..., Any]) -> bool:
    """Undo one wrapper, reporting whether the attribute is now free of it."""
    current = getattr(owner, method, None)
    underlying = getattr(current, "__wrapped__", None)
    if underlying is not None and getattr(current, "_self_wrapper", None) is wrapper:
        setattr(owner, method, underlying)
        return True

    # Either nothing of ours is here, or another library wrapped on top of us. Unwrapping
    # blindly would remove their wrapper and leave ours in place underneath it.
    return underlying is None


def _ambient_context(loop: Any) -> Optional[Context]:
    """The Context a callback of this loop returns to, or None if the run is not covered."""
    return getattr(loop, _AMBIENT_CONTEXT_ATTR, None) if _installed else None


def _publish(context: Optional[Context] = None) -> None:
    """Publish a context switch, as seen from `context` when one is given."""
    if context is None:
        core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
        return

    # Listeners write contextvars. The real ambient Context is out of reach, so the writes
    # go to a throwaway copy: applying them to the snapshot would move it further away from
    # the ambient Context on every callback.
    context.run(copy_context).run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)


def _wrapped_run_forever(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """Keep a copy of the loop's pre-callback Context available until the run returns."""
    if instance.is_running():
        # uvloop is about to refuse this call. Taking a snapshot or publishing here would
        # overwrite the state of the run that is already in progress.
        return wrapped(*args, **kwargs)

    setattr(instance, _AMBIENT_CONTEXT_ATTR, copy_context())
    try:
        return wrapped(*args, **kwargs)
    finally:
        setattr(instance, _AMBIENT_CONTEXT_ATTR, None)
        _publish()


def _wrapped_call_exception_handler(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """Report a failed callback from the Context uvloop actually runs the handler in."""
    ambient = _ambient_context(instance)
    if ambient is None:
        return wrapped(*args, **kwargs)

    try:
        # Unlike in asyncio, the failed callback's Context is still current here, and the
        # handler traces and logs from it.
        _publish()
        return wrapped(*args, **kwargs)
    finally:
        _publish(ambient)


def _wrapped_call_soon(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """call_soon(callback, *args, context=None)."""
    return _publish_around_callback(wrapped, instance, args, kwargs, 0)


def _wrapped_call_soon_threadsafe(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """call_soon_threadsafe(callback, *args, context=None), which builds its own Handle."""
    return _publish_around_callback(wrapped, instance, args, kwargs, 0)


def _wrapped_call_later(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """call_later(delay, callback, *args, context=None), and call_at through it."""
    return _publish_around_callback(wrapped, instance, args, kwargs, 1)


def _wrapped_add_reader(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """add_reader(fd, callback, *args), whose Context is captured at registration."""
    return _publish_around_callback(wrapped, instance, args, kwargs, 1)


def _wrapped_add_writer(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """add_writer(fd, callback, *args), whose Context is captured at registration."""
    return _publish_around_callback(wrapped, instance, args, kwargs, 1)


def _wrapped_add_signal_handler(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """add_signal_handler(sig, callback, *args), whose Context is captured at registration."""
    return _publish_around_callback(wrapped, instance, args, kwargs, 1)


def _publish_around_callback(
    wrapped: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    callback_position: int,
) -> Any:
    """Schedule a publishing stand-in for the callback these methods take."""
    callback = get_argument_value(args, kwargs, callback_position, "callback", optional=True)
    # Unrecognized arguments go through untouched, leaving validation to uvloop. An
    # uncovered run is skipped here so that it does not pay for a wrapper that publishes
    # nothing.
    if not callable(callback) or not _covered(instance):
        return wrapped(*args, **kwargs)

    callback = _publishing_callback(instance, callback)
    args, kwargs = set_argument_value(args, kwargs, callback_position, "callback", callback)
    return wrapped(*args, **kwargs)


def _covered(loop: Any) -> bool:
    """Whether callbacks scheduled on this loop now will have an ambient Context to return to."""
    return _installed and not (getattr(loop, _AMBIENT_CONTEXT_ATTR, None) is None and loop.is_running())


def _publishing_callback(loop: Any, callback: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a scheduled callback so that entering and leaving its Context is published."""

    def publish_around_callback(*args: Any, **kwargs: Any) -> Any:
        ambient = _ambient_context(loop)
        if ambient is None:
            # Scheduled before uninstall(), or after the run it belonged to ended.
            return callback(*args, **kwargs)

        _publish()
        try:
            return callback(*args, **kwargs)
        finally:
            # uvloop leaves the callback's Context only once this returns, so the restore
            # has to be published from the ambient Context rather than the current one. It
            # is unconditional: an entry without its restore would leave the thread's
            # OpenTelemetry context pointing at a callback that has finished.
            _publish(ambient)

    return publish_around_callback
