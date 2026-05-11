# -*- encoding: utf-8 -*-
"""Faulthandler compatibility for the stack profiler.

Python's faulthandler module installs a SIGSEGV handler that can interfere with
the stack profiler's safe_memcpy recovery mechanism. This module wraps
faulthandler.enable to reinstall our handler on top, ensuring both systems
coexist correctly:

- Our handler recovers from expected faults during safe_memcpy (siglongjmp)
- For unexpected faults, we chain to faulthandler's handler for traceback output

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
    # Only apply the wrap if the stack module is available and fast_copy might be used
    if not stack.is_available:
        return

    # faulthandler.enable is a C built-in; we cannot use ddtrace's wrap which
    # requires __code__, so we monkey-patch the module attribute directly.
    _original_enable: Callable[..., None] = faulthandler.enable

    def _patched_enable(*args: typing.Any, **kwargs: typing.Any) -> None:
        _original_enable(*args, **kwargs)

        # Reinstall our SIGSEGV handler on top of faulthandler's.
        # init_segv_catcher saves faulthandler's handler as g_old_segv,
        # so unexpected faults still chain to faulthandler for traceback output.
        try:
            stack.reinstall_segv_handler()
        except Exception:  # nosec: B110
            # If the stack module isn't fully initialized, ignore
            pass

    faulthandler.enable = _patched_enable  # type: ignore[attr-defined]

    # Handle case where faulthandler was already enabled before we patched
    if faulthandler.is_enabled():
        try:
            stack.reinstall_segv_handler()
        except Exception:  # nosec: B110
            pass
