# -*- encoding: utf-8 -*-
import gc
import os
import threading

import pytest


try:
    from ddtrace.profiling.collector import _memalloc
except ImportError:
    pytestmark = pytest.mark.skip("_memalloc not available")

from ddtrace.internal import nogevent
from ddtrace.profiling import recorder
from ddtrace.profiling.collector import memalloc


TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)


def test_start_twice():
    _memalloc.start(64, 1000, 512)
    with pytest.raises(RuntimeError):
        _memalloc.start(64, 1000, 512)
    _memalloc.stop()


def test_start_wrong_arg():
    with pytest.raises(TypeError, match="function takes exactly 3 arguments \\(1 given\\)"):
        _memalloc.start(2)

    with pytest.raises(ValueError, match="the number of frames must be in range \\[1; 65535\\]"):
        _memalloc.start(429496, 1000, 1)

    with pytest.raises(ValueError, match="the number of frames must be in range \\[1; 65535\\]"):
        _memalloc.start(-1, 1000, 1)

    with pytest.raises(ValueError, match="the number of events must be in range \\[1; 65535\\]"):
        _memalloc.start(64, -1, 1)

    with pytest.raises(ValueError, match="the heap sample size must be in range \\[0; 4294967295\\]"):
        _memalloc.start(64, 1000, -1)

    with pytest.raises(ValueError, match="the heap sample size must be in range \\[0; 4294967295\\]"):
        _memalloc.start(64, 1000, 345678909876)


def test_start_stop():
    _memalloc.start(1, 1, 1)
    _memalloc.stop()


# This is used by tests and must be equal to the line number where object() is called in _allocate_1k 😉
_ALLOC_LINE_NUMBER = 59


def _allocate_1k():
    return [object() for _ in range(1000)]


def _pre_allocate_1k():
    return _allocate_1k()


def test_iter_events():
    max_nframe = 32
    _memalloc.start(max_nframe, 10000, 512 * 1024)
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
        assert size >= 1  # size depends on the object size
        if last_call[2] == "<listcomp>" and last_call[1] == _ALLOC_LINE_NUMBER:
            assert thread_id == nogevent.main_thread_id
            assert last_call[0] == __file__
            assert stack[1][0] == __file__
            assert stack[1][1] == _ALLOC_LINE_NUMBER
            assert stack[1][2] == "_allocate_1k"
            object_count += 1

    assert object_count >= 1000


def test_iter_events_dropped():
    max_nframe = 32
    _memalloc.start(max_nframe, 100, 512 * 1024)
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
    _memalloc.start(max_nframe, 10000, 512 * 1024)
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
        if last_call[2] == "<listcomp>" and last_call[1] == _ALLOC_LINE_NUMBER:
            assert last_call[0] == __file__
            if thread_id == nogevent.main_thread_id:
                count_object += 1
                assert stack[1][0] == __file__
                assert stack[1][1] == _ALLOC_LINE_NUMBER
                assert stack[1][2] == "_allocate_1k"
            elif thread_id == t.ident:
                count_thread += 1
                assert stack[2][0] == threading.__file__
                assert stack[2][1] > 0
                assert stack[2][2] == "run"

    assert count_object >= 1000
    assert count_thread >= 1000


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
        if last_call[2] == "<listcomp>" and last_call[1] == _ALLOC_LINE_NUMBER:
            assert event.thread_id == nogevent.main_thread_id
            assert event.thread_name == "MainThread"
            count_object += 1
            assert event.frames[2][0] == __file__
            assert event.frames[2][1] == 152
            assert event.frames[2][2] == "test_memory_collector"

    assert count_object > 0


@pytest.mark.skipif(TESTING_GEVENT, reason="Test not compatible with gevent")
@pytest.mark.parametrize(
    "ignore_profiler",
    (True, False),
)
def test_memory_collector_ignore_profiler(
    ignore_profiler,  # type: bool
):
    # type: (...) -> None
    r = recorder.Recorder()
    mc = memalloc.MemoryCollector(r, ignore_profiler=ignore_profiler)
    quit_thread = threading.Event()
    with mc:

        def alloc():
            _allocate_1k()
            quit_thread.wait()

        alloc_thread = threading.Thread(name="allocator", target=alloc)

        if ignore_profiler:
            alloc_thread._ddtrace_profiling_ignore = True

        alloc_thread.start()

        thread_id = alloc_thread.ident

        # Make sure we collect at least once
        mc.periodic()

    # We need to wait for the data collection to happen so it gets the `_ddtrace_profiling_ignore` Thread attribute from
    # the global thread list.
    quit_thread.set()
    alloc_thread.join()

    for event in r.events[memalloc.MemoryAllocSampleEvent]:
        if ignore_profiler:
            assert event.thread_id != thread_id
        elif event.thread_id == thread_id:
            break
    else:
        assert ignore_profiler, "No allocation event was found with the allocator thread"


