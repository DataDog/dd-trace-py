# -*- encoding: utf-8 -*-
import logging
import threading
from unittest import mock

import pytest

from ddtrace.internal import service
from ddtrace.internal import threads as internal_threads
from ddtrace.profiling import scheduler


def test_exporter_failure():
    s = scheduler.Scheduler()
    s.flush()


def test_thread_name():
    s = scheduler.Scheduler()
    s.start()
    assert s._worker is not None
    assert s._worker.name == "ddtrace.profiling.scheduler:Scheduler"
    s.stop()


def test_before_flush():
    x = {}

    def call_me():
        x["OK"] = True

    s = scheduler.Scheduler(before_flush=call_me)
    s.flush()
    assert x["OK"]


def test_before_flush_failure(caplog):
    def call_me():
        raise Exception("LOL")

    s = scheduler.Scheduler(before_flush=call_me)
    s.flush()
    assert caplog.record_tuples == [
        (("ddtrace.profiling.scheduler", logging.ERROR, "Scheduler before_flush hook failed"))
    ]


def test_partial_start_rollback_handles_unstarted_worker():
    worker = mock.Mock()
    worker.start.side_effect = OSError("thread creation failed")
    worker.stop.side_effect = RuntimeError("Thread not started")
    worker.join.side_effect = RuntimeError("Periodic thread not started")
    worker._cancel_deferred_start_unlocked.return_value = False
    s = scheduler.Scheduler()

    with (
        mock.patch("ddtrace.internal.periodic.PeriodicThread", return_value=worker),
        pytest.raises(OSError, match="thread creation failed"),
    ):
        s.start()

    s._rollback_start()
    s._rollback_start()
    s.join()

    worker.stop.assert_called_once_with()
    worker.join.assert_called_once_with(0)
    assert s._worker is None
    assert s.status == service.ServiceStatus.STOPPED


def test_partial_start_rollback_retains_started_worker_until_joined():
    worker = mock.Mock()
    worker.stop.side_effect = [KeyboardInterrupt(), RuntimeError("Thread not started"), None]
    worker._cancel_deferred_start_unlocked.return_value = False
    s = scheduler.Scheduler()

    with mock.patch("ddtrace.internal.periodic.PeriodicThread", return_value=worker):
        s.start()

    with pytest.raises(KeyboardInterrupt):
        s._rollback_start()

    s._rollback_start()
    s.join()

    assert s._worker is worker
    assert worker.join.call_args_list == [mock.call(0), mock.call(None)]
    assert s.status == service.ServiceStatus.STOPPED


def test_partial_start_rollback_stops_worker_created_during_cleanup():
    worker = mock.Mock()
    worker.stop.side_effect = [RuntimeError("Thread not started"), None]
    worker._cancel_deferred_start_unlocked.return_value = False
    s = scheduler.Scheduler()

    with mock.patch("ddtrace.internal.periodic.PeriodicThread", return_value=worker):
        s.start()

    s._rollback_start()
    s.join()

    assert worker.stop.call_count == 2
    assert worker.join.call_args_list == [mock.call(0), mock.call(None)]
    assert s._worker is worker
    assert s.status == service.ServiceStatus.STOPPED


def test_partial_start_rollback_serializes_with_fork_restart(monkeypatch):
    fork_lock = threading.Lock()
    monkeypatch.setattr(internal_threads, "_forking_lock", fork_lock)
    worker = mock.Mock()

    def cancel_deferred_start():
        assert fork_lock.locked()
        return False

    def stop():
        assert fork_lock.locked()
        raise RuntimeError("Thread not started")

    def join(timeout):
        assert timeout == 0
        assert fork_lock.locked()
        raise RuntimeError("Periodic thread not started")

    worker._cancel_deferred_start_unlocked.side_effect = cancel_deferred_start
    worker.stop.side_effect = stop
    worker.join.side_effect = join
    s = scheduler.Scheduler()
    s._worker = worker

    s._rollback_start()

    assert s._worker is None
    assert s.status == service.ServiceStatus.STOPPED


def test_partial_start_rollback_clears_failed_deferred_worker(monkeypatch):
    deferred_starts = []
    monkeypatch.setattr(internal_threads, "_forking", True)
    monkeypatch.setattr(internal_threads, "_threads_to_start_after_fork", deferred_starts)
    s = scheduler.Scheduler()

    s.start()
    worker = s._worker
    assert deferred_starts

    class FailedRestart:
        name = "failed restart"

        def start(self):
            raise OSError("thread creation failed")

    deferred_starts[:] = [FailedRestart().start]
    internal_threads._after_fork_child()
    s._rollback_start()
    s.join()

    assert deferred_starts == []
    assert s._worker is None
    assert worker is not None
    assert worker not in internal_threads.periodic_threads.values()
    assert s.status == service.ServiceStatus.STOPPED


def test_partial_start_rollback_cancels_deferred_worker(monkeypatch):
    deferred_starts = []
    deferred_restarts = set()
    monkeypatch.setattr(internal_threads, "_forking", True)
    monkeypatch.setattr(internal_threads, "_threads_to_start_after_fork", deferred_starts)
    monkeypatch.setattr(internal_threads, "_threads_to_restart_after_fork", deferred_restarts)
    s = scheduler.Scheduler()

    s.start()
    worker = s._worker
    assert deferred_starts
    assert worker is not None
    deferred_restarts.add(worker)

    s._rollback_start()
    internal_threads._after_fork_child()

    assert deferred_starts == []
    assert deferred_restarts == set()
    assert s._worker is None
    assert worker not in internal_threads.periodic_threads.values()
    assert s.status == service.ServiceStatus.STOPPED


@mock.patch("ddtrace.profiling.scheduler.Scheduler.periodic")
@mock.patch("ddtrace.profiling.scheduler.time.time_ns")
def test_serverless_periodic(mock_time_ns, mock_periodic):
    s = scheduler.ServerlessScheduler()
    # Fake start()
    s._last_export = 0
    mock_time_ns.return_value = int(s.FORCED_INTERVAL * s.FLUSH_AFTER_INTERVALS * 1e9)

    for _ in range(int(s.FLUSH_AFTER_INTERVALS) - 1):
        s.periodic()

    assert s._profiled_intervals == s.FLUSH_AFTER_INTERVALS - 1
    mock_periodic.assert_not_called()

    s.periodic()

    assert s._profiled_intervals == 0
    assert s.interval == 1
    mock_periodic.assert_called_once_with()
