import os

try:
    import tracemalloc
except ImportError:
    tracemalloc = None

import pytest

import ddtrace.profile
from ddtrace.profile import recorder
from ddtrace.profile.collector import memory
from ddtrace.profile.collector import stack

from . import test_collector


def _alloc():
    return [object() for _ in range(16384)]


@pytest.mark.skipif(tracemalloc is None, reason="tracemalloc is unavailable")
def test_collect():
    test_collector._test_collector_collect(
        memory.MemoryCollector, memory.MemorySampleEvent, fn=_alloc, capture_pct=100, ignore_profiler=False
    )


@pytest.mark.skipif(tracemalloc is None, reason="tracemalloc is unavailable")
def test_status():
    test_collector._test_collector_status(memory.MemoryCollector)


@pytest.mark.skipif(tracemalloc is None, reason="tracemalloc is unavailable")
def test_repr():
    test_collector._test_repr(
        memory.MemoryCollector,
        "MemoryCollector(recorder=Recorder(max_size=49152), capture_pct=5.0, nframes=64, ignore_profiler=True)",
    )


def test_max_capture():
    r = recorder.Recorder()
    with pytest.raises(ValueError):
        memory.MemoryCollector(r, capture_pct=-1)


def test_max_capture_over():
    r = recorder.Recorder()
    with pytest.raises(ValueError):
        memory.MemoryCollector(r, capture_pct=200)


def _test_memory_ignore(ignore):
    r = recorder.Recorder()
    # Start a stack collector so it allocates memory
    with stack.StackCollector(r):
        r = recorder.Recorder()
        c = memory.MemoryCollector(r, ignore_profiler=ignore, capture_pct=100)
        while not r.events[memory.MemorySampleEvent]:
            c.collect()
            _ = _alloc()
    events = r.events[memory.MemorySampleEvent]
    files = {frame.filename for event in events for trace in event.snapshot.traces for frame in trace.traceback}
    return files


@pytest.mark.skipif(tracemalloc is None, reason="tracemalloc is unavailable")
def test_memory_ignore_true():
    files = _test_memory_ignore(True)
    ddprofile_dir = os.path.dirname(ddtrace.profile.__file__)
    assert all(map(lambda f: not f.startswith(ddprofile_dir), files)), files


@pytest.mark.skipif(tracemalloc is None, reason="tracemalloc is unavailable")
def test_memory_ignore_false():
    files = _test_memory_ignore(False)
    ddprofile_dir = os.path.dirname(ddtrace.profile.__file__)
    assert any(map(lambda f: f.startswith(ddprofile_dir), files)), files


@pytest.mark.benchmark(group="memory-capture", min_time=1)
@pytest.mark.parametrize(
    "pct", range(10, 101, 10),
)
@pytest.mark.skipif(tracemalloc is None, reason="tracemalloc is unavailable")
def test_memory_alloc_speed_patched(benchmark, pct):
    r = recorder.Recorder()
    with memory.MemoryCollector(r, capture_pct=pct):
        benchmark(_alloc)


@pytest.mark.benchmark(group="memory-capture",)
def test_memory_alloc_speed(benchmark):
    benchmark(_alloc)
