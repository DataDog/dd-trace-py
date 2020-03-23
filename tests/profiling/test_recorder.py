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


def test_filter():
    r = recorder.Recorder()

    def filter_all(events):
        return []

    r.add_event_filter(event.Event, filter_all)
    r.push_event(event.Event())
    assert len(r.events[event.Event]) == 0


def test_filter_contents():
    r = recorder.Recorder()

    def filter_one_on_two(events):
        return [event for i, event in enumerate(events) if i % 2 == 0]

    r.add_event_filter(event.Event, filter_one_on_two)
    r.push_events([event.Event(), event.Event(), event.Event()])
    assert len(r.events[event.Event]) == 2


def test_filter_remove():
    r = recorder.Recorder()

    def filter_all(events):
        return []

    r.add_event_filter(event.Event, filter_all)
    r.push_event(event.Event())
    assert len(r.events[event.Event]) == 0

    r.remove_event_filter(event.Event, filter_all)
    r.push_event(event.Event())
    assert len(r.events[event.Event]) == 1
