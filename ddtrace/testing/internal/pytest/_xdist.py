"""pytest-xdist support for Test Optimization."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import typing as t

from _pytest.reports import TestReport
import pytest

from ddtrace.testing.internal.pytest._protocols import TestOptPluginProtocol
from ddtrace.testing.internal.pytest.xdist import is_xdist_worker_process
from ddtrace.testing.internal.retry_handlers import AutoTestRetriesHandler


_CRASH_RETRY_REASON = "xdist_worker_crash"
_CRASH_RETRY_STATE_WORKER_INPUT = "dd_atr_crash_retry_state"


class CrashRetryBudget(t.NamedTuple):
    retries: int
    retry_limit: int


def read_atr_crash_retry_state(path: Path) -> dict[str, CrashRetryBudget]:
    """Read the controller's crash-only ATR budget ledger."""
    raw_state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_state, dict):
        raise ValueError("ATR crash retry state must be a mapping")

    state: dict[str, CrashRetryBudget] = {}
    for nodeid, raw_budget in raw_state.items():
        if (
            not isinstance(nodeid, str)
            or not isinstance(raw_budget, list)
            or len(raw_budget) != 2
            or not all(isinstance(value, int) and value >= 0 for value in raw_budget)
        ):
            raise ValueError("Invalid ATR crash retry state entry")
        state[nodeid] = CrashRetryBudget(*raw_budget)
    return state


