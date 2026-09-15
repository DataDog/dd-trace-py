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
When retries are active, we win pytest-timeout's ``firstresult=True``
``pytest_timeout_set_timer`` hook (our impl is ``tryfirst=True``, pytest-timeout's is
``trylast=True``) and install a SIGALRM itimer instead. On timeout the signal handler
calls ``pytest_timeout.timeout_sigalrm`` — pytest-timeout's *own* signal-method
callback — which honors debugger detection (``is_debugging`` / ``SUPPRESS_TIMEOUT``),
dumps the stacks of other threads, and then raises ``pytest.fail`` (a catchable
failure). The test becomes a normal failure that retries can handle, and the process
stays alive.

This is a no-op when retries are inactive, so customers not using ATR/EFD/ATF see no
behavior change. When SIGALRM is unavailable (e.g. Windows) or we are not in the main
thread, we cannot install the signal timer; we fall back to pytest-timeout's thread
timer and warn once that retries will not survive a timeout.
"""
from __future__ import annotations

import signal
import threading
import typing as t

import pytest

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


try:
    from pytest_timeout import _get_item_settings as _get_pytest_timeout_settings
    from pytest_timeout import timeout_sigalrm
except (ImportError, AttributeError):
    # pytest-timeout is not installed. The functions below guard on this being None and
    # become no-ops, so this module remains safe to import (and to call) without it.
    _get_pytest_timeout_settings = None
    timeout_sigalrm = None


# Stashed on the item to record that *we* armed the timer, so our cancel hook only
# claims cancellation for timers it owns and otherwise lets pytest-timeout's own
# ``trylast`` cancel clean up the timer it installed.
_OVERRIDE_ATTR = "_ddtrace_pytest_timeout_override"


def reset_pytest_timeout_timer(item: pytest.Item) -> None:
    """Cancel and re-arm pytest-timeout's timer so this retry attempt gets a fresh budget.

    pytest-timeout installs its per-test timer in its ``pytest_runtest_protocol`` hookwrapper,
    which only fires once even when we retry by calling ``runtestprotocol()`` directly. Without
    this reset, all retry attempts share the original timer and later attempts can time out
    mid-teardown despite each attempt individually being well within the budget.

    We only reset when ``func_only=False`` (the default), because when ``func_only=True``
    pytest-timeout installs the timer in ``pytest_runtest_call``, which ``runtestprotocol()``
    re-invokes per attempt and therefore already gets a fresh budget on every retry.

    No-op when pytest-timeout is not installed or not registered, so callers can invoke this
    unconditionally on every test attempt.
    """
    if _get_pytest_timeout_settings is None or not item.config.pluginmanager.hasplugin("timeout"):
        return
    try:
        settings = _get_pytest_timeout_settings(item)
        if settings.timeout and settings.timeout > 0 and not settings.func_only:
            hooks = item.config.pluginmanager.hook
            hooks.pytest_timeout_cancel_timer(item=item)
            hooks.pytest_timeout_set_timer(item=item, settings=settings)
    except Exception:
        log.debug("Could not reset pytest-timeout timer for test attempt", exc_info=True)


class PytestTimeoutRetryOverride:
    """Override pytest-timeout's ``method="thread"`` timer with a SIGALRM-based one."""

    __test__ = False

    def __init__(self, plugin: t.Any) -> None:
        # ``plugin`` is a TestOptPlugin; we only need its ``manager`` to check whether any
        # retry handlers are registered for this session. Typed as Any to avoid an import
        # cycle with the plugin module (which imports this one lazily).
        self._plugin = plugin
        self._warned_no_sigalrm = False

    @pytest.hookimpl(tryfirst=True)
    def pytest_timeout_set_timer(self, item: pytest.Item, settings: t.Any) -> t.Optional[bool]:
        # Let pytest-timeout do its own thing when retries aren't active, so customers not
        # using ATR/EFD/ATF see no behavior change. ``manager.retry_handlers`` is the single
        # source of truth for "are retries active" (populated in SessionManager.setup_retry_handlers),
        # so this stays correct as new retry handlers are added without touching this code.
        if not self._plugin.manager.retry_handlers:
            return None

        # Only the thread method calls os._exit(); the signal method already raises a catchable failure.
        if settings.method != "thread" or not settings.timeout or settings.timeout <= 0:
            return None

        # We can only install a SIGALRM timer from the main thread on platforms that have SIGALRM.
        # Otherwise fall back to pytest-timeout's thread timer and warn once that retries won't
        # survive a timeout there.
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

        # Delegate the actual timeout behavior to pytest-timeout's own signal-method callback: it
        # honors debugger detection (is_debugging / SUPPRESS_TIMEOUT), dumps other threads' stacks,
        # and raises pytest.fail — exactly what method="signal" already does. This keeps us
        # consistent with pytest-timeout's semantics instead of reimplementing them.
        def handler(signum, frame):  # noqa: ARG001
            __tracebackhide__ = True
            timeout_sigalrm(item, settings)

        def cancel() -> None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, signal.SIG_DFL)

        item.cancel_timeout = cancel
        setattr(item, _OVERRIDE_ATTR, True)
        signal.signal(signal.SIGALRM, handler)
        signal.setitimer(signal.ITIMER_REAL, settings.timeout)
        return True

    @pytest.hookimpl(tryfirst=True)
    def pytest_timeout_cancel_timer(self, item: pytest.Item) -> t.Optional[bool]:
        # Only claim the cancel when we claimed the set; otherwise let pytest-timeout's own
        # cancel_timer (trylast) run so it can clean up the timer it installed.
        if not getattr(item, _OVERRIDE_ATTR, False):
            return None
        try:
            cancel = getattr(item, "cancel_timeout", None)
            if cancel is not None:
                cancel()
        finally:
            setattr(item, _OVERRIDE_ATTR, False)
        return True
