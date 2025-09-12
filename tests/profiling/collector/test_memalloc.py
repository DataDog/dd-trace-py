# -*- encoding: utf-8 -*-
import gc
import os
import sys
import threading

import pytest

from ddtrace.profiling.collector import memalloc
from ddtrace.profiling.event import DDFrame
from ddtrace.settings.profiling import ProfilingConfig
from ddtrace.settings.profiling import _derive_default_heap_sample_size


try:
    from ddtrace.profiling.collector import _memalloc
except ImportError:
    pytestmark = pytest.mark.skip("_memalloc not available")


def test_start_twice():
    _memalloc.start(64, 512)
    with pytest.raises(RuntimeError):
        _memalloc.start(64, 512)
    _memalloc.stop()


def test_start_wrong_arg():
    with pytest.raises(TypeError, match="function takes exactly 2 arguments \\(1 given\\)"):
        _memalloc.start(2)

    with pytest.raises(ValueError, match="the number of frames must be in range \\[1; 65535\\]"):
        _memalloc.start(429496, 1)

    with pytest.raises(ValueError, match="the number of frames must be in range \\[1; 65535\\]"):
        _memalloc.start(-1, 1)

    with pytest.raises(
        ValueError,
        match="the heap sample size must be in range \\[0; 4294967295\\]",
    ):
        _memalloc.start(64, -1)

    with pytest.raises(
        ValueError,
        match="the heap sample size must be in range \\[0; 4294967295\\]",
    ):
        _memalloc.start(64, 345678909876)


def test_start_stop():
    _memalloc.start(1, 1)
    _memalloc.stop()


def _allocate_1k():
    return [object() for _ in range(1000)]


_ALLOC_LINE_NUMBER = _allocate_1k.__code__.co_firstlineno + 1


def _pre_allocate_1k():
    return _allocate_1k()


def test_iter_events():
    max_nframe = 32
    collector = memalloc.MemoryCollector(max_nframe=max_nframe, heap_sample_size=64)
    with collector:
        _allocate_1k()
        samples = collector.test_snapshot()
    alloc_samples = [s for s in samples if s.alloc_size > 0]

    total_alloc_count = sum(s.count for s in alloc_samples)

    assert total_alloc_count >= 1000
    # Watchout: if we dropped samples the test will likely fail

    object_count = 0
    for sample in alloc_samples:
        stack = sample.frames
        thread_id = sample.thread_id
        size = sample.alloc_size

        assert 0 < len(stack) <= max_nframe
        assert size >= 1  # size depends on the object size
        last_call = stack[0]
        if last_call == DDFrame(
            __file__,
            _ALLOC_LINE_NUMBER,
            "<listcomp>" if sys.version_info < (3, 12) else "_allocate_1k",
            "",
        ):
            assert thread_id == threading.main_thread().ident
            if sys.version_info < (3, 12) and len(stack) > 1:
                assert stack[1] == DDFrame(__file__, _ALLOC_LINE_NUMBER, "_allocate_1k", "")
            object_count += sample.count

    assert object_count >= 1000


def test_iter_events_dropped():
    max_nframe = 32
    collector = memalloc.MemoryCollector(max_nframe=max_nframe, heap_sample_size=64)
    with collector:
        _allocate_1k()
        samples = collector.test_snapshot()
    alloc_samples = [s for s in samples if s.alloc_size > 0]

    total_alloc_count = sum(s.count for s in alloc_samples)

    assert len(alloc_samples) > 0
    assert total_alloc_count >= 1000


def test_iter_events_not_started():
    collector = memalloc.MemoryCollector()
    samples = collector.test_snapshot()
    assert samples == ()


@pytest.mark.skipif(
    os.getenv("DD_PROFILE_TEST_GEVENT", False),
    reason="Test not compatible with gevent",
)
def test_iter_events_multi_thread():
    max_nframe = 32
    t = threading.Thread(target=_allocate_1k)
    collector = memalloc.MemoryCollector(max_nframe=max_nframe, heap_sample_size=64)
    with collector:
        _allocate_1k()
        t.start()
        t.join()

        samples = collector.test_snapshot()
    alloc_samples = [s for s in samples if s.alloc_size > 0]

    total_alloc_count = sum(s.count for s in alloc_samples)

    assert total_alloc_count >= 1000

    # Watchout: if we dropped samples the test will likely fail

    count_object = 0
    count_thread = 0
    for sample in alloc_samples:
        stack = sample.frames
        thread_id = sample.thread_id
        size = sample.alloc_size

        assert 0 < len(stack) <= max_nframe
        assert size >= 1  # size depends on the object size
        last_call = stack[0]
        if last_call == DDFrame(
            __file__,
            _ALLOC_LINE_NUMBER,
            "<listcomp>" if sys.version_info < (3, 12) else "_allocate_1k",
            "",
        ):
            if thread_id == threading.main_thread().ident:
                count_object += sample.count
                if sys.version_info < (3, 12) and len(stack) > 1:
                    assert stack[1] == DDFrame(__file__, _ALLOC_LINE_NUMBER, "_allocate_1k", "")
            elif thread_id == t.ident:
                count_thread += sample.count
                entry = 2 if sys.version_info < (3, 12) else 1
                if entry < len(stack):
                    assert stack[entry].file_name == threading.__file__
                    assert stack[entry].lineno > 0
                    assert stack[entry].function_name == "run"

    assert count_object >= 1000
    assert count_thread >= 1000


