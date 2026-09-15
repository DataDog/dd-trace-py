"""Datadog Test Optimization hooks for pytest-xdist.

This module is imported lazily, only when pytest-xdist is installed (see pytest_configure in
ddtrace.testing.internal.pytest.plugin), so customers without xdist pay no import cost.

It owns the main-process side of xdist integration:
- pytest_configure_node: passes the Datadog session id to each worker.
- pytest_testnodedown: aggregates ITR skip counts from workers.
- pytest_handlecrashitem: re-queues a test whose worker crashed (e.g. pytest-timeout
  method="thread" calling os._exit) so Datadog retries (ATR/EFD/ATF) can still reach it
  on a replacement worker.

Why pytest_handlecrashitem exists
---------------------------------
When a worker dies mid-test (pytest-timeout's "thread" method calls os._exit(1) on timeout), xdist
reports the test as failed and replaces the worker, but does not retry the crashed test. Datadog
in-process retries (ATR/EFD/ATF) live inside the worker, so they are lost with it — the test is
never retried. This hook closes that gap: when a retry feature is active, we re-queue the crashed
test to a replacement worker via sched.mark_test_pending so it gets a clean re-run, with its
original timeout method preserved (no SIGALRM override, no signal-cancellation side effects —
customers who deliberately chose method="thread" to avoid method="signal" cancellation bugs are
not penalized). The crash attempt's failure report is relabeled "rerun" so it does not count
toward pytest's pass/fail tally; the re-queue's own result determines the outcome.

Crash re-queue vs in-worker ATR retry
-------------------------------------
These are different mechanisms at different levels:

- In-worker ATR retry happens inside one living worker: a test fails normally (e.g. assertion
  error, not a crash) and the worker re-runs it up to its budget, counting attempts and deciding
  the final status. This already works today and is untouched here.
- Crash re-queue happens in the main process across worker deaths: the worker is dead, so
  there is no living worker to do in-process ATR. The main re-runs the test on a new worker,
  which then starts fresh and applies its own in-worker ATR from scratch.

The retry budget lives in the worker, and the worker is dead — so a naive re-queue would let
each replacement worker apply a full budget again, and the global budget would not be honored
across worker deaths.

How the budget is honored here (and why it is exact for the common case)
-----------------------------------------------------------------------
For the dominant crash scenario — a test that always hangs (the motivating case for
method="thread") — each worker dies during attempt 0, before the in-worker ATR loop ever runs.
So zero in-worker retries happen on a crashing attempt: each worker death is exactly one
attempt, and the worker never builds a retry count of its own. That means the main process's
crash count is the retry count: capping re-queues at the configured budget honors the global
budget exactly, with no cross-process state needed.

  attempt 0 (initial)            -> crash        (0 retries)
  re-queue -> attempt 0 (retry 1) -> crash       (1 retry)
  ...
  re-queue -> attempt 0 (retry N) -> crash       (N retries) -> cap reached, stop

The cap is max(handler.max_retries_for_timeout(duration) for handler in self._retry_handlers)
where duration is the wall-clock time the test ran before crashing (measured in the main
process; see below). For ATR — the customer's feature — the budget is a flat
max_retries_per_test (default 5), so the duration is ignored and the budget is honored
exactly. For dynamic ATR, the budget is derived from the duration via the EFD retry buckets
(retries_for_duration). EFD's 5-minute abort threshold (EFD_ABORT_TEST_SECONDS = 300) is
honored: a test that runs longer than 5 minutes gets 0 retries from EFD, matching the
in-process behavior. Dynamic ATR has no such cutoff. The max across handlers is used
because the main process cannot determine which handler would have applied to the crashed
test (that depends on per-test properties like is_new() / is_attempt_to_fix() that the main
does not have without running the test); it also ensures that if ATR is also active, its budget
still applies even when EFD aborts. xdist's own max_worker_restart remains the global backstop
across all tests.

The cap is cached per nodeid from the first crash: DynamicATRRetriesHandler caches the bucket
selected from the initial attempt (via lru_cache), so we do the same here — the first crash's
duration determines the cap for all subsequent re-queues of that test, preventing the cap from
shrinking or growing if later replacement workers crash after different durations.

The handlers are built in __init__ from manager.settings (not manager.retry_handlers)
because the main (controller) process prohibits collection, so
SessionManager.setup_retry_handlers never runs there and manager.retry_handlers stays empty in
the main. We only ever query max_retries_for_timeout (a session-level constant per handler),
never should_apply/should_retry (which need per-test state the main does not have). ATR's
session-level retry limit (max_tests_to_retry_per_session, from
DD_CIVISIBILITY_TOTAL_FLAKY_RETRY_COUNT) is also honored: when it is 0, no ATR handler is
registered, so crashed tests are not re-queued.

How the crash duration is measured (wall-clock from logstart)
-------------------------------------------------------------
The main process receives pytest_runtest_logstart (re-fired by xdist's worker_logstart) before
the worker runs the test, so it fires before any crash. We record time.monotonic() per nodeid
in a pytest_runtest_logstart hookimpl. When pytest_handlecrashitem fires, the delta between
now and the recorded start time approximates how long the test ran before crashing (setup +
call up to the timeout). This is purely main-process state — no files, no reliance on
worker-sent data surviving os._exit (which kills the worker before the xdist channel can
flush in-flight reports). A monotonic clock is used so system clock changes don't affect the
elapsed-time measurement.

The delta includes setup time and xdist's worker-death detection latency (~10-50ms in
practice, negligible for bucket classification). At a bucket boundary the delta may be
slightly over-measured (setup time pushes it into the next-higher bucket), which is the safe
direction (more retries, not fewer). The delta is also slightly under-measured by the
detection latency, which at worst pushes a boundary test into the lower bucket (more
retries) — also safe.

Start times are cleaned up when a test completes normally (via pytest_runtest_logfinish) so
the dict does not grow unbounded in large sessions. They are also popped on crash
(handlecrashitem), so only in-flight tests retain an entry.

Known bounded imprecision (the mixed path)
------------------------------------------
The only case where the global budget is not honored exactly is the mixed path: a worker
does some in-worker ATR retries (the test fails normally a few times), then crashes on a
later attempt. The replacement worker starts fresh and could redo a full in-worker
budget, so the total can exceed the configured budget by at most one worker's budget.
This requires "fails normally several times, then hangs" — uncommon, and bounded. Closing
that gap exactly needs persisting retry state across the process boundary, which is deferred
(see below).

Backend visibility of the crash attempt
-----------------------------------------
The worker's in-progress test run is lost to os._exit (no flush), so the backend would
only see the replacement worker's re-queue result. To fix this, the main process emits
a fail-status TestRun event for each crash (see _emit_crash_test_run), tagged as a retry
with reason "xdist_worker_crash". The backend correlates it with the re-queue's result
via the shared test_session_id / test_suite_id / test name (the main and workers share
the same session id, passed via workerinput at pytest_configure_node). This gives the
backend the full retry history: crash (fail) then re-queue (pass/fail), with correct
retry counts.

The crash TestRun is created on the Test object discovered in the main's session via
logstart (SessionManager.discover_test works on demand, even though the main prohibits
collection). The attempt_number is the crash count (1 for the first crash, 2 for the
second, etc.), matching the re-queue sequence.

ATF/EFD final-status semantics: the crash event's fail status is visible to the backend,
so an ATF test that crashes then passes on the re-queue has both a fail and a pass in
its retry history. The backend can correctly determine the final status (ATF requires
all attempts to pass; EFD marks flaky if pass-after-fail). The replacement worker's
fresh Test object does not see the crash, but the backend's view is complete because
the crash event carries the fail.

Deferred follow-up (not in this PR)
-----------------------------------
1. Exact global budget for the mixed path (cross-process retry-state persistence):
   if a worker does in-worker ATR retries then crashes, the replacement worker starts
   fresh and could redo a full in-worker budget. The crash event only records the
   crash, not the in-worker retries that preceded it, so the backend's retry count may
   under-count in the mixed path.
"""

