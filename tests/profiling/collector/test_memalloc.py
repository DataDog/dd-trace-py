# -*- encoding: utf-8 -*-
import os
import threading

import pytest

try:
    from ddtrace.profiling.collector import _memalloc
except ImportError:
    pytestmark = pytest.mark.skip("_memalloc not available")

from ddtrace.profiling import recorder
from ddtrace.profiling import _nogevent
from ddtrace.profiling import _periodic
from ddtrace.profiling.collector import memalloc


def test_start_twice():
    _memalloc.start(64, 1000)
    with pytest.raises(RuntimeError):
        _memalloc.start(64, 1000)
    _memalloc.stop()


def test_start_wrong_arg():
    with pytest.raises(TypeError, match="function takes exactly 2 arguments \\(1 given\\)"):
        _memalloc.start(2)

    with pytest.raises(ValueError, match="the number of frames must be in range \\[1; 65535\\]"):
        _memalloc.start(429496, 1000)

    with pytest.raises(ValueError, match="the number of frames must be in range \\[1; 65535\\]"):
        _memalloc.start(-1, 1000)

    with pytest.raises(ValueError, match="the number of events must be in range \\[1; 4294967295\\]"):
        _memalloc.start(64, -1)


def test_start_stop():
    _memalloc.start(1, 1)
    _memalloc.stop()


# This is used by tests and must be equal to the line number where object() is called in _allocate_1k 😉
_ALLOC_LINE_NUMBER = 50


def _allocate_1k():
    for _ in range(1000):
        object()


def _pre_allocate_1k():
    return _allocate_1k()


def test_iter_events():
    max_nframe = 32
    _memalloc.start(max_nframe, 10000)
    _allocate_1k()
    events, count, alloc_count = _memalloc.iter_events()
    _memalloc.stop()

    assert count >= 1000
    # Watchout: if we dropped samples the test will likely fail

    object_count = 0
    for (stack, nframe, thread_id), size in events:
        assert 0 < len(stack) <= max_nframe
        assert nframe >= len(stack)
        last_call = stack[0]
        assert thread_id == _nogevent.main_thread_id
        assert size >= 1  # size depends on the object size
        if last_call[2] == "_allocate_1k" and last_call[1] == _ALLOC_LINE_NUMBER:
            assert last_call[0] == __file__
            assert stack[1][0] == __file__
            assert stack[1][1] == 60
            assert stack[1][2] == "test_iter_events"
            object_count += 1

    assert object_count == 1000


def test_iter_events_dropped():
    max_nframe = 32
    _memalloc.start(max_nframe, 100)
    _allocate_1k()
    events, count, alloc_count = _memalloc.iter_events()
    _memalloc.stop()

    assert count == 100
    assert alloc_count >= 1000


def test_iter_events_not_started():
    with pytest.raises(RuntimeError, match="the memalloc module was not started"):
        _memalloc.iter_events()


@pytest.mark.skipif(os.getenv("DD_PROFILE_TEST_GEVENT", False), reason="Test not compatible with gevent")
def test_iter_events_multi_thread():
    max_nframe = 32
    t = threading.Thread(target=_allocate_1k)
    _memalloc.start(max_nframe, 10000)
    _allocate_1k()
    t.start()
    t.join()
    events, count, alloc_count = _memalloc.iter_events()
    _memalloc.stop()

    assert count >= 1000

    # Watchout: if we dropped samples the test will likely fail

    count_object = 0
    count_thread = 0
    for (stack, nframe, thread_id), size in events:
        assert 0 < len(stack) <= max_nframe
        assert nframe >= len(stack)
        last_call = stack[0]
        assert size >= 1  # size depends on the object size
        if last_call[2] == "_allocate_1k" and last_call[1] == _ALLOC_LINE_NUMBER:
            assert last_call[0] == __file__
            if thread_id == _nogevent.main_thread_id:
                count_object += 1
                assert stack[1][0] == __file__
                assert stack[1][1] == 105
                assert stack[1][2] == "test_iter_events_multi_thread"
            elif thread_id == t.ident:
                count_thread += 1
                assert stack[1][0] == threading.__file__
                assert stack[1][1] > 0
                assert stack[1][2] == "run"

    assert count_object == 1000
    assert count_thread == 1000


def test_memory_collector():
    r = recorder.Recorder()
    mc = memalloc.MemoryCollector(r)
    with mc:
        _allocate_1k()
        # Make sure we collect at least once
        mc.periodic()

    count_object = 0
    for event in r.events[memalloc.MemoryAllocSampleEvent]:
        assert 0 < len(event.frames) <= mc.max_nframe
        assert event.nframes >= len(event.frames)
        assert 0 < event.capture_pct <= 100
        last_call = event.frames[0]
        assert event.size > 0
        if last_call[2] == "_allocate_1k" and last_call[1] == _ALLOC_LINE_NUMBER:
            assert event.thread_id == _nogevent.main_thread_id
            assert event.thread_name == "MainThread"
            count_object += 1
            assert event.frames[1][0] == __file__
            assert event.frames[1][1] == 143
            assert event.frames[1][2] == "test_memory_collector"

    assert count_object > 0


@pytest.mark.parametrize(
    "ignore_profiler",
    (True, False),
)
def test_memory_collector_ignore_profiler(ignore_profiler):
    r = recorder.Recorder()
    mc = memalloc.MemoryCollector(r, ignore_profiler=ignore_profiler)
    with mc:
        object()
        # Make sure we collect at least once
        mc.periodic()

    ok = False
    for event in r.events[memalloc.MemoryAllocSampleEvent]:
        for frame in event.frames:
            if ignore_profiler:
                assert frame[0] != _periodic.__file__
            elif frame[0] == _periodic.__file__:
                ok = True
                break

    if not ignore_profiler:
        assert ok
