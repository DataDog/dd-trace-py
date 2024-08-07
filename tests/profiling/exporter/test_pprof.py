import os
import platform

import mock

from ddtrace import ext
from ddtrace.profiling.collector import _lock
from ddtrace.profiling.collector import memalloc
from ddtrace.profiling.collector import stack_event
from ddtrace.profiling.exporter import pprof


TEST_EVENTS = {
    stack_event.StackExceptionSampleEvent: [
        stack_event.StackExceptionSampleEvent(
            timestamp=1,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", "class"),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
            ],
            exc_type=TypeError,
            sampling_period=1000000,
            nframes=3,
        ),
        stack_event.StackExceptionSampleEvent(
            timestamp=2,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", "class"),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 20, "func5", "otherclass"),
            ],
            sampling_period=1000000,
            exc_type=TypeError,
            nframes=3,
        ),
        stack_event.StackExceptionSampleEvent(
            timestamp=3,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
            ],
            sampling_period=1000000,
            exc_type=TypeError,
            nframes=4,
        ),
        stack_event.StackExceptionSampleEvent(
            timestamp=4,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", "class"),
                ("foobar2.py", 19, "func5", ""),
            ],
            sampling_period=1000000,
            exc_type=TypeError,
            nframes=6,
        ),
        stack_event.StackExceptionSampleEvent(
            timestamp=5,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", "classX"),
                ("foobar2.py", 19, "func5", ""),
            ],
            sampling_period=1000000,
            exc_type=ValueError,
            nframes=3,
        ),
        stack_event.StackExceptionSampleEvent(
            timestamp=6,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
            ],
            sampling_period=1000000,
            exc_type=IOError,
            nframes=3,
        ),
        stack_event.StackExceptionSampleEvent(
            timestamp=7,
            thread_id=67892304,
            thread_native_id=123987,
            local_root_span_id=1322219321,
            span_id=1322219,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 49, "func2", ""),
                ("foobar.py", 19, "func5", ""),
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
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
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
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 20, "func5", ""),
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
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
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
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar2.py", 19, "func5", ""),
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
            local_root_span_id=1322219321,
            span_id=49393,
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar2.py", 19, "func5", ""),
            ],
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
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
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
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 49, "func2", ""),
                ("foobar.py", 19, "func5", ""),
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
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
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
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 20, "func5", ""),
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
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
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
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar2.py", 19, "func5", ""),
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
            local_root_span_id=1322219321,
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar2.py", 19, "func5", ""),
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
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
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
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 49, "func2", ""),
                ("foobar.py", 19, "func5", ""),
            ],
            size=12,
            nframes=3,
            sample_size=512 * 1024,
        ),
    ],
    stack_event.StackSampleEvent: [
        stack_event.StackSampleEvent(
            timestamp=1,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            local_root_span_id=1322219321,
            span_id=49343,
            trace_type=ext.SpanTypes.WEB,
            trace_resource_container=["myresource"],
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
            ],
            task_id=123,
            task_name="sometask",
            wall_time_ns=1324,
            cpu_time_ns=1321,
            sampling_period=1000000,
            nframes=3,
        ),
        stack_event.StackSampleEvent(
            timestamp=2,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            local_root_span_id=1322219321,
            span_id=24930,
            trace_type="sql",
            trace_resource_container=["\x1bnotme"],
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 20, "func5", ""),
            ],
            wall_time_ns=13244,
            cpu_time_ns=1312,
            sampling_period=1000000,
            nframes=3,
        ),
        stack_event.StackSampleEvent(
            timestamp=3,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            local_root_span_id=1322219321,
            span_id=24930,
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
            ],
            wall_time_ns=1324,
            cpu_time_ns=29121,
            sampling_period=1000000,
            nframes=4,
        ),
        stack_event.StackSampleEvent(
            timestamp=4,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar2.py", 19, "func5", ""),
            ],
            wall_time_ns=213244,
            cpu_time_ns=94021,
            sampling_period=1000000,
            nframes=6,
        ),
        stack_event.StackSampleEvent(
            timestamp=5,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            local_root_span_id=1322219321,
            span_id=249304,
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar2.py", 19, "func5", ""),
            ],
            wall_time_ns=132444,
            cpu_time_ns=9042,
            sampling_period=1000000,
            nframes=3,
        ),
        stack_event.StackSampleEvent(
            timestamp=6,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
            ],
            wall_time_ns=18244,
            cpu_time_ns=841019,
            sampling_period=1000000,
            nframes=3,
        ),
        stack_event.StackSampleEvent(
            timestamp=7,
            thread_id=67892304,
            thread_native_id=123987,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 49, "func2", ""),
                ("foobar.py", 19, "func5", ""),
            ],
            wall_time_ns=13244,
            cpu_time_ns=501809,
            sampling_period=1000000,
            nframes=3,
        ),
    ],
    _lock.LockAcquireEvent: [
        _lock.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=1,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
            ],
            task_id=12234,
            task_name="mytask",
            local_root_span_id=23435,
            span_id=345432,
            trace_type=ext.SpanTypes.WEB,
            trace_resource_container=["myresource"],
            nframes=3,
            wait_time_ns=74839,
            sampling_pct=10,
        ),
        _lock.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=2,
            thread_id=67892304,
            thread_name="MainThread",
            local_root_span_id=23435,
            span_id=345432,
            trace_type="sql",
            trace_resource_container=[b"notme"],
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 20, "func5", ""),
            ],
            nframes=3,
            wait_time_ns=7483,
            sampling_pct=10,
        ),
        _lock.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=3,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
            ],
            nframes=4,
            wait_time_ns=7489,
            sampling_pct=10,
        ),
        _lock.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=4,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar2.py", 19, "func5", ""),
            ],
            nframes=6,
            wait_time_ns=4839,
            sampling_pct=10,
        ),
        _lock.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=5,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar2.py", 19, "func5", ""),
            ],
            nframes=3,
            wait_time_ns=748394,
            sampling_pct=10,
        ),
        _lock.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=6,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
            ],
            nframes=3,
            wait_time_ns=748339,
            sampling_pct=10,
        ),
        _lock.LockAcquireEvent(
            lock_name="foobar.py:12",
            timestamp=7,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 49, "func2", ""),
                ("foobar.py", 19, "func5", ""),
            ],
            nframes=3,
            wait_time_ns=174839,
            sampling_pct=10,
        ),
    ],
    _lock.LockReleaseEvent: [
        _lock.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=1,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
            ],
            nframes=3,
            locked_for_ns=74839,
            sampling_pct=5,
        ),
        _lock.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=2,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 20, "func5", ""),
            ],
            nframes=3,
            locked_for_ns=7483,
            sampling_pct=5,
        ),
        _lock.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=3,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
            ],
            nframes=4,
            locked_for_ns=7489,
            sampling_pct=5,
        ),
        _lock.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=4,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar2.py", 19, "func5", ""),
            ],
            nframes=6,
            locked_for_ns=4839,
            sampling_pct=5,
        ),
        _lock.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=5,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar2.py", 19, "func5", ""),
            ],
            nframes=3,
            locked_for_ns=748394,
            sampling_pct=5,
        ),
        _lock.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=6,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 44, "func2", ""),
                ("foobar.py", 19, "func5", ""),
            ],
            nframes=3,
            locked_for_ns=748339,
            sampling_pct=5,
        ),
        _lock.LockReleaseEvent(
            lock_name="foobar.py:12",
            timestamp=7,
            thread_id=67892304,
            thread_name="MainThread",
            frames=[
                ("foobar.py", 23, "func1", ""),
                ("foobar.py", 49, "func2", ""),
                ("foobar.py", 19, "func5", ""),
            ],
            nframes=3,
            locked_for_ns=174839,
            sampling_pct=50,
        ),
    ],
}


