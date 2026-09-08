# -*- encoding: utf-8 -*-
import logging
import time
from unittest import mock

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


@mock.patch("ddtrace.profiling.scheduler.ddup")
def test_reported_windows_are_contiguous(mock_ddup):
    """Consecutive flushes must report back-to-back windows: window N+1 starts where window N
    ended, with no gap for the upload round-trip.

    upload() serializes (and thereby resets) the profile near its start and stamps the window
    end there, so the scheduler has to advance _window_start_ns before the blocking POST, not
    after it. The sleep below stands in for a slow agent: with the window advanced after upload()
    returned, the gap between consecutive windows would be the sleep duration.
    """
    upload_latency = 0.1
    windows = []

    def slow_upload(tracer, enable_code_provenance, start_ns=None):
        # Approximates where _upload_via_native captures end_ns: right after serialization, i.e.
        # before the request is actually sent.
        windows.append((start_ns, time.time_ns()))
        time.sleep(upload_latency)

    mock_ddup.upload.side_effect = slow_upload

    s = scheduler.Scheduler()
    s.flush()
    s.flush()

    assert len(windows) == 2
    (_, first_end), (second_start, _) = windows
    # Tolerance well below the simulated latency: the only legitimate skew is the bookkeeping
    # between the scheduler's clock read and upload()'s own.
    assert abs(second_start - first_end) < int(upload_latency * 1e9) / 5


@mock.patch("ddtrace.profiling.scheduler.ddup")
def test_flush_before_start_reports_recent_window(mock_ddup):
    """A flush on a scheduler that was never started must not claim a window beginning at the
    Unix epoch (a ~56-year profile); _window_start_ns is stamped in __init__, not only in
    _start_service.
    """
    before = time.time_ns()
    s = scheduler.Scheduler()
    s.flush()
    after = time.time_ns()

    start_ns = mock_ddup.upload.call_args.kwargs["start_ns"]
    assert before <= start_ns <= after


@mock.patch("ddtrace.profiling.scheduler.ddup")
def test_last_export_stays_zero_until_a_real_export(mock_ddup):
    """_last_export answers "when did a flush last finish", and 0 means "never".

    It is separate from _window_start_ns on purpose: ServerlessScheduler gates on it, and
    tests/contrib/gunicorn/wsgi_mw_app.py probes `_last_export > 0` to decide whether the
    profiler has exported yet. Stamping it at construction time would make that probe true
    before anything was ever uploaded.
    """
    s = scheduler.Scheduler()
    assert s._last_export == 0
    # The reported window, by contrast, must already be usable so a pre-start flush cannot
    # claim to begin at the Unix epoch.
    assert s._window_start_ns > 0

    s.flush()
    assert s._last_export > 0
