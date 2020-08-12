import os
import time

try:
    import tracemalloc
except ImportError:
    tracemalloc = None

import pytest

import ddtrace.profiling
from ddtrace.profiling import recorder
from ddtrace.profiling.collector import memory
from ddtrace.profiling.collector import stack

from . import test_collector


def _alloc():
    return [object() for _ in range(16384)]


@pytest.mark.skipif(tracemalloc is None, reason="tracemalloc is unavailable")
def test_collect():
    test_collector._test_collector_collect(
        memory.MemoryCollector, memory.MemorySampleEvent, fn=_alloc, capture_pct=100, ignore_profiler=False
    )


@pytest.mark.skipif(tracemalloc is None, reason="tracemalloc is unavailable")
def test_restart():
    test_collector._test_restart(memory.MemoryCollector)


@pytest.mark.skipif(tracemalloc is None, reason="tracemalloc is unavailable")
def test_repr():
    test_collector._test_repr(
        memory.MemoryCollector,
        "MemoryCollector(status=<ServiceStatus.STOPPED: 'stopped'>, "
        "recorder=Recorder(default_max_events=32768, max_events={}), "
        "capture_pct=2.0, nframes=64, ignore_profiler=True)",
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
    with stack.StackCollector(r) as sc:
        r = recorder.Recorder()
        c = memory.MemoryCollector(r, ignore_profiler=ignore, capture_pct=100)
        with c as mc:
            while not r.events[memory.MemorySampleEvent]:
                _ = object()
                # Allow gevent to switch to the memory collector thread
                time.sleep(0)
    sc.join()
    mc.join()
    events = r.events[memory.MemorySampleEvent]
    files = {frame.filename for event in events for trace in event.snapshot.traces for frame in trace.traceback}
    return files


@pytest.mark.skipif(tracemalloc is None, reason="tracemalloc is unavailable")
def test_memory_ignore_true():
    files = _test_memory_ignore(True)
    ddprofile_dir = os.path.dirname(ddtrace.profiling.__file__)
    assert all(map(lambda f: not f.startswith(ddprofile_dir), files)), files


@pytest.mark.skipif(tracemalloc is None, reason="tracemalloc is unavailable")
def test_memory_ignore_false():
    files = _test_memory_ignore(False)
    ddprofile_dir = os.path.dirname(ddtrace.profiling.__file__)
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
