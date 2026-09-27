import logging
import threading
from unittest import mock

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import scheduler


def test_exporter_failure(caplog):
    """A failing export must not propagate out of flush()."""
    s = scheduler.Scheduler()

    # at_level(DEBUG) also switches off the rate limiter that ddtrace's get_logger
    # installs, which would otherwise drop this record when another test logged from
    # the same line less than a minute earlier.
    with caplog.at_level(logging.DEBUG, logger="ddtrace.profiling.scheduler"):
        with mock.patch.object(ddup, "upload", side_effect=RuntimeError("LOL")):
            s.flush()

    assert ("ddtrace.profiling.scheduler", logging.ERROR, "Failed to upload profile") in caplog.record_tuples


def test_periodic_survives_export_failure():
    """A failed export must not stop the scheduler from exporting again.

    periodic() runs on a PeriodicThread whose loop breaks for good once the target
    raises, so an export failure that escaped flush() used to end every later upload
    for the life of the process while the collectors kept on sampling.
    """
    attempts = []
    exported_again = threading.Event()

    def flaky_upload(*args, **kwargs):
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
