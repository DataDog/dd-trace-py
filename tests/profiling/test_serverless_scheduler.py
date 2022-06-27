# -*- encoding: utf-8 -*-

from ddtrace.profiling import exporter
from ddtrace.profiling import recorder
from ddtrace.profiling import serverless_scheduler
from ddtrace.internal import compat

def test_periodic():
    r = recorder.Recorder()
    s = serverless_scheduler.ServerlessScheduler(r, [exporter.NullExporter()])
    s._last_export = compat.time_ns()
    s.periodic()
    assert s._total_profiled_seconds == 1
