# -*- encoding: utf-8 -*-
from ddtrace.profiling import event
from ddtrace.profiling import recorder
from ddtrace.profiling.collector import stack

import pytest


def test_defaultdictkey():
    d = recorder._defaultdictkey(lambda k: [str(k) + "k"])
    assert isinstance(d["abc"], list)
    assert 2 not in d
    d[1].append("hello")
    assert d[1] == ["1k", "hello"]
    d[1].append("bar")
    assert d[1] == ["1k", "hello", "bar"]


def test_defaultdictkey_no_factory():
    d = recorder._defaultdictkey()
    with pytest.raises(KeyError):
        d[1]


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


def test_limit():
    r = recorder.Recorder(default_max_events=12, max_events={stack.StackSampleEvent: 24,})
    assert r.events[stack.StackExceptionSampleEvent].maxlen == 12
    assert r.events[stack.StackSampleEvent].maxlen == 24
