"""Override pytest-timeout's ``method="thread"`` timer so Datadog retries survive.

This module is imported lazily, only when pytest-timeout is installed (see
``pytest_configure`` in ``ddtrace/testing.internal.pytest.plugin``), so it never
imposes an import cost on customers who don't use pytest-timeout.

Why this exists
---------------
pytest-timeout's ``"thread"`` method arms a ``threading.Timer`` whose callback
(``pytest_timeout.timeout_timer``) dumps stacks and calls ``os._exit(1)``. That
hard-kills the process, so the first timed-out test takes the whole session down
before Datadog Auto Test Retries (ATR), Early Flake Detection (EFD), or
Attempt-to-Fix (ATF) can retry it. ``func_only`` only changes *where* the timer is
armed; it does not change the ``os._exit`` behavior.

How it works
------------
When a retry feature is active, we win pytest-timeout's ``firstresult=True``
``pytest_timeout_set_timer`` hook (our impl is ``tryfirst=True``, pytest-timeout's is
``trylast=True``) and install a SIGALRM itimer instead. On timeout the signal handler
calls ``pytest_timeout.timeout_sigalrm`` — pytest-timeout's *own* signal-method
callback — which honors debugger detection (``is_debugging`` / ``SUPPRESS_TIMEOUT``),
dumps the stacks of other threads, and then raises ``pytest.fail`` (a catchable
failure). The test becomes a normal failure that retries can handle, and the process
stays alive.

This is a no-op when no retry feature is active, so customers not using ATR/EFD/ATF
see no behavior change. When SIGALRM is unavailable (e.g. Windows) or we are not in
the main thread, we cannot install the signal timer; we fall back to pytest-timeout's
thread timer and warn once that retries will not survive a timeout.
"""
from __future__ import annotations

import signal
import threading
import typing as t

import pytest

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class PytestTimeoutRetryOverride:
    """Override pytest-timeout's ``method="thread"`` timer with a SIGALRM-based one."""

    __test__ = False

    # Attribute stashed on the item to record that *we* armed the timer, so our
    # ``pytest_timeout_cancel_timer`` only claims the cancel for timers it owns and
    # otherwise lets pytest-timeout's own ``trylast`` cancel clean up.
    _OVERRIDE_ATTR = "_ddtrace_pytest_timeout_override"

    def __init__(self, plugin: t.Any) -> None:
        # ``plugin`` is a TestOptPlugin; we only need its ``manager.settings`` to decide
        # whether a retry feature is active. Typed as Any to avoid an import cycle with the
        # plugin module (which imports this one lazily).
        self._plugin = plugin
        self._warned_no_sigalrm = False

    def _retries_active(self) -> bool:
        s = self._plugin.manager.settings
        return s.auto_test_retries.enabled or s.early_flake_detection.enabled or s.test_management.enabled

    @pytest.hookimpl(tryfirst=True)
    def pytest_timeout_set_timer(self, item: pytest.Item, settings: t.Any) -> t.Optional[bool]:
        # Lazy import: this module is only loaded when pytest-timeout is present, but keep
        # the import local so the module remains importable in isolation for tooling/tests.
        from pytest_timeout import timeout_sigalrm

        if not self._retries_active():
            return None
        # Only the thread method calls os._exit(); the signal method already raises a catchable failure.
        if settings.method != "thread" or not settings.timeout or settings.timeout <= 0:
            return None
        if not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
            if not self._warned_no_sigalrm:
                self._warned_no_sigalrm = True
                log.warning(
                    "Datadog Test Optimization retries are active, but pytest-timeout method=\"thread\" "
                    "cannot be overridden with a signal-based timer here (SIGALRM is unavailable or not "
                    "in the main thread). A timed-out test may kill the process before retries can run; "
                    "use method=\"signal\" for retry support."
                )
            return None

        # Delegate the actual timeout behavior to pytest-timeout's own signal-method callback:
        # it honors debugger detection (is_debugging / SUPPRESS_TIMEOUT), dumps other threads'
        # stacks, and raises pytest.fail — exactly what method="signal" already does. This keeps
        # us consistent with pytest-timeout's semantics (e.g. a breakpoint()/pdb session is not
        # interrupted) instead of reimplementing them.
        def handler(signum, frame):  # noqa: ARG001
            __tracebackhide__ = True
            timeout_sigalrm(item, settings)

        def cancel() -> None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, signal.SIG_DFL)

        item.cancel_timeout = cancel
        setattr(item, self._OVERRIDE_ATTR, True)
        signal.signal(signal.SIGALRM, handler)
        signal.setitimer(signal.ITIMER_REAL, settings.timeout)
        return True

    @pytest.hookimpl(tryfirst=True)
    def pytest_timeout_cancel_timer(self, item: pytest.Item) -> t.Optional[bool]:
        # Only claim the cancel when we claimed the set; otherwise let pytest-timeout's own
        # cancel_timer (trylast) run so it can clean up the timer it installed.
        if not getattr(item, self._OVERRIDE_ATTR, False):
            return None
        try:
            cancel = getattr(item, "cancel_timeout", None)
            if cancel is not None:
                cancel()
        finally:
            setattr(item, self._OVERRIDE_ATTR, False)
        return True