from __future__ import annotations

import time
import typing as t

import pytest

from ddtrace.internal.logger import get_logger
from ddtrace.testing.internal.constants import TAG_TRUE
from ddtrace.testing.internal.dynamic_atr_retries import DynamicATRRetriesHandler
from ddtrace.testing.internal.dynamic_atr_retries import get_retries_buckets
from ddtrace.testing.internal.dynamic_atr_retries import is_dynamic_retries_enabled
from ddtrace.testing.internal.pytest.utils import nodeid_to_names
from ddtrace.testing.internal.retry_handlers import AttemptToFixHandler
from ddtrace.testing.internal.retry_handlers import AutoTestRetriesHandler
from ddtrace.testing.internal.retry_handlers import EarlyFlakeDetectionHandler
from ddtrace.testing.internal.retry_handlers import RetryHandler
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from ddtrace.testing.internal.test_data import TestStatus
from ddtrace.testing.internal.test_data import TestTag


log = get_logger(__name__)


# User-property keys added to the crash report so the re-queue is visible in pytest's terminal output.
_CRASH_RETRY_REASON_KEY = "dd_retry_reason"
_CRASH_RETRY_NUMBER_KEY = "dd_retry_number"
_CRASH_RETRY_REASON_VALUE = "xdist_worker_crash"


