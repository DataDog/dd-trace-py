# -*- encoding: utf-8 -*-
from ddtrace.profiling import event
from ddtrace.profiling import recorder


def test_reset():
    r = recorder.Recorder()
    r.push_event(event.Event())
    assert len(r.events[event.Event]) == 1
    assert len(r.reset()[event.Event]) == 1
    assert len(r.events[event.Event]) == 0
    assert len(r.reset()[event.Event]) == 0
    r.push_event(event.Event())
    assert len(r.events[event.Event]) == 1
    assert len(r.reset()[event.Event]) == 1


def test_push_events_empty():
    r = recorder.Recorder()
    r.push_events([])
    assert len(r.events[event.Event]) == 0
