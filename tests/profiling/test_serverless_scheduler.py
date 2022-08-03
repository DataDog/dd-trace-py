# -*- encoding: utf-8 -*-
import mock

from ddtrace.internal import compat
from ddtrace.profiling import exporter
from ddtrace.profiling import recorder
from ddtrace.profiling import serverless_scheduler


@mock.patch("ddtrace.profiling.scheduler.Scheduler.periodic")
def test_periodic(mock_periodic):
    r = recorder.Recorder()
    s = serverless_scheduler.ServerlessScheduler(r, [exporter.NullExporter()])
    s._last_export = compat.time_ns() - 65 * 1e9
    s._profiled_intervals = 65
    s.periodic()
    mock_periodic.assert_called()
