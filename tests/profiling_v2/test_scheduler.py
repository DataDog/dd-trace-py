# -*- encoding: utf-8 -*-
import logging
import time

import mock

from ddtrace.profiling import scheduler


def test_thread_name():
    s = scheduler.Scheduler()
    s.start()
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
def test_serverless_periodic(mock_periodic):
    s = scheduler.ServerlessScheduler()
    # Fake start()
    s._last_export = time.time_ns()
    s.periodic()
    assert s._profiled_intervals == 1
    mock_periodic.assert_not_called()
    s._last_export = time.time_ns() - 65
    s._profiled_intervals = 65
    s.periodic()
    assert s._profiled_intervals == 0
    assert s.interval == 1
    mock_periodic.assert_called()