class XdistTestOptPlugin:
    """Handle controller-side xdist coordination."""

    __test__ = False

    def __init__(self, main_plugin: TestOptPluginProtocol) -> None:
        self.main_plugin = main_plugin
        self._start_times: dict[str, float] = {}
        self._crash_retry_counts: dict[str, int] = {}
        self._crash_retry_limits: dict[str, int] = {}
        self._session_counted_nodeids: set[str] = set()
        self._worker_retried_nodeids: set[str] = set()
        self._published_crash_retry_budgets: dict[str, CrashRetryBudget] = {}
        self._crash_retry_state_dir: t.Optional[tempfile.TemporaryDirectory[str]] = None
        self._crash_retry_state_path: t.Optional[Path] = None

        manager = main_plugin.manager
        settings = manager.settings
        atr = AutoTestRetriesHandler(settings)
        self._enabled = (
            settings.auto_test_retries.enabled
            and not settings.early_flake_detection.enabled
            and not settings.test_management.enabled
        )
        self._remaining_session_retries = atr.max_tests_to_retry_per_session
        self._flat_retry_limit = atr.max_retries_per_test
        self._atr_pretty_name = atr.get_pretty_name()

        if self._enabled and not is_xdist_worker_process():
            try:
                self._crash_retry_state_dir = tempfile.TemporaryDirectory(prefix="ddtrace_atr_xdist_")
                self._crash_retry_state_path = Path(self._crash_retry_state_dir.name, "crash_retries.json")
                self._write_crash_retry_state({})
            except OSError:
                self._enabled = False
                self._cleanup_crash_retry_state()

    @pytest.hookimpl
    def pytest_configure_node(self, node: t.Any) -> None:
        """Pass controller-owned session coordination data to a worker."""
        node.workerinput["dd_session_id"] = self.main_plugin.session.item_id
        if self._crash_retry_state_path is not None:
            node.workerinput[_CRASH_RETRY_STATE_WORKER_INPUT] = str(self._crash_retry_state_path)

    @pytest.hookimpl
    def pytest_sessionfinish(self) -> None:
        """Remove the controller-owned crash retry ledger after all workers finish."""
        self._cleanup_crash_retry_state()

    @pytest.hookimpl
    def pytest_testnodedown(self, node: t.Any, error: t.Any) -> None:
        """Add a worker's ITR skip count to the controller session."""
        if not hasattr(node, "workeroutput"):
            return

        if tests_skipped_by_itr := node.workeroutput.get("tests_skipped_by_itr"):
            self.main_plugin.session.tests_skipped_by_itr += tests_skipped_by_itr

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_logstart(self, nodeid: str, location: t.Any) -> None:
        """Record when an attempt starts."""
        if self._enabled:
            self._start_times[nodeid] = time.monotonic()

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_logreport(self, report: TestReport) -> None:
        """Remember when the worker already performed an in-process ATR attempt."""
        if not self._enabled:
            return

        properties = dict(report.user_properties)
        if properties.get("dd_retry_reason") == self._atr_pretty_name:
            self._worker_retried_nodeids.add(report.nodeid)
            # An in-worker ATR retry consumes the session budget just like a crash requeue.
            # Charge it once per test so the controller does not over-allocate retries.
            if report.nodeid not in self._session_counted_nodeids:
                self._session_counted_nodeids.add(report.nodeid)
                self._remaining_session_retries = max(0, self._remaining_session_retries - 1)

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_logfinish(self, nodeid: str, location: t.Any) -> None:
        """Discard per-test controller state after normal completion."""
        self._clear_test_state(nodeid)

    @pytest.hookimpl(tryfirst=True, optionalhook=True)
    def pytest_handlecrashitem(self, crashitem: str, report: TestReport, sched: t.Any) -> None:
        """Let ATR retry a test whose worker exited before reporting its result."""
        # AIDEV-NOTE: EFD and ATF depend on worker-owned applicability and final-status state. Enabling this hook when
        # either feature is active can select a different policy after the crash. ATR-only sessions are safe because
        # ATR applies uniformly and its last result is authoritative. Only consumed ATR counts and limits cross the
        # process boundary through the crash retry ledger; test identity and event lifecycle remain worker-owned.
        if not self._enabled:
            return

        # A worker can emit some ATR attempt reports and then die on a later attempt. Requeueing would restart the
        # in-worker budget from zero, so preserve the global limit by treating that crash as final.
        if crashitem in self._worker_retried_nodeids:
            self._clear_test_state(crashitem)
            return

        if crashitem not in self._session_counted_nodeids and self._remaining_session_retries <= 0:
            self._clear_test_state(crashitem)
            return

        retry_count = self._crash_retry_counts.get(crashitem, 0)
        retry_limit = self._crash_retry_limits.get(crashitem)
        if retry_limit is None:
            retry_limit = self._flat_retry_limit
            self._crash_retry_limits[crashitem] = retry_limit

        if retry_count >= retry_limit:
            self._clear_test_state(crashitem)
            return

        retry_count += 1
        next_state = dict(self._published_crash_retry_budgets)
        next_state[crashitem] = CrashRetryBudget(retry_count, retry_limit)
        try:
            self._write_crash_retry_state(next_state)
        except OSError:
            # The replacement worker cannot safely continue ATR without this handoff. Leave the crash final instead.
            self._enabled = False
            self._clear_test_state(crashitem)
            return

        self._published_crash_retry_budgets = next_state
        if crashitem not in self._session_counted_nodeids:
            self._session_counted_nodeids.add(crashitem)
            self._remaining_session_retries -= 1
        self._crash_retry_counts[crashitem] = retry_count
        sched.mark_test_pending(crashitem)

        previous_outcome = report.outcome
        report.outcome = "rerun"
        report.user_properties = list(report.user_properties) + [
            ("dd_retry_outcome", previous_outcome),
            ("dd_retry_reason", _CRASH_RETRY_REASON),
            ("dd_retry_number", retry_count),
        ]

    def _clear_test_state(self, nodeid: str) -> None:
        """Discard state that is no longer needed for a test."""
        self._start_times.pop(nodeid, None)
        self._crash_retry_counts.pop(nodeid, None)
        self._crash_retry_limits.pop(nodeid, None)
        self._worker_retried_nodeids.discard(nodeid)

    def _write_crash_retry_state(self, state: dict[str, CrashRetryBudget]) -> None:
        """Atomically publish retry budgets before xdist schedules the replacement attempt."""
        if self._crash_retry_state_path is None:
            raise OSError("ATR crash retry state is unavailable")

        temporary_path = self._crash_retry_state_path.with_suffix(".tmp")
        serialized = {nodeid: list(budget) for nodeid, budget in state.items()}
        try:
            temporary_path.write_text(json.dumps(serialized), encoding="utf-8")
            os.replace(temporary_path, self._crash_retry_state_path)
        except OSError:
            temporary_path.unlink(missing_ok=True)
            raise

    def _cleanup_crash_retry_state(self) -> None:
        if self._crash_retry_state_dir is not None:
            self._crash_retry_state_dir.cleanup()
            self._crash_retry_state_dir = None
        self._crash_retry_state_path = None