def test_heap():
    max_nframe = 32
    _memalloc.start(max_nframe, 10, 1024)
    x = _allocate_1k()
    # Check that at least one sample comes from the main thread
    thread_found = False
    for (stack, nframe, thread_id), size in _memalloc.heap():
        assert 0 < len(stack) <= max_nframe
        assert size > 0
        if thread_id == nogevent.main_thread_id:
            thread_found = True
        assert isinstance(thread_id, int)
        if (
            stack[0][0] == __file__
            and stack[0][1] == _ALLOC_LINE_NUMBER
            and stack[0][2] == "<listcomp>"
            and stack[1][0] == __file__
            and stack[1][1] == _ALLOC_LINE_NUMBER
            and stack[1][2] == "_allocate_1k"
            and stack[2][0] == __file__
            and stack[2][2] == "test_heap"
        ):
            break
    else:
        pytest.fail("No trace of allocation in heap")
    assert thread_found, "Main thread not found"
    y = _pre_allocate_1k()
    for (stack, nframe, thread_id), size in _memalloc.heap():
        assert 0 < len(stack) <= max_nframe
        assert size > 0
        assert isinstance(thread_id, int)
        if (
            stack[0][0] == __file__
            and stack[0][1] == _ALLOC_LINE_NUMBER
            and stack[0][2] == "<listcomp>"
            and stack[1][0] == __file__
            and stack[1][1] == _ALLOC_LINE_NUMBER
            and stack[1][2] == "_allocate_1k"
            and stack[2][0] == __file__
            and stack[2][2] == "_pre_allocate_1k"
        ):
            break
    else:
        pytest.fail("No trace of allocation in heap")
    del x
    gc.collect()
    for (stack, nframe, thread_id), size in _memalloc.heap():
        assert 0 < len(stack) <= max_nframe
        assert size > 0
        assert isinstance(thread_id, int)
        if (
            stack[0][0] == __file__
            and stack[0][1] == _ALLOC_LINE_NUMBER
            and stack[0][2] == "<listcomp>"
            and stack[1][0] == __file__
            and stack[1][1] == _ALLOC_LINE_NUMBER
            and stack[1][2] == "_allocate_1k"
            and stack[2][0] == __file__
            and stack[2][2] == "test_heap"
        ):
            pytest.fail("Allocated memory still in heap")
    del y
    gc.collect()
    for (stack, nframe, thread_id), size in _memalloc.heap():
        assert 0 < len(stack) <= max_nframe
        assert size > 0
        assert isinstance(thread_id, int)
        if (
            stack[0][0] == __file__
            and stack[0][1] == _ALLOC_LINE_NUMBER
            and stack[0][2] == "<listcomp>"
            and stack[1][0] == __file__
            and stack[1][1] == _ALLOC_LINE_NUMBER
            and stack[1][2] == "_allocate_1k"
            and stack[2][0] == __file__
            and stack[2][2] == "_pre_allocate_1k"
        ):
            pytest.fail("Allocated memory still in heap")
    _memalloc.stop()


def test_heap_collector():
    heap_sample_size = 1024
    r = recorder.Recorder()
    mc = memalloc.MemoryCollector(r, heap_sample_size=heap_sample_size)
    with mc:
        keep_me = _allocate_1k()
        events = mc.snapshot()

    assert len(events) == 1
    assert len(events[0]) >= 1

    del keep_me

    for event in events[0]:
        assert 0 < len(event.frames) <= mc.max_nframe
        assert event.nframes >= len(event.frames)
        assert event.sample_size == heap_sample_size
        assert len(event.frames) >= 1
        assert event.size > 0
        assert event.thread_id > 0
        assert isinstance(event.thread_name, str)


def test_heap_stress():
    # This should run for a few seconds, and is enough to spot potential segfaults.
    _memalloc.start(64, 64, 1024)

    x = []

    for _ in range(20):
        for _ in range(1000):
            x.append(object())
        _memalloc.heap()
        del x[:100]

    _memalloc.stop()


@pytest.mark.parametrize("heap_sample_size", (0, 512 * 1024, 1024 * 1024, 2048 * 1024, 4096 * 1024))
def test_memalloc_speed(benchmark, heap_sample_size):
    if heap_sample_size:
        r = recorder.Recorder()
        with memalloc.MemoryCollector(r, heap_sample_size=heap_sample_size):
            benchmark(_allocate_1k)
    else:
        benchmark(_allocate_1k)


@pytest.mark.parametrize(
    "enabled,predicates",
    (
        (True, (lambda v: v >= 512 * 1024, lambda v: v > 1, lambda v: v > 512, lambda v: v == 512 * 1024 * 1024)),
        (False, (lambda v: v == 0, lambda v: v == 0, lambda v: v == 0, lambda v: v == 0)),
    ),
)
def test_memalloc_sample_size(enabled, predicates, monkeypatch):
    monkeypatch.setenv("DD_PROFILING_HEAP_ENABLED", str(enabled))
    assert predicates[0](memalloc._get_default_heap_sample_size())
    assert predicates[1](memalloc._get_default_heap_sample_size(1))
    assert predicates[2](memalloc._get_default_heap_sample_size(512))
    assert predicates[3](memalloc._get_default_heap_sample_size(512 * 1024 * 1024))
