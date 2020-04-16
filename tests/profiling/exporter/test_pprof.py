import os
import sys

try:
    import tracemalloc
except ImportError:
    tracemalloc = None

import mock

import pytest

from ddtrace.profiling.collector import exceptions
from ddtrace.profiling.collector import memory
from ddtrace.profiling.collector import stack
from ddtrace.profiling.collector import threading
from ddtrace.profiling.exporter import pprof


TEST_EVENTS = {
    stack.StackExceptionSampleEvent: [
        stack.StackExceptionSampleEvent(
            timestamp=1,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 19, "func5"),],
            exc_type=TypeError,
            sampling_period=1000000,
            nframes=3,
        ),
        stack.StackExceptionSampleEvent(
            timestamp=2,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 20, "func5"),],
            sampling_period=1000000,
            exc_type=TypeError,
            nframes=3,
        ),
        stack.StackExceptionSampleEvent(
            timestamp=3,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 19, "func5"),],
            sampling_period=1000000,
            exc_type=TypeError,
            nframes=4,
        ),
        stack.StackExceptionSampleEvent(
            timestamp=4,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar2.py", 19, "func5"),],
            sampling_period=1000000,
            exc_type=TypeError,
            nframes=6,
        ),
        stack.StackExceptionSampleEvent(
            timestamp=5,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar2.py", 19, "func5"),],
            sampling_period=1000000,
            exc_type=ValueError,
            nframes=3,
        ),
        stack.StackExceptionSampleEvent(
            timestamp=6,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 19, "func5"),],
            sampling_period=1000000,
            exc_type=IOError,
            nframes=3,
        ),
        stack.StackExceptionSampleEvent(
            timestamp=7,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 49, "func2"), ("foobar.py", 19, "func5"),],
            sampling_period=1000000,
            exc_type=IOError,
            nframes=3,
        ),
    ],
    exceptions.UncaughtExceptionEvent: [
        exceptions.UncaughtExceptionEvent(
            timestamp=1,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 19, "func5"),],
            nframes=3,
            exc_type=ValueError,
        ),
        exceptions.UncaughtExceptionEvent(
            timestamp=2,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 20, "func5"),],
            nframes=3,
            exc_type=ValueError,
        ),
        exceptions.UncaughtExceptionEvent(
            timestamp=3,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 19, "func5"),],
            nframes=4,
            exc_type=IOError,
        ),
        exceptions.UncaughtExceptionEvent(
            timestamp=4,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar2.py", 19, "func5"),],
            nframes=6,
            exc_type=IOError,
        ),
        exceptions.UncaughtExceptionEvent(
            timestamp=5,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar2.py", 19, "func5"),],
            nframes=3,
            exc_type=IOError,
        ),
        exceptions.UncaughtExceptionEvent(
            timestamp=6,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 19, "func5"),],
            nframes=3,
            exc_type=IOError,
        ),
        exceptions.UncaughtExceptionEvent(
            timestamp=7,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 49, "func2"), ("foobar.py", 19, "func5"),],
            nframes=3,
            exc_type=IOError,
        ),
    ],
    stack.StackSampleEvent: [
        stack.StackSampleEvent(
            timestamp=1,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 19, "func5"),],
            wall_time_ns=1324,
            cpu_time_ns=1321,
            sampling_period=1000000,
            nframes=3,
        ),
        stack.StackSampleEvent(
            timestamp=2,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 20, "func5"),],
            wall_time_ns=13244,
            cpu_time_ns=1312,
            sampling_period=1000000,
            nframes=3,
        ),
        stack.StackSampleEvent(
            timestamp=3,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 19, "func5"),],
            wall_time_ns=1324,
            cpu_time_ns=29121,
            sampling_period=1000000,
            nframes=4,
        ),
        stack.StackSampleEvent(
            timestamp=4,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar2.py", 19, "func5"),],
            wall_time_ns=213244,
            cpu_time_ns=94021,
            sampling_period=1000000,
            nframes=6,
        ),
        stack.StackSampleEvent(
            timestamp=5,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar2.py", 19, "func5"),],
            wall_time_ns=132444,
            cpu_time_ns=9042,
            sampling_period=1000000,
            nframes=3,
        ),
        stack.StackSampleEvent(
            timestamp=6,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 19, "func5"),],
            wall_time_ns=18244,
            cpu_time_ns=841019,
            sampling_period=1000000,
            nframes=3,
        ),
        stack.StackSampleEvent(
            timestamp=7,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 49, "func2"), ("foobar.py", 19, "func5"),],
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
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 19, "func5"),],
            nframes=3,
            wait_time_ns=74839,
            sampling_pct=10,
        ),
        threading.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=2,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 20, "func5"),],
            nframes=3,
            wait_time_ns=7483,
            sampling_pct=10,
        ),
        threading.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=3,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 19, "func5"),],
            nframes=4,
            wait_time_ns=7489,
            sampling_pct=10,
        ),
        threading.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=4,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar2.py", 19, "func5"),],
            nframes=6,
            wait_time_ns=4839,
            sampling_pct=10,
        ),
        threading.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=5,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar2.py", 19, "func5"),],
            nframes=3,
            wait_time_ns=748394,
            sampling_pct=10,
        ),
        threading.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=6,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 19, "func5"),],
            nframes=3,
            wait_time_ns=748339,
            sampling_pct=10,
        ),
        threading.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=7,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 49, "func2"), ("foobar.py", 19, "func5"),],
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
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 19, "func5"),],
            nframes=3,
            locked_for_ns=74839,
            sampling_pct=5,
        ),
        threading.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=2,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 20, "func5"),],
            nframes=3,
            locked_for_ns=7483,
            sampling_pct=5,
        ),
        threading.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=3,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 19, "func5"),],
            nframes=4,
            locked_for_ns=7489,
            sampling_pct=5,
        ),
        threading.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=4,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar2.py", 19, "func5"),],
            nframes=6,
            locked_for_ns=4839,
            sampling_pct=5,
        ),
        threading.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=5,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar2.py", 19, "func5"),],
            nframes=3,
            locked_for_ns=748394,
            sampling_pct=5,
        ),
        threading.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=6,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 44, "func2"), ("foobar.py", 19, "func5"),],
            nframes=3,
            locked_for_ns=748339,
            sampling_pct=5,
        ),
        threading.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=7,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[("foobar.py", 23, "func1"), ("foobar.py", 49, "func2"), ("foobar.py", 19, "func5"),],
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
    if tracemalloc:
        if stack.FEATURES["stack-exceptions"]:
            filename = "test-pprof-exporter_tracemalloc+stack-exceptions.txt"
        else:
            filename = "test-pprof-exporter_tracemalloc.txt"
    else:
        filename = "test-pprof-exporter.txt"

    with open(os.path.join(os.path.dirname(__file__), filename)) as f:
        assert f.read() == str(exports), filename


