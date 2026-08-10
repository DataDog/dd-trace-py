# -*- encoding: utf-8 -*-
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

import threading
from types import ModuleType
import typing
from typing import Callable

from ddtrace.internal.datadog.profiling import stack
from ddtrace.internal.module import ModuleWatchdog


@ModuleWatchdog.after_module_imported("faulthandler")
def _(faulthandler: ModuleType) -> None:
    if not stack.is_available:
        return

    _original_enable: Callable[..., None] = faulthandler.enable
    _original_disable: Callable[[], bool] = faulthandler.disable
    _enable_lock: threading.Lock = threading.Lock()

    def _patched_enable(*args: typing.Any, **kwargs: typing.Any) -> None:
        with _enable_lock:
            # pause_sampling returns:
            #   True  — sampler paused (safe to swap handlers)
            #   False — sampler not running (safe to swap; no racing thread)
            #   None  — timed out: sampler IS running but didn't pause in time;
            #           skip the uninstall process to avoid racing with safe_memcpy
            #           (This means there will be a fault handler cycle, but this is about
            #           the only thing we can do.)
            pause_result: bool | None = stack.pause_sampling()
            safe_to_swap: bool = pause_result is not None
            try:
                if safe_to_swap:
                    try:
                        stack.uninstall_segv_handler()
                    except Exception:  # nosec: B110
                        pass

                    # Disable faulthandler before re-enabling so it does a fresh
                    # sigaction install.  Without this, Python may reinstall the
                    # handler while it is already the current handler, saving itself as
                    # its own ``previous`` and creating an infinite handler loop.
                    try:
                        _original_disable()
                    except Exception:  # nosec: B110
                        pass

                try:
                    _original_enable(*args, **kwargs)
                except Exception:
                    if safe_to_swap:
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
        with _enable_lock:
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
