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

The cap is ``max(handler.max_retries_for_timeout(duration) for handler in self._retry_handlers)``
where ``duration`` is the wall-clock time the test ran before crashing (measured in the main process;
see below). For ATR - the customer's feature - ``max_retries`` is ``max_retries_per_test`` (default 5),
so the budget is honored exactly. For dynamic ATR, the budget is derived from the duration via the
EFD retry buckets (``retries_for_duration``), so a 5-minute-timeout test gets 2 retries (not the
flat 5). The ``max`` across handlers is used because the main process cannot determine which handler
would have applied to the crashed test (that depends on per-test properties like ``is_new()`` /
``is_attempt_to_fix()`` that the main does not have without running the test). xdist's own
``max_worker_restart`` remains the global backstop across all tests.

The handlers are built in ``__init__`` from ``manager.settings`` (not ``manager.retry_handlers``)
because the main (controller) process prohibits collection, so ``SessionManager.setup_retry_handlers``
never runs there and ``manager.retry_handlers`` stays empty in the main. We only ever query
``max_retries`` / ``max_retries_for_timeout`` (session-level constants), never
``should_apply``/``should_retry`` (which need per-test state the main does not have).

How the crash duration is measured (wall-clock from logstart)
-------------------------------------------------------------
The main process receives ``pytest_runtest_logstart`` (re-fired by xdist's ``worker_logstart``)
*before* the worker runs the test, so it fires before any crash. We record ``time.time()`` per nodeid
in a ``pytest_runtest_logstart`` hookimpl. When ``pytest_handlecrashitem`` fires, the delta between
now and the recorded start time approximates how long the test ran before crashing (setup + call up
to the timeout). This is purely main-process state - no files, no reliance on worker-sent data
surviving ``os._exit`` (which kills the worker before the xdist channel can flush in-flight
reports).

The delta includes setup time and xdist's worker-death detection latency (~10-50ms in practice,
negligible for bucket classification). At a bucket boundary the delta may be slightly
over-measured (setup time pushes it into the next-higher bucket), which is the *safe* direction
(more retries, not fewer). The delta is also slightly *under*-measured by the detection latency,
which at worst pushes a boundary test into the *lower* bucket (more retries) - also safe.

Known bounded imprecision (the mixed path)
------------------------------------------
The only case where the global budget is not honored exactly is the *mixed* path: a worker does
some in-worker ATR retries (the test fails normally a few times), *then* crashes on a later attempt.
The replacement worker starts fresh and could redo a full in-worker budget, so the total can exceed
the configured budget by at most one worker's budget. This requires "fails normally several times,
then hangs" - uncommon, and bounded. Closing that gap exactly needs persisting retry state across
the process boundary, which is deferred (see below).

Deferred follow-ups (not in this PR)
------------------------------------
1. **Backend visibility of the crash attempt.** Today the backend sees only the re-run's result (the
   crash attempt's buffered start event is lost to ``os._exit`` and the main process does not emit
   per-test events under xdist). A follow-up will make the main process emit a backend "retry attempt
   (crashed)" event per crash so ATR retry counts/visibility are preserved across worker restarts.
2. **Exact global budget for the mixed path** (cross-process retry-state persistence).
"""

from __future__ import annotations

import time
import typing as t

import pytest

from ddtrace.internal.logger import get_logger
from ddtrace.testing.internal.dynamic_atr_retries import DynamicATRRetriesHandler
from ddtrace.testing.internal.dynamic_atr_retries import get_retries_buckets
from ddtrace.testing.internal.dynamic_atr_retries import is_dynamic_retries_enabled
from ddtrace.testing.internal.retry_handlers import AttemptToFixHandler
from ddtrace.testing.internal.retry_handlers import AutoTestRetriesHandler
from ddtrace.testing.internal.retry_handlers import EarlyFlakeDetectionHandler
from ddtrace.testing.internal.retry_handlers import RetryHandler


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
        # Per-nodeid wall-clock start time recorded at pytest_runtest_logstart (before the worker runs the
        # test, so before any crash). Used to measure how long the test ran before crashing, which drives
        # the duration-aware re-queue cap for dynamic ATR/EFD. See module docstring.
        self._start_times_by_nodeid: dict[str, float] = {}
        # Retry handlers for this session, built from settings. The main (controller) process prohibits
        # collection (DSession.pytest_collection returns True), so pytest_collection_finish never fires in
        # the main and SessionManager.setup_retry_handlers() never runs there — manager.retry_handlers stays
        # empty in the main. Since pytest_handlecrashitem runs in the main, we cannot rely on that list.
        # Instead we build the handler instances here from settings, purely to query max_retries for the cap.
        # We never call should_apply/should_retry (those need per-test state the main doesn't have); we only
        # need the retry budget, which is a session-level constant per handler.
        s = main_plugin.manager.settings
        self._retry_handlers: list[RetryHandler] = []
        if s.auto_test_retries.enabled:
            if is_dynamic_retries_enabled():
                self._retry_handlers.append(DynamicATRRetriesHandler(s, get_retries_buckets()))
            else:
                self._retry_handlers.append(AutoTestRetriesHandler(s))
        if s.early_flake_detection.enabled:
            self._retry_handlers.append(EarlyFlakeDetectionHandler(s))
        if s.test_management.enabled:
            self._retry_handlers.append(AttemptToFixHandler(s))

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
    def pytest_runtest_logstart(self, nodeid: str, location: t.Any) -> None:
        """Record the wall-clock start time for each test before the worker runs it.

        xdist re-fires ``pytest_runtest_logstart`` in the main process (via ``worker_logstart``) *before*
        the worker executes the test. For a test that crashes during the call (e.g. pytest-timeout
        ``method="thread"`` calling ``os._exit``), this fires before the crash, so we have a reliable
        start time to measure the crash duration from. See module docstring for why this is main-process
        state (no reliance on worker-sent data surviving ``os._exit``).
        """
        self._start_times_by_nodeid[nodeid] = time.time()

    @pytest.hookimpl(tryfirst=True)
    def pytest_handlecrashitem(self, crashitem: str, report: pytest.TestReport, sched: t.Any) -> None:
        """Re-queue a test whose xdist worker crashed so a retry feature can reach it.

        See the module docstring for the full rationale (crash re-queue vs in-worker ATR, how the
        budget is honored, and how the crash duration is measured). In short: when a retry feature is
        active, re-queue the crashed test to a replacement worker via ``sched.mark_test_pending`` and
        relabel the crash report ``rerun`` so it does not count as a final failure. No-op when no retry
        feature is active, leaving xdist's default behavior (report failure, replace worker, no
        re-queue) untouched.
        """
        retry_handlers = self._retry_handlers
        if not retry_handlers:
            return None

        # Measure how long the test ran before crashing (setup + call up to the timeout). This drives the
        # duration-aware re-queue cap for dynamic ATR/EFD. If we have no start time (e.g. the test crashed
        # before logstart, which shouldn't happen), fall back to the static max_retries.
        start_time = self._start_times_by_nodeid.pop(crashitem, None)
        if start_time is not None:
            duration = time.time() - start_time
            max_requeue = max(handler.max_retries_for_timeout(duration) for handler in retry_handlers)
        else:
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
