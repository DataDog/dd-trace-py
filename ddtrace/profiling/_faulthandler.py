# -*- encoding: utf-8 -*-
"""Faulthandler compatibility for the stack profiler.

Python's faulthandler module installs a SIGSEGV handler that can interfere with
the stack profiler's safe_memcpy recovery mechanism. This module wraps
faulthandler.enable to reinstall our handler on top, ensuring both systems
coexist correctly:

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
from ddtrace.internal.module import ModuleWatchdog


@ModuleWatchdog.after_module_imported("faulthandler")
def _(faulthandler: ModuleType) -> None:
    if not stack.is_available:
        return

    _original_enable: Callable[..., None] = faulthandler.enable
    _original_disable: Callable[[], None] = faulthandler.disable

    def _patched_enable(*args: typing.Any, **kwargs: typing.Any) -> None:
        was_paused: bool = stack.pause_sampling()
        try:
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
                try:
                    stack.reinstall_segv_handler()
                except Exception:  # nosec: B110
                    pass
                raise

            try:
                stack.reinstall_segv_handler()
            except Exception:  # nosec: B110
                pass
        finally:
            if was_paused:
                stack.resume_sampling()

    faulthandler.enable = _patched_enable  # type: ignore[attr-defined]

    # Handle case where faulthandler was already enabled before we patched
    if faulthandler.is_enabled():
        try:
            stack.reinstall_segv_handler()
        except Exception:  # nosec: B110
            pass
