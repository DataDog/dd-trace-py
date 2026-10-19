import logging
import threading
import typing as t
from unittest import mock

import pytest

from ddtrace.internal import service
from ddtrace.internal import threads
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import scheduler


def test_exporter_failure(caplog: pytest.LogCaptureFixture) -> None:
    s = scheduler.Scheduler()

    with caplog.at_level(logging.DEBUG, logger="ddtrace.profiling.scheduler"):
        with mock.patch.object(ddup, "upload", side_effect=RuntimeError("LOL")):
            s.flush()

    assert ("ddtrace.profiling.scheduler", logging.ERROR, "Failed to upload profile") in caplog.record_tuples


def test_periodic_survives_export_failure() -> None:
    attempts: list[None] = []
    exported_again = threading.Event()

    def flaky_upload(*args: t.Any, **kwargs: t.Any) -> None:
        attempts.append(None)
        if len(attempts) == 1:
            raise RuntimeError("transient export failure")
        if len(attempts) >= 3:
            exported_again.set()

    s = scheduler.Scheduler(interval=0.05)
    with mock.patch.object(ddup, "upload", flaky_upload):
        s.start()
        try:
            assert exported_again.wait(30), "scheduler stopped exporting after %d attempt(s)" % len(attempts)
        finally:
            s.stop()
            s.join()


def test_thread_name() -> None:
    s = scheduler.Scheduler()
    s.start()
    assert s._worker is not None
    assert s._worker.name == "ddtrace.profiling.scheduler:Scheduler"
    s.stop()


def test_before_flush() -> None:
    x: dict[str, bool] = {}

    def call_me() -> None:
        x["OK"] = True

    s = scheduler.Scheduler(before_flush=call_me)
    s.flush()
    assert x["OK"]


def test_before_flush_failure(caplog: pytest.LogCaptureFixture) -> None:
    def call_me() -> None:
        raise Exception("LOL")

    s = scheduler.Scheduler(before_flush=call_me)
    s.flush()
    assert caplog.record_tuples == [
        (("ddtrace.profiling.scheduler", logging.ERROR, "Scheduler before_flush hook failed"))
    ]


@mock.patch("ddtrace.profiling.scheduler.Scheduler.periodic")
@mock.patch("ddtrace.profiling.scheduler.time.time_ns")
def test_serverless_periodic(mock_time_ns: mock.MagicMock, mock_periodic: mock.MagicMock) -> None:
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


def test_stop_with_unstarted_worker() -> None:
    s = scheduler.Scheduler()
    worker = mock.Mock()
    worker.cancel_if_not_started.return_value = True
    s._worker = worker
    s.status = service.ServiceStatus.RUNNING

    s.stop()
    s.join()

    worker.stop.assert_not_called()
    assert s._worker is None
    assert s.status == service.ServiceStatus.STOPPED


def test_stop_cancels_worker_start_deferred_by_fork() -> None:
    s = scheduler.Scheduler()
    with mock.patch.object(threads, "_forking", True):
        s.start()
    worker = s._worker
    assert worker is not None

    try:
        assert worker.is_started() is False

        s.stop()
        s.join()

        assert s._worker is None
        assert s.status == service.ServiceStatus.STOPPED
        # A start left in the queue would bring the upload thread up later with nothing able to stop it.
        assert all(start.__self__ is not worker for start in threads._threads_to_start_after_fork)
    finally:
        threads._threads_to_start_after_fork[:] = [
            start for start in threads._threads_to_start_after_fork if start.__self__ is not worker
        ]


def test_rollback_start_stops_started_worker() -> None:
    s = scheduler.Scheduler()
    s.start()
    worker = s._worker
    assert worker is not None

    s._rollback_start()
    s.join()

    assert s.status == service.ServiceStatus.STOPPED
    assert s._worker is worker


def test_rollback_start_without_worker() -> None:
    s = scheduler.Scheduler()

    s._rollback_start()
    s.join()

    assert s.status == service.ServiceStatus.STOPPED
