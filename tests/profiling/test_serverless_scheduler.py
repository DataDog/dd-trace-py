# -*- encoding: utf-8 -*-

from ddtrace.profiling import exporter
from ddtrace.profiling import recorder
from ddtrace.profiling import serverless_scheduler


def test_periodic():
    r = recorder.Recorder()
    s = serverless_scheduler.ServerlessScheduler(r, [exporter.NullExporter()])
    s.periodic()
    assert s._total_profiled_seconds == 1