def test_pprof_exporter_empty():
    exp = pprof.PprofExporter()
    export = exp.export({}, 0, 1)
    assert len(export.sample) == 0


@pytest.mark.skipif(tracemalloc is None, reason="tracemalloc is unavailable")
def test_ppprof_memory_exporter():
    if sys.version_info.major <= 3 and sys.version_info.minor < 6:
        # Python before 3.6 does not support domain
        traces = [
            (45, (("<unknown>", 0),)),
            (64, (("<stdin>", 1),)),
            (8224, (("<unknown>", 0),)),
            (144, (("<unknown>", 0),)),
            (32, (("<unknown>", 0),)),
            (32, (("<stdin>", 1),)),
            (24, (("<unknown>", 0),)),
        ]

    else:
        traces = [
            (0, 45, (("<unknown>", 0),)),
            (0, 64, (("<stdin>", 1),)),
            (0, 8224, (("<unknown>", 0),)),
            (0, 144, (("<unknown>", 0),)),
            (0, 32, (("<unknown>", 0),)),
            (0, 32, (("<stdin>", 1),)),
            (0, 24, (("<unknown>", 0),)),
        ]
    events = {
        memory.MemorySampleEvent: [
            memory.MemorySampleEvent(timestamp=1, snapshot=tracemalloc.Snapshot(traces, 1), sampling_pct=10),
            memory.MemorySampleEvent(timestamp=2, snapshot=tracemalloc.Snapshot(traces, 1), sampling_pct=10),
        ],
    }
    exp = pprof.PprofExporter()
    exp._get_program_name = mock.Mock()
    exp._get_program_name.return_value = "bonjour"
    if stack.FEATURES["stack-exceptions"]:
        assert """sample_type {
  type: 5
  unit: 6
}
sample_type {
  type: 7
  unit: 8
}
sample_type {
  type: 9
  unit: 8
}
sample_type {
  type: 10
  unit: 6
}
sample_type {
  type: 11
  unit: 6
}
sample_type {
  type: 12
  unit: 8
}
sample_type {
  type: 13
  unit: 6
}
sample_type {
  type: 14
  unit: 8
}
sample_type {
  type: 15
  unit: 6
}
sample_type {
  type: 16
  unit: 6
}
sample_type {
  type: 17
  unit: 18
}
sample {
  location_id: 1
  value: 0
  value: 0
  value: 0
  value: 0
  value: 0
  value: 0
  value: 0
  value: 0
  value: 0
  value: 100
  value: 169380
}
sample {
  location_id: 2
  value: 0
  value: 0
  value: 0
  value: 0
  value: 0
  value: 0
  value: 0
  value: 0
  value: 0
  value: 40
  value: 1920
}
mapping {
  id: 1
  filename: 20
}
location {
  id: 1
  line {
    function_id: 1
  }
}
location {
  id: 2
  line {
    function_id: 2
    line: 1
  }
}
function {
  id: 1
  name: 1
  filename: 2
}
function {
  id: 2
  name: 3
  filename: 4
}
string_table: ""
string_table: "<unknown>:0"
string_table: "<unknown>"
string_table: "<stdin>:1"
string_table: "<stdin>"
string_table: "cpu-samples"
string_table: "count"
string_table: "cpu-time"
string_table: "nanoseconds"
string_table: "wall-time"
string_table: "uncaught-exceptions"
string_table: "lock-acquire"
string_table: "lock-acquire-wait"
string_table: "lock-release"
string_table: "lock-release-hold"
string_table: "exception-samples"
string_table: "alloc-samples"
string_table: "alloc-space"
string_table: "bytes"
string_table: "time"
string_table: "bonjour"
time_nanos: 1
duration_nanos: 1
period_type {
  type: 19
  unit: 8
}
""" == str(
            exp.export(events, 1, 2)
        )
