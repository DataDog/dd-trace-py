import os

import mock

from ddtrace.profiling.collector import memalloc
from ddtrace.profiling.collector import stack
from ddtrace.profiling.collector import threading
from ddtrace.profiling.exporter import pprof
from ddtrace.vendor import six


TEST_EVENTS = {
    stack.StackExceptionSampleEvent: [
        stack.StackExceptionSampleEvent(
            timestamp=1,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            exc_type=TypeError,
            sampling_period=1000000,
            nframes=3,
        ),
        stack.StackExceptionSampleEvent(
            timestamp=2,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 20, "func5"),
            ],
            sampling_period=1000000,
            exc_type=TypeError,
            nframes=3,
        ),
        stack.StackExceptionSampleEvent(
            timestamp=3,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            sampling_period=1000000,
            exc_type=TypeError,
            nframes=4,
        ),
        stack.StackExceptionSampleEvent(
            timestamp=4,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar2.py", 19, "func5"),
            ],
            sampling_period=1000000,
            exc_type=TypeError,
            nframes=6,
        ),
        stack.StackExceptionSampleEvent(
            timestamp=5,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar2.py", 19, "func5"),
            ],
            sampling_period=1000000,
            exc_type=ValueError,
            nframes=3,
        ),
        stack.StackExceptionSampleEvent(
            timestamp=6,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            sampling_period=1000000,
            exc_type=IOError,
            nframes=3,
        ),
        stack.StackExceptionSampleEvent(
            timestamp=7,
            thread_id=67892304,
            thread_native_id=123987,
            trace_ids=set([1322219321, 343332]),
            span_ids=set([1322219]),
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 49, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            sampling_period=1000000,
            exc_type=IOError,
            nframes=3,
        ),
    ],
    memalloc.MemoryAllocSampleEvent: [
        memalloc.MemoryAllocSampleEvent(
            timestamp=1,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            size=34,
            capture_pct=44,
            nframes=3,
            nevents=1024,
        ),
        memalloc.MemoryAllocSampleEvent(
            timestamp=2,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 20, "func5"),
            ],
            size=99,
            capture_pct=100,
            nframes=3,
            nevents=1024,
        ),
        memalloc.MemoryAllocSampleEvent(
            timestamp=3,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            size=340,
            capture_pct=50,
            nframes=4,
            nevents=1024,
        ),
        memalloc.MemoryAllocSampleEvent(
            timestamp=4,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar2.py", 19, "func5"),
            ],
            size=44,
            capture_pct=33,
            nframes=6,
            nevents=1024,
        ),
        memalloc.MemoryAllocSampleEvent(
            timestamp=5,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            trace_ids=set([1322219321]),
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar2.py", 19, "func5"),
            ],
            span_ids=set([49393]),
            size=68,
            capture_pct=50,
            nframes=3,
            nevents=2048,
        ),
        memalloc.MemoryAllocSampleEvent(
            timestamp=6,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            size=24,
            capture_pct=90,
            nframes=3,
            nevents=2048,
        ),
        memalloc.MemoryAllocSampleEvent(
            timestamp=7,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 49, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            size=12,
            capture_pct=100,
            nframes=3,
            nevents=2048,
        ),
    ],
    memalloc.MemoryHeapSampleEvent: [
        memalloc.MemoryHeapSampleEvent(
            timestamp=1,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            size=34,
            nframes=3,
            sample_size=512 * 1024,
        ),
        memalloc.MemoryHeapSampleEvent(
            timestamp=2,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 20, "func5"),
            ],
            size=99,
            nframes=3,
            sample_size=512 * 1024,
        ),
        memalloc.MemoryHeapSampleEvent(
            timestamp=3,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            size=340,
            nframes=4,
            sample_size=512 * 1024,
        ),
        memalloc.MemoryHeapSampleEvent(
            timestamp=4,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar2.py", 19, "func5"),
            ],
            size=44,
            nframes=6,
            sample_size=512 * 1024,
        ),
        memalloc.MemoryHeapSampleEvent(
            timestamp=5,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            trace_ids=set([1322219321]),
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar2.py", 19, "func5"),
            ],
            size=68,
            nframes=3,
            sample_size=512 * 1024,
        ),
        memalloc.MemoryHeapSampleEvent(
            timestamp=6,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            size=24,
            nframes=3,
            sample_size=512 * 1024,
        ),
        memalloc.MemoryHeapSampleEvent(
            timestamp=7,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 49, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            size=12,
            nframes=3,
            sample_size=512 * 1024,
        ),
    ],
    stack.StackSampleEvent: [
        stack.StackSampleEvent(
            timestamp=1,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            trace_ids=set([1322219321]),
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            span_ids=set([49343]),
            wall_time_ns=1324,
            cpu_time_ns=1321,
            sampling_period=1000000,
            nframes=3,
        ),
        stack.StackSampleEvent(
            timestamp=2,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            trace_ids=set([1322219321]),
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 20, "func5"),
            ],
            span_ids=set([24930]),
            wall_time_ns=13244,
            cpu_time_ns=1312,
            sampling_period=1000000,
            nframes=3,
        ),
        stack.StackSampleEvent(
            timestamp=3,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            trace_ids=set([1322219321]),
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            span_ids=set([24930]),
            wall_time_ns=1324,
            cpu_time_ns=29121,
            sampling_period=1000000,
            nframes=4,
        ),
        stack.StackSampleEvent(
            timestamp=4,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar2.py", 19, "func5"),
            ],
            wall_time_ns=213244,
            cpu_time_ns=94021,
            sampling_period=1000000,
            nframes=6,
        ),
        stack.StackSampleEvent(
            timestamp=5,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            trace_ids=set([1322219321]),
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar2.py", 19, "func5"),
            ],
            span_ids=set([249304]),
            wall_time_ns=132444,
            cpu_time_ns=9042,
            sampling_period=1000000,
            nframes=3,
        ),
        stack.StackSampleEvent(
            timestamp=6,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            wall_time_ns=18244,
            cpu_time_ns=841019,
            sampling_period=1000000,
            nframes=3,
        ),
        stack.StackSampleEvent(
            timestamp=7,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 49, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            wall_time_ns=13244,
            cpu_time_ns=501809,
            sampling_period=1000000,
            nframes=3,
        ),
    ],
    threading.LockAcquireEvent: [
        threading.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=1,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            trace_ids={23435},
            span_ids={345432},
            nframes=3,
            wait_time_ns=74839,
            sampling_pct=10,
        ),
        threading.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=2,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 20, "func5"),
            ],
            nframes=3,
            wait_time_ns=7483,
            sampling_pct=10,
        ),
        threading.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=3,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            nframes=4,
            wait_time_ns=7489,
            sampling_pct=10,
        ),
        threading.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=4,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar2.py", 19, "func5"),
            ],
            nframes=6,
            wait_time_ns=4839,
            sampling_pct=10,
        ),
        threading.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=5,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar2.py", 19, "func5"),
            ],
            nframes=3,
            wait_time_ns=748394,
            sampling_pct=10,
        ),
        threading.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=6,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            nframes=3,
            wait_time_ns=748339,
            sampling_pct=10,
        ),
        threading.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=7,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 49, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            nframes=3,
            wait_time_ns=174839,
            sampling_pct=10,
        ),
    ],
    threading.LockReleaseEvent: [
        threading.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=1,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            nframes=3,
            locked_for_ns=74839,
            sampling_pct=5,
        ),
        threading.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=2,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 20, "func5"),
            ],
            nframes=3,
            locked_for_ns=7483,
            sampling_pct=5,
        ),
        threading.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=3,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            nframes=4,
            locked_for_ns=7489,
            sampling_pct=5,
        ),
        threading.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=4,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar2.py", 19, "func5"),
            ],
            nframes=6,
            locked_for_ns=4839,
            sampling_pct=5,
        ),
        threading.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=5,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar2.py", 19, "func5"),
            ],
            nframes=3,
            locked_for_ns=748394,
            sampling_pct=5,
        ),
        threading.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=6,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 44, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            nframes=3,
            locked_for_ns=748339,
            sampling_pct=5,
        ),
        threading.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=7,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1"),
                ("foobar.py", 49, "func2"),
                ("foobar.py", 19, "func5"),
            ],
            nframes=3,
            locked_for_ns=174839,
            sampling_pct=50,
        ),
    ],
}


