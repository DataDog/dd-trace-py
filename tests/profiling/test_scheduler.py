# -*- encoding: utf-8 -*-
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
