"""Datadog Test Optimization hooks for pytest-xdist.

This module is imported lazily, only when pytest-xdist is installed (see ``pytest_configure`` in
``ddtrace.testing.internal.pytest.plugin``), so customers without xdist pay no import cost.

It owns the main-process side of xdist integration:
- ``pytest_configure_node`` - passes the Datadog session id to each worker.
- ``pytest_testnodedown`` - aggregates ITR skip counts from workers.
- ``pytest_handlecrashitem`` - re-queues a test whose worker crashed (e.g. pytest-timeout
  ``method="thread"`` calling ``os._exit``) so Datadog retries (ATR/EFD/ATF) can still reach it
  on a replacement worker.

Why pytest_handlecrashitem exists
---------------------------------
When a worker dies mid-test (pytest-timeout's ``method="thread"`` calls ``os._exit(1)`` on timeout),
xdist reports the test as failed and replaces the worker, but does *not* retry the crashed test.
Datadog in-process retries (ATR/EFD/ATF) live *inside the worker*, so they are lost with it - the
test is never retried. This hook closes that gap: when a retry feature is active, we re-queue the
crashed test to a replacement worker via ``sched.mark_test_pending`` so it gets a clean re-run, with
its original timeout method preserved (no SIGALRM override, no signal-cancellation side effects -
customers who deliberately chose ``method="thread"`` to avoid ``method="signal"`` cancellation bugs
are not penalized). The crash attempt's failure report is relabeled ``rerun`` so it does not count
toward pytest's pass/fail tally; the re-queue's own result determines the outcome.

Crash re-queue vs in-worker ATR retry
-------------------------------------
These are different mechanisms at different levels:

- **In-worker ATR retry** happens inside one *living* worker: a test fails normally (e.g. assertion
  error, not a crash) and the worker re-runs it up to its budget, counting attempts and deciding the
  final status. This already works today and is untouched here.
- **Crash re-queue** happens in the *main process* across worker deaths: the worker is dead, so
  there is no living worker to do in-process ATR. The main re-runs the test on a *new* worker, which
  then starts fresh and applies its own in-worker ATR from scratch.

The retry budget lives in the worker, and the worker is dead - so a naive re-queue would let each
replacement worker apply a full budget again, and the global budget would not be honored across
worker deaths.

How the budget is honored here (and why it is exact for the common case)
-----------------------------------------------------------------------
For the dominant crash scenario - a test that *always* hangs (the motivating case for
``method="thread"``) - each worker dies during attempt 0, *before* the in-worker ATR loop ever runs.
So **zero in-worker retries happen on a crashing attempt**: each worker death is exactly one
attempt, and the worker never builds a retry count of its own. That means the main process's crash
count *is* the retry count: capping re-queues at the configured budget honors the global budget
exactly, with no cross-process state needed.

  attempt 0 (initial)            -> crash        (0 retries)
  re-queue -> attempt 0 (retry 1) -> crash       (1 retry)
  ...
  re-queue -> attempt 0 (retry N) -> crash       (N retries) -> cap reached, stop

The cap is ``max(handler.max_retries for handler in retry_handlers)`` (the ``max_retries`` property
on ``RetryHandler``). For ATR - the customer's feature - ``max_retries`` is ``max_retries_per_test``
(default 5), so the budget is honored exactly. The ``max`` across handlers is used because the main
process cannot determine which handler would have applied to the crashed test (that depends on
per-test properties like ``is_new()`` / ``is_attempt_to_fix()`` that the main does not have without
running the test). xdist's own ``max_worker_restart`` remains the global backstop across all tests.

Known bounded imprecision (the mixed path)
------------------------------------------
The only case where the global budget is not honored exactly is the *mixed* path: a worker does
some in-worker ATR retries (the test fails normally a few times), *then* crashes on a later attempt.
The replacement worker starts fresh and could redo a full in-worker budget, so the total can exceed
the configured budget by at most one worker's budget. This requires "fails normally several times,
then hangs" - uncommon, and bounded. Closing that gap exactly needs persisting retry state across
the process boundary (a side-channel file with the cumulative offset, or moving all retry counting
to the main), which is deferred (see below) because it needs cross-process validation we cannot do
in this PR.

Deferred follow-ups (not in this PR)
------------------------------------
1. **Backend visibility of the crash attempt.** Today the backend sees only the re-run's result (the
   crash attempt's buffered start event is lost to ``os._exit`` and the main process does not emit
   per-test events under xdist). A follow-up will make the main process emit a backend "retry attempt
   (crashed)" event per crash so ATR retry counts/visibility are preserved across worker restarts;
   that requires resolving cross-process attempt-numbering and backend ``TestRun`` correlation.
2. **Exact global budget for the mixed path** (side-channel offset or main-owned retry counting).
3. **EFD dynamic budget from the timeout.** EFD's budget scales with test duration; in the crash
   case the test hangs for ~the timeout, so the exact EFD budget would be
   ``retries_for_duration(timeout)``. Currently EFD's ``max_retries`` returns its largest bucket as
   a conservative ceiling; the timeout-peek refinement is a follow-up (it only matters for
   EFD+crash, a narrower case than ATR+crash).
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
    def pytest_handlecrashitem(self, crashitem: str, report: pytest.TestReport, sched: t.Any) -> None:
        """Re-queue a test whose xdist worker crashed so a retry feature can reach it.

        See the module docstring for the full rationale (crash re-queue vs in-worker ATR, how the
        budget is honored, and the known bounded imprecision in the mixed path). In short: when a
        retry feature is active, re-queue the crashed test to a replacement worker via
        ``sched.mark_test_pending`` and relabel the crash report ``rerun`` so it does not count as a
        final failure. No-op when no retry feature is active, leaving xdist's default behavior
        (report failure, replace worker, no re-queue) untouched.
        """
        retry_handlers = self.main_plugin.manager.retry_handlers
        if not retry_handlers:
            return None

        # Cap at the largest retry budget across active handlers. See module docstring: for the common
        # crash-always case this honors the global budget exactly; the mixed path can over-run by at
        # most one worker's budget (deferred follow-up).
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