class XdistTestOptPlugin:
    """Main-process hooks for pytest-xdist integration."""

    __test__ = False

    def __init__(self, main_plugin: t.Any) -> None:
        # main_plugin is a TestOptPlugin; typed as Any to avoid an import cycle with the plugin module
        # (which imports this one lazily).
        self.main_plugin = main_plugin
        # Per-nodeid count of crash re-queues we have triggered, so a test that crashes repeatedly
        # does not re-queue forever. Capped at the retry budget of the active handlers.
        self._crash_retries: dict[str, int] = {}
        # Per-nodeid wall-clock start time recorded at pytest_runtest_logstart (before the worker
        # runs the test, so before any crash). Used to measure how long the test ran before crashing,
        # which drives the duration-aware re-queue cap for dynamic ATR/EFD. Cleaned up on normal
        # completion (logfinish) and on crash (handlecrashitem).
        self._start_times_by_nodeid: dict[str, float] = {}
        # Per-nodeid cached re-queue cap, computed from the first crash's duration. Mirrors
        # DynamicATRRetriesHandler's lru_cache behavior: the initial attempt's duration determines
        # the bucket for all subsequent retries of that test.
        self._cached_caps_by_nodeid: dict[str, int] = {}
        # Per-nodeid cached retry reason (from the handler that determined the cap), used for
        # the backend crash-attempt TestRun event so the backend knows which retry feature
        # triggered the re-queue (e.g. "auto_test_retry", "early_flake_detection").
        self._cached_reasons_by_nodeid: dict[str, str] = {}
        # Per-nodeid Test objects discovered in the main's session via logstart. Used to emit
        # backend crash-attempt events (see _emit_crash_test_run).
        self._tests_by_nodeid: dict[str, t.Any] = {}
        # Retry handlers for this session, built from settings. The main (controller) process
        # prohibits collection (DSession.pytest_collection returns True), so
        # pytest_collection_finish never fires in the main and
        # SessionManager.setup_retry_handlers() never runs there — manager.retry_handlers stays
        # empty in the main. Since pytest_handlecrashitem runs in the main, we cannot rely on
        # that list. Instead we build the handler instances here from settings, purely to
        # query max_retries_for_timeout for the cap. We never call should_apply/should_retry
        # (those need per-test state the main doesn't have); we only need the retry budget,
        # which is a session-level constant per handler.
        s = main_plugin.manager.settings
        self._retry_handlers: list[RetryHandler] = []
        if s.auto_test_retries.enabled:
            if is_dynamic_retries_enabled():
                self._retry_handlers.append(DynamicATRRetriesHandler(s, get_retries_buckets()))
            else:
                atr = AutoTestRetriesHandler(s)
                # Honor the session-level retry limit: when max_tests_to_retry_per_session is 0,
                # ATR should not retry any test, so don't register the handler.
                if atr.max_tests_to_retry_per_session > 0:
                    self._retry_handlers.append(atr)
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
        """Record the monotonic start time and discover the test in the main's session.

        xdist re-fires pytest_runtest_logstart in the main process (via worker_logstart) before
        the worker executes the test. For a test that crashes during the call (e.g. pytest-timeout
        method="thread" calling os._exit), this fires before the crash, so we have a reliable
        start time to measure the crash duration from. We also discover the test in the main's
        session so we can emit a backend crash-attempt event later (see _emit_crash_test_run).
        """
        self._start_times_by_nodeid[nodeid] = time.monotonic()
        # Discover the test in the main's session so we can make_test_run on it if the worker crashes.
        # The main prohibits collection, but SessionManager.discover_test works on demand.
        if nodeid not in self._tests_by_nodeid:
            module_name, suite_name, test_name = nodeid_to_names(nodeid)
            test_ref = TestRef(SuiteRef(ModuleRef(module_name), suite_name), test_name)
            try:
                _, _, test = self.main_plugin.manager.discover_test(
                    test_ref,
                    on_new_module=lambda m: None,
                    on_new_suite=lambda s: None,
                    on_new_test=lambda t: None,
                )
                self._tests_by_nodeid[nodeid] = test
            except Exception:
                log.debug("Could not discover test %s in main process for crash event", nodeid, exc_info=True)

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_logfinish(self, nodeid: str, location: t.Any) -> None:
        """Clean up the start time for a test that completed normally (no crash).

        Without this, _start_times_by_nodeid would grow with the full test count in large sessions.
        The entry is only needed between logstart and either logfinish (normal completion) or
        handlecrashitem (crash); removing it here keeps the dict bounded to in-flight tests.
        """
        self._start_times_by_nodeid.pop(nodeid, None)

    @pytest.hookimpl(tryfirst=True)
    def pytest_handlecrashitem(self, crashitem: str, report: pytest.TestReport, sched: t.Any) -> None:
        """Re-queue a test whose xdist worker crashed so a retry feature can reach it.

        See the module docstring for the full rationale. In short: when a retry feature is
        active, re-queue the crashed test to a replacement worker via sched.mark_test_pending
        and relabel the crash report "rerun" so it does not count as a final failure. No-op
        when no retry feature is active, leaving xdist's default behavior (report failure,
        replace worker, no re-queue) untouched.
        """
        retry_handlers = self._retry_handlers
        if not retry_handlers:
            return None

        # Measure how long the test ran before crashing (setup + call up to the timeout).
        # Use a monotonic clock so system clock changes don't affect the measurement.
        start_time = self._start_times_by_nodeid.pop(crashitem, None)
        duration = (time.monotonic() - start_time) if start_time is not None else 0.0

        # Cache the cap and retry reason from the first crash's duration, mirroring
        # DynamicATRRetriesHandler's lru_cache: the initial attempt's duration determines
        # the bucket for all subsequent re-queues of that test. The retry reason comes
        # from the handler that gave the max budget (the one that would retry the test).
        max_requeue = self._cached_caps_by_nodeid.get(crashitem)
        if max_requeue is None:
            winning_handler = max(retry_handlers, key=lambda h: h.max_retries_for_timeout(duration))
            max_requeue = winning_handler.max_retries_for_timeout(duration)
            self._cached_caps_by_nodeid[crashitem] = max_requeue
            self._cached_reasons_by_nodeid[crashitem] = winning_handler.retry_reason

        count = self._crash_retries.get(crashitem, 0)
        if count >= max_requeue:
            return None

        self._crash_retries[crashitem] = count + 1
        sched.mark_test_pending(crashitem)

        # Emit a backend event for the crashed attempt so the backend sees the full retry
        # history (crash = fail, then the re-queue's result). Without this, the backend would
        # only see the re-queue's result with no indication the test was retried.
        self._emit_crash_test_run(
            crashitem, count + 1, duration, self._cached_reasons_by_nodeid.get(crashitem, "xdist_worker_crash")
        )

        # Relabel the crash report as a retry so pytest's terminal summary does not count it
        # as a final failure; the re-queued run will emit its own pass/fail report that
        # determines the outcome.
        report.outcome = "rerun"
        report.user_properties = list(report.user_properties) + [
            (_CRASH_RETRY_REASON_KEY, _CRASH_RETRY_REASON_VALUE),
            (_CRASH_RETRY_NUMBER_KEY, count + 1),
        ]
        return None

    def _emit_crash_test_run(self, nodeid: str, attempt_number: int, duration: float, retry_reason: str) -> None:
        """Emit a backend TestRun event for a crashed test attempt.

        The worker's in-progress test run is lost to os._exit (no flush), so the backend would
        only see the replacement worker's re-queue result. This emits a fail-status TestRun
        from the main process, tagged as a retry with the active handler's retry reason (e.g.
        "auto_test_retry", "early_flake_detection", "attempt_to_fix"), so the backend sees the
        full retry history: crash (fail) then re-queue (pass/fail), and knows which retry feature
        triggered the re-queue. The TestRun is correlated with the re-queue via the shared
        test_session_id / test_suite_id / test name (the main and workers share the same
        session id, passed via workerinput at pytest_configure_node).
        """
        test = self._tests_by_nodeid.get(nodeid)
        if test is None:
            log.debug("Cannot emit crash test run for %s: test not discovered", nodeid)
            return

        test_run = test.make_test_run()
        test_run.set_status(TestStatus.FAIL)
        test_run.set_tags(
            {
                TestTag.IS_RETRY: TAG_TRUE,
                TestTag.RETRY_REASON: retry_reason,
            }
        )
        # Set a minimal duration so the backend has timing context. Use the measured crash
        # duration (setup + call up to the timeout) in nanoseconds.
        test_run.start_ns = int((time.monotonic() - duration) * 1e9) if duration > 0 else int(time.monotonic() * 1e9)
        test_run.finish()
        self.main_plugin.manager.writer.put_item(test_run)