def test_string_table():
    t = pprof._StringTable()
    assert len(t) == 1
    id1 = t.index("foobar")
    assert len(t) == 2
    assert id1 == t.index("foobar")
    assert len(t) == 2
    id2 = t.index("foobaz")
    assert len(t) == 3
    assert id2 == t.index("foobaz")
    assert len(t) == 3
    assert id1 != id2


def test_to_str_none():
    c = pprof._PprofConverter()
    id1 = c._str(None)
    id2 = c._str("None")
    id_o = c._str("foobar")
    assert id1 == id2 != id_o


@mock.patch("ddtrace.internal.utils.config.get_application_name")
def test_pprof_exporter(gan):
    gan.return_value = "bonjour"
    exp = pprof.PprofExporter()
    exports, _ = exp.export(TEST_EVENTS, 1, 7)

    assert len(exports.sample_type) == 11
    assert len(exports.string_table) == 58
    assert len(exports.sample) == 28
    assert len(exports.location) == 8

    assert exports.period == 1000000
    assert exports.time_nanos == 1
    assert exports.duration_nanos == 6

    assert all(_ in exports.string_table for _ in ("time", "nanoseconds", "bonjour"))


@mock.patch("ddtrace.internal.utils.config.get_application_name")
def test_pprof_exporter_libs(gan):
    gan.return_value = "bonjour"
    exp = pprof.PprofExporter()
    TEST_EVENTS = {
        stack_event.StackSampleEvent: [
            stack_event.StackSampleEvent(
                timestamp=1,
                thread_id=67892304,
                thread_native_id=123987,
                thread_name="MainThread",
                local_root_span_id=1322219321,
                span_id=49343,
                trace_type=ext.SpanTypes.WEB,
                trace_resource_container=["myresource"],
                frames=[
                    (memalloc.__file__, 44, "func2", ""),
                    ("foobar.py", 19, "func5", ""),
                ],
                task_id=123,
                task_name="sometask",
                wall_time_ns=1324,
                cpu_time_ns=1321,
                sampling_period=1000000,
                nframes=3,
            ),
            stack_event.StackSampleEvent(
                timestamp=2,
                thread_id=67892304,
                thread_native_id=123987,
                thread_name="MainThread",
                local_root_span_id=1322219321,
                span_id=24930,
                trace_type="sql",
                trace_resource_container=["\x1bnotme"],
                frames=[
                    (__file__, 23, "func1", ""),
                    ("foobar.py", 44, "func2", ""),
                    (os.__file__, 20, "func5", ""),
                ],
                wall_time_ns=13244,
                cpu_time_ns=1312,
                sampling_period=1000000,
                nframes=3,
            ),
        ]
    }

    _, libs = exp.export(TEST_EVENTS, 1, 7)

    # Version does not match between pip and __version__ for ddtrace; ignore
    for lib in libs:
        if lib["name"] == "ddtrace":
            del lib["version"]
        elif lib["kind"] == "standard library":
            assert len(lib["paths"]) == 1
            del lib["paths"]
        elif lib["name"] == "<unknown>":
            assert len(lib["paths"]) == 1
            del lib["paths"]

    expected_libs = [
        # {"name": "ddtrace", "kind": "library", "paths": {__file__, memalloc.__file__}},
        {"kind": "standard library", "name": "stdlib", "version": platform.python_version()},
        {
            "kind": "library",
            "name": "<unknown>",
            "version": "<unknown>",
        },
    ]

    expected_libs.append(
        {"kind": "standard library", "name": "platstdlib", "version": platform.python_version()},
    )

    # DEV: We cannot convert the lists to sets because the some of the values
    # in the dicts are list. We resort to matching the elements of one list to
    # the other instead and check that:
    # - for all elements in libs we have a match in expected_libs
    # - we end up with an empty expected_libs
    # This is equivalent to checking that the two lists are equal.
    for lib in libs:
        if "paths" in lib:
            lib["paths"] = set(lib["paths"])
        if lib in expected_libs:
            expected_libs.remove(lib)

    assert not expected_libs


def test_pprof_exporter_empty():
    exp = pprof.PprofExporter()
    export, libs = exp.export({}, 0, 1)
    assert len(libs) > 0
    assert len(export.sample) == 0