def test_sequence():
    s = pprof._Sequence()
    assert s.start_at == 1
    assert s.next_id == 1
    assert s.generate() == 1
    assert s.start_at == 1
    assert s.next_id == 2


def test_string_table():
    t = pprof._StringTable()
    assert len(t) == 1
    id1 = t.to_id("foobar")
    assert len(t) == 2
    assert id1 == t.to_id("foobar")
    assert len(t) == 2
    id2 = t.to_id("foobaz")
    assert len(t) == 3
    assert id2 == t.to_id("foobaz")
    assert len(t) == 3
    assert id1 != id2


def test_to_str_none():
    c = pprof._PprofConverter()
    id1 = c._str(None)
    id2 = c._str("None")
    id_o = c._str("foobar")
    assert id1 == id2 != id_o


def test_ppprof_exporter():
    exp = pprof.PprofExporter()
    exp._get_program_name = mock.Mock()
    exp._get_program_name.return_value = "bonjour"
    exports = exp.export(TEST_EVENTS, 1, 7)
    if six.PY2:
        filename = "test-pprof-exporter-py2.txt"
    else:
        filename = "test-pprof-exporter.txt"
    with open(os.path.join(os.path.dirname(__file__), filename)) as f:
        assert f.read() == str(exports), filename


def test_pprof_exporter_empty():
    exp = pprof.PprofExporter()
    export = exp.export({}, 0, 1)
    assert len(export.sample) == 0
