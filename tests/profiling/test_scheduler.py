import logging
import threading
import typing as t
from unittest import mock

import pytest

from ddtrace.internal import service
from ddtrace.internal.datadog.profiling import code_provenance
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


def test_start_prewarms_code_provenance() -> None:
    s: scheduler.Scheduler = scheduler.Scheduler()
    s._enable_code_provenance = True

    with mock.patch.object(code_provenance, "get_code_provenance_file") as prewarm:
        s.start()
        try:
            # Resolved here, so the shutdown flush finds a memoized result.
            prewarm.assert_called_once_with()
        finally:
            s.stop()
            s.join()


def test_start_skips_prewarm_when_code_provenance_disabled() -> None:
    s: scheduler.Scheduler = scheduler.Scheduler()
    s._enable_code_provenance = False

    with mock.patch.object(code_provenance, "get_code_provenance_file") as prewarm:
        s.start()
        try:
            prewarm.assert_not_called()
        finally:
            s.stop()
            s.join()


def test_start_survives_prewarm_failure() -> None:
    s: scheduler.Scheduler = scheduler.Scheduler()
    s._enable_code_provenance = True

    with mock.patch.object(code_provenance, "get_code_provenance_file", side_effect=RuntimeError("LOL")):
        s.start()
        try:
            assert s.status == service.ServiceStatus.RUNNING
        finally:
            s.stop()
            s.join()


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