def test_heap():
    max_nframe = 32
    collector = memalloc.MemoryCollector(max_nframe=max_nframe, heap_sample_size=1024)
    with collector:
        _test_heap_impl(collector, max_nframe)


def _test_heap_impl(collector, max_nframe):
    x = _allocate_1k()
    samples = collector.test_snapshot()

    alloc_samples = [s for s in samples if s.in_use_size > 0]

    # Check that at least one sample comes from the main thread
    thread_found = False

    for sample in alloc_samples:
        stack = sample.frames
        thread_id = sample.thread_id
        size = sample.in_use_size

        assert 0 < len(stack) <= max_nframe
        assert size > 0

        if thread_id == threading.main_thread().ident:
            thread_found = True
        assert isinstance(thread_id, int)
        if stack[0] == DDFrame(
            __file__,
            _ALLOC_LINE_NUMBER,
            "<listcomp>" if sys.version_info < (3, 12) else "_allocate_1k",
            "",
        ):
            break
    else:
        pytest.fail("No trace of allocation in heap")
    assert thread_found, "Main thread not found"

    y = _pre_allocate_1k()
    samples = collector.test_snapshot()

    alloc_samples = [s for s in samples if s.in_use_size > 0]

    for sample in alloc_samples:
        stack = sample.frames
        thread_id = sample.thread_id
        size = sample.in_use_size

        assert 0 < len(stack) <= max_nframe
        assert size > 0
        assert isinstance(thread_id, int)
        if stack[0] == DDFrame(
            __file__,
            _ALLOC_LINE_NUMBER,
            "<listcomp>" if sys.version_info < (3, 12) else "_allocate_1k",
            "",
        ):
            break
    else:
        pytest.fail("No trace of allocation in heap")

    del x
    gc.collect()

    samples = collector.test_snapshot()

    alloc_samples = [s for s in samples if s.in_use_size > 0]

    for sample in alloc_samples:
        stack = sample.frames
        thread_id = sample.thread_id
        size = sample.in_use_size

        assert 0 < len(stack) <= max_nframe
        assert size > 0
        assert isinstance(thread_id, int)
        entry = 2 if sys.version_info < (3, 12) else 1
        if (
            len(stack) > entry
            and stack[0]
            == DDFrame(
                __file__,
                _ALLOC_LINE_NUMBER,
                "<listcomp>" if sys.version_info < (3, 12) else "_allocate_1k",
                "",
            )
            and stack[entry].function_name == "_test_heap_impl"
        ):
            pytest.fail("Allocated memory still in heap")

    del y
    gc.collect()

    samples = collector.test_snapshot()

    alloc_samples = [s for s in samples if s.in_use_size > 0]

    for sample in alloc_samples:
        stack = sample.frames
        thread_id = sample.thread_id
        size = sample.in_use_size

        assert 0 < len(stack) <= max_nframe
        assert size > 0
        assert isinstance(thread_id, int)
        if (
            len(stack) >= 3
            and stack[0].file_name == __file__
            and stack[0].lineno == _ALLOC_LINE_NUMBER
            and stack[0].function_name in ("<listcomp>", "_allocate_1k")
            and stack[1].file_name == __file__
            and stack[1].lineno == _ALLOC_LINE_NUMBER
            and stack[1].function_name == "_allocate_1k"
            and stack[2].file_name == __file__
            and stack[2].function_name == "_pre_allocate_1k"
        ):
            pytest.fail("Allocated memory still in heap")


def test_heap_stress():
    # This should run for a few seconds, and is enough to spot potential segfaults.
    _memalloc.start(64, 1024)
    try:
        x = []

        for _ in range(20):
            for _ in range(1000):
                x.append(object())
            _memalloc.heap()
            del x[:100]
    finally:
        _memalloc.stop()


@pytest.mark.parametrize("heap_sample_size", (0, 512 * 1024, 1024 * 1024, 2048 * 1024, 4096 * 1024))
def test_memalloc_speed(benchmark, heap_sample_size):
    if heap_sample_size:
        with memalloc.MemoryCollector(heap_sample_size=heap_sample_size):
            benchmark(_allocate_1k)
    else:
        benchmark(_allocate_1k)


@pytest.mark.parametrize(
    "enabled,predicates",
    (
        (
            True,
            (
                lambda v: v >= 512 * 1024,
                lambda v: v > 1,
                lambda v: v > 512,
                lambda v: v == 512 * 1024 * 1024,
            ),
        ),
        (
            False,
            (
                lambda v: v == 0,
                lambda v: v == 0,
                lambda v: v == 0,
                lambda v: v == 0,
            ),
        ),
    ),
)
def test_memalloc_sample_size(enabled, predicates, monkeypatch):
    monkeypatch.setenv("DD_PROFILING_HEAP_ENABLED", str(enabled).lower())
    config = ProfilingConfig()

    assert config.heap.enabled is enabled

    for predicate, default in zip(predicates, (1024 * 1024, 1, 512, 512 * 1024 * 1024)):
        assert predicate(_derive_default_heap_sample_size(config.heap, default))
