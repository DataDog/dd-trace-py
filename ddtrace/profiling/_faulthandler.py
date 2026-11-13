"""Faulthandler compatibility for the stack profiler.

Python's faulthandler module installs a SIGSEGV handler that can interfere with
the stack profiler's safe_memcpy recovery mechanism. This module wraps
faulthandler.enable and faulthandler.disable to reinstall our handler on top,
ensuring both systems coexist correctly:
- Our handler recovers from expected faults during safe_memcpy (siglongjmp)
- For unexpected faults, we chain to faulthandler's handler for traceback output

The handler swap must not race with the sampling thread: if safe_memcpy faults
while our handler is temporarily uninstalled, the fault goes to faulthandler or
the default handler and the process crashes. To prevent this, we pause the
sampling thread (waiting for any in-flight sample to complete) before swapping.

The wrap is applied via ModuleWatchdog so it takes effect regardless of when
faulthandler is imported (before or after ddtrace).
"""

from __future__ import annotations

from types import ModuleType
import typing
from typing import Callable

from ddtrace.internal.datadog.profiling import stack
from ddtrace.internal.datadog.profiling.stack._handler_swap_lock import handler_swap_lock
from ddtrace.internal.module import ModuleWatchdog


@ModuleWatchdog.after_module_imported("faulthandler")
def _(faulthandler: ModuleType) -> None:
    if not stack.is_available:
        return

    _original_enable: Callable[..., None] = faulthandler.enable
    _original_disable: Callable[[], bool] = faulthandler.disable

    def _patched_enable(*args: typing.Any, **kwargs: typing.Any) -> None:
        # Same lock as crashtracking.start: pause is not nested across components.
        with handler_swap_lock:
            # True=paused, False=not running, None=timeout mid-copy — on timeout do
            # not enable (would replace recovery) or reinstall (handler cycle).
            pause_result: bool | None = stack.pause_sampling()
            safe_to_swap: bool = pause_result is not None
            if not safe_to_swap:
                return
            try:
                try:
                    stack.uninstall_segv_handler()
                except Exception:  # nosec: B110
                    pass

                # Disable faulthandler before re-enabling so it does a fresh
                # sigaction install.  Without this, Python may reinstall the
                # handler while it is already the current handler, saving itself as
                # its own `previous` and creating an infinite handler loop.
                try:
                    _original_disable()
                except Exception:  # nosec: B110
                    pass

                try:
                    _original_enable(*args, **kwargs)
                except Exception:
                    try:
                        stack.reinstall_segv_handler()
                    except Exception:  # nosec: B110
                        pass

                    # Re-raise so callers see the same exception as without our wrapper.
                    raise

                try:
                    stack.reinstall_segv_handler()
                except Exception:  # nosec: B110
                    pass
            finally:
                if pause_result is True:
                    stack.resume_sampling()

    def _patched_disable() -> bool:
        with handler_swap_lock:
            pause_result: bool | None = stack.pause_sampling()
            safe_to_swap: bool = pause_result is not None
            try:
                if not safe_to_swap:
                    # Sampler timed out at a safe pause point; swapping signal
                    # handlers now would race with safe_memcpy.
                    return False

                try:
                    disabled: bool = _original_disable()
                except Exception:  # nosec: B110
                    disabled = False

                # faulthandler.disable restores its saved previous handler, which
                # may not be ddtrace's handler (it was recorded before we reinstalled
                # on top). Reinstall ddtrace's handler so safe_memcpy recovery works.
                try:
                    stack.reinstall_segv_handler()
                except Exception:  # nosec: B110
                    pass

                return disabled
            finally:
                if pause_result is True:
                    stack.resume_sampling()

    faulthandler.enable = _patched_enable  # type: ignore[attr-defined]
    faulthandler.disable = _patched_disable  # type: ignore[attr-defined]

    # Handle case where faulthandler was already enabled before we patched
    if faulthandler.is_enabled():
        try:
            stack.reinstall_segv_handler()
        except Exception:  # nosec: B110
            pass
