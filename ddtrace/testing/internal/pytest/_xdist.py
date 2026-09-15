"""Datadog Test Optimization hooks for pytest-xdist.

This module is imported lazily, only when pytest-xdist is installed (see
``pytest_configure`` in ``ddtrace.testing.internal.pytest.plugin``), so customers
without xdist pay no import cost.

It owns the main-process side of xdist integration:
- ``pytest_configure_node`` — passes the Datadog session id to each worker.
- ``pytest_testnodedown`` — aggregates ITR skip counts from workers.
- ``pytest_handlecrashitem`` — re-queues a test whose worker crashed (e.g.
  pytest-timeout ``method="thread"`` calling ``os._exit``) so Datadog retries
  (ATR/EFD/ATF) can still reach it on a replacement worker.
"""
from __future__ import annotations

import typing as t

import pytest

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


# User-property keys added to the crash report so the re-queue is visible in pytest's terminal output.
_CRASH_RETRY_REASON = "xdist_worker_crash"
_CRASH_RETRY_NUMBER = "dd_retry_number"


class XdistTestOptPlugin:
    """Main-process hooks for pytest-xdist integration."""

    __test__ = False

    def __init__(self, main_plugin: t.Any) -> None:
        # ``main_plugin`` is a TestOptPlugin; typed as Any to avoid an import cycle with the plugin module
        # (which imports this one lazily).
        self.main_plugin = main_plugin
        # Per-nodeid count of crash re-queues we have triggered, so a test that crashes repeatedly does not
        # re-queue forever. Capped at the retry budget of the active handlers (see pytest_handlecrashitem).
        self._crash_retries: dict[str, int] = {}

    @pytest.hookimpl
    def pytest_configure_node(self, node: t.Any) -> None:
        """Pass the Datadog session id from the main process to xdist workers."""
        node.workerinput["dd_session_id"] = self.main_plugin.session.item_id

    @pytest.hookimpl
    def pytest_testnodedown(self, node: t.Any, error: t.Any) -> None:
        """Aggregate ITR skip counts from a worker node into the main process' session."""
        if not hasattr(node, "workeroutput"):
            return

        if tests_skipped_by_itr := node.workeroutput.get("tests_skipped_by_itr"):
            self.main_plugin.session.tests_skipped_by_itr += tests_skipped_by_itr

    @pytest.hookimpl(tryfirst=True)
    def pytest_handlecrashitem(
        self, crashitem: str, report: pytest.TestReport, sched: t.Any
    ) -> None:
        """Re-queue a test whose xdist worker crashed so a retry feature can reach it.

        When a worker dies mid-test (e.g. pytest-timeout ``method="thread"`` calls ``os._exit(1)``), xdist reports
        the test as failed and replaces the worker, but does not retry the crashed test. Datadog in-process retries
        (ATR/EFD/ATF) live in the worker, so they are lost with it. This hook closes that gap: when a retry feature is
        active, we re-queue the crashed test to a replacement worker via ``sched.mark_test_pending`` so it gets a clean
        re-run with its original timeout method preserved (no SIGALRM override, no signal-cancellation side effects).

        The crash attempt's failure report is relabeled ``rerun`` so it does not count toward pytest's pass/fail tally
        — the re-queue's own result determines the final outcome.

        Per-test re-queue count is capped at the retry budget of the active handlers (``handler.max_retries``). We take
        the maximum across handlers because the main process cannot determine which handler would have applied to the
        crashed test (that depends on per-test properties like ``is_new()`` / ``is_attempt_to_fix()`` that the main
        doesn't have without running the test). xdist's own ``max_worker_restart`` remains the global backstop across
        all tests.

        STAGING / FOLLOW-UP: This is xdist-level re-execution, not in-process ATR. The backend currently sees only the
        re-run's result (the crash attempt's buffered start event is lost to ``os._exit`` and the main process does
        not emit per-test events under xdist). A follow-up will make the main process emit a backend "retry attempt
        (crashed)" event per crash so ATR retry counts/visibility are preserved across worker restarts; that requires
        resolving cross-process attempt-numbering and backend TestRun correlation.
        """
        retry_handlers = self.main_plugin.manager.retry_handlers
        if not retry_handlers:
            # No retry feature active — leave xdist's default behavior (report failure, replace worker, no re-queue).
            return None

        # Cap at the largest retry budget across active handlers (see docstring for why max, not per-test).
        max_requeue = max(handler.max_retries for handler in retry_handlers)

        count = self._crash_retries.get(crashitem, 0)
        if count >= max_requeue:
            return None

        self._crash_retries[crashitem] = count + 1
        sched.mark_test_pending(crashitem)

        # Relabel the crash report as a retry so pytest's terminal summary does not count it as a final failure;
        # the re-queued run will emit its own pass/fail report that determines the outcome.
        report.outcome = "rerun"
        report.user_properties = list(report.user_properties) + [
            (_CRASH_RETRY_REASON, "xdist_worker_crash"),
            (_CRASH_RETRY_NUMBER, count + 1),
        ]
        return None
