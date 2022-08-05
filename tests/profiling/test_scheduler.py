# -*- encoding: utf-8 -*-
import logging

import mock

from ddtrace.internal import compat
from ddtrace.profiling import event
from ddtrace.profiling import exporter
from ddtrace.profiling import recorder
from ddtrace.profiling import scheduler


class _FailExporter(exporter.Exporter):
    @staticmethod
    def export(events):
        raise Exception("BOO!")


def test_exporter_failure():
    r = recorder.Recorder()
    exp = _FailExporter()
    s = scheduler.Scheduler(r, [exp])
    r.push_events([event.Event()] * 10)
    s.flush()


def test_thread_name():
    r = recorder.Recorder()
    exp = exporter.NullExporter()
    s = scheduler.Scheduler(r, [exp])
    s.start()
    assert s._worker.name == "ddtrace.profiling.scheduler:Scheduler"
    s.stop()


def test_before_flush():
    x = {}

    def call_me():
        x["OK"] = True

    r = recorder.Recorder()
    s = scheduler.Scheduler(r, [exporter.NullExporter()], before_flush=call_me)
    r.push_events([event.Event()] * 10)
    s.flush()
    assert x["OK"]


def test_before_flush_failure(caplog):
    def call_me():
        raise Exception("LOL")

    r = recorder.Recorder()
    s = scheduler.Scheduler(r, [exporter.NullExporter()], before_flush=call_me)
    r.push_events([event.Event()] * 10)
    s.flush()
    assert caplog.record_tuples == [
        (("ddtrace.profiling.scheduler", logging.ERROR, "Scheduler before_flush hook failed"))
    ]


@mock.patch("ddtrace.profiling.scheduler.Scheduler.periodic")
def test_serverless_periodic(mock_periodic):
    r = recorder.Recorder()
    s = scheduler.ServerlessScheduler(r, [exporter.NullExporter()])
    # Fake start()
    s._last_export = compat.time_ns()
    s.periodic()
    assert s._profiled_intervals == 1
    mock_periodic.assert_not_called()
    s._last_export = compat.time_ns() - 65
    s._profiled_intervals = 65
    s.periodic()
    assert s._profiled_intervals == 0
    assert s.interval == 1
    mock_periodic.assert_called()
