# -*- encoding: utf-8 -*-
import os
import sys

import pytest

from ddtrace.profiling import event
from ddtrace.profiling import recorder
from ddtrace.profiling.collector import stack_event
from tests.utils import call_program


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
    r = recorder.Recorder(
        default_max_events=12,
        max_events={
            stack_event.StackSampleEvent: 24,
        },
    )
    assert r.events[stack_event.StackExceptionSampleEvent].maxlen == 12
    assert r.events[stack_event.StackSampleEvent].maxlen == 24


@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
def test_fork():
    stdout, stderr, exitcode, pid = call_program("python", os.path.join(os.path.dirname(__file__), "recorder_fork.py"))
    assert exitcode == 0, (stdout, stderr)
