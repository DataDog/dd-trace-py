# -*- encoding: utf-8 -*-
from ddtrace.profile import event
from ddtrace.profile import exporter
from ddtrace.profile import recorder
from ddtrace.profile import scheduler


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
    assert s._periodic.name == "ddtrace.profile.scheduler:Scheduler"
    s.stop()
