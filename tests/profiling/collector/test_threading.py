import os
import threading
import uuid

import pytest

from ddtrace.internal import nogevent
from ddtrace.profiling import recorder
from ddtrace.profiling.collector import threading as collector_threading

from . import test_collector


TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)


def test_repr():
    test_collector._test_repr(
        collector_threading.ThreadingLockCollector,
        "ThreadingLockCollector(status=<ServiceStatus.STOPPED: 'stopped'>, "
        "recorder=Recorder(default_max_events=16384, max_events={}), capture_pct=1.0, nframes=64, "
        "endpoint_collection_enabled=True, tracer=None)",
    )


def test_wrapper():
    r = recorder.Recorder()
    collector = collector_threading.ThreadingLockCollector(r)
    with collector:

        class Foobar(object):
            lock_class = threading.Lock

            def __init__(self):
                lock = self.lock_class()
                assert lock.acquire()
                lock.release()

        # Try to access the attribute
        lock = Foobar.lock_class()
        assert lock.acquire()
        lock.release()

        # Try this way too
        Foobar()


def test_patch():
    r = recorder.Recorder()
    lock = threading.Lock
    collector = collector_threading.ThreadingLockCollector(r)
    collector.start()
    assert lock == collector.original
    # wrapt makes this true
    assert lock == threading.Lock
    collector.stop()
    assert lock == threading.Lock
    assert collector.original == threading.Lock


def test_lock_acquire_events():
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=100):
        lock = threading.Lock()
        lock.acquire()
    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 0
    event = r.events[collector_threading.ThreadingLockAcquireEvent][0]
    assert event.lock_name == "test_threading.py:64"
    assert event.thread_id == nogevent.thread_get_ident()
    assert event.wait_time_ns >= 0
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[0] == (__file__.replace(".pyc", ".py"), 65, "test_lock_acquire_events", "")
    assert event.sampling_pct == 100


def test_lock_acquire_events_class():
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=100):

        class Foobar(object):
            def lockfunc(self):
                lock = threading.Lock()
                lock.acquire()

        Foobar().lockfunc()

    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 0
    event = r.events[collector_threading.ThreadingLockAcquireEvent][0]
    assert event.lock_name == "test_threading.py:85"
    assert event.thread_id == nogevent.thread_get_ident()
    assert event.wait_time_ns >= 0
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[0] == (__file__.replace(".pyc", ".py"), 86, "lockfunc", "Foobar")
    assert event.sampling_pct == 100


def test_lock_events_tracer(tracer):
    resource = str(uuid.uuid4())
    span_type = str(uuid.uuid4())
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, tracer=tracer, capture_pct=100):
        lock = threading.Lock()
        lock.acquire()
        with tracer.trace("test", resource=resource, span_type=span_type) as t:
            lock2 = threading.Lock()
            lock2.acquire()
            lock.release()
            trace_id = t.trace_id
            span_id = t.span_id
        lock2.release()
    events = r.reset()
    # The tracer might use locks, so we need to look into every event to assert we got ours
    for event_type in (collector_threading.ThreadingLockAcquireEvent, collector_threading.ThreadingLockReleaseEvent):
        assert {"test_threading.py:108", "test_threading.py:111"}.issubset({e.lock_name for e in events[event_type]})
        for event in events[event_type]:
            if event.name == "test_threading.py:84":
                assert event.trace_id is None
                assert event.span_id is None
                assert event.trace_resource_container is None
                assert event.trace_type is None
            elif event.name == "test_threading.py:87":
                assert event.trace_id == trace_id
                assert event.span_id == span_id
                assert event.trace_resource_container[0] == t.resource
                assert event.trace_type == t.span_type


def test_lock_events_tracer_late_finish(tracer):
    resource = str(uuid.uuid4())
    span_type = str(uuid.uuid4())
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, tracer=tracer, capture_pct=100):
        lock = threading.Lock()
        lock.acquire()
        span = tracer.start_span("test", span_type=span_type)
        lock2 = threading.Lock()
        lock2.acquire()
        lock.release()
        trace_id = span.trace_id
        span_id = span.span_id
        lock2.release()
    span.resource = resource
    span.finish()
    events = r.reset()
    # The tracer might use locks, so we need to look into every event to assert we got ours
    for event_type in (collector_threading.ThreadingLockAcquireEvent, collector_threading.ThreadingLockReleaseEvent):
        assert {"test_threading.py:139", "test_threading.py:142"}.issubset({e.lock_name for e in events[event_type]})
        for event in events[event_type]:
            if event.name == "test_threading.py:115":
                assert event.trace_id is None
                assert event.span_id is None
                assert event.trace_resource_container is None
                assert event.trace_type is None
            elif event.name == "test_threading.py:118":
                assert event.trace_id == trace_id
                assert event.span_id == span_id
                assert event.trace_resource_container[0] == span.resource
                assert event.trace_type == span.span_type


def test_resource_not_collected(monkeypatch, tracer):
    monkeypatch.setenv("DD_PROFILING_ENDPOINT_COLLECTION_ENABLED", "false")
    resource = str(uuid.uuid4())
    span_type = str(uuid.uuid4())
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, tracer=tracer, capture_pct=100):
        lock = threading.Lock()
        lock.acquire()
        with tracer.trace("test", resource=resource, span_type=span_type) as t:
            lock2 = threading.Lock()
            lock2.acquire()
            lock.release()
            trace_id = t.trace_id
            span_id = t.span_id
        lock2.release()
    events = r.reset()
    # The tracer might use locks, so we need to look into every event to assert we got ours
    for event_type in (collector_threading.ThreadingLockAcquireEvent, collector_threading.ThreadingLockReleaseEvent):
        assert {"test_threading.py:173", "test_threading.py:176"}.issubset({e.lock_name for e in events[event_type]})
        for event in events[event_type]:
            if event.name == "test_threading.py:149":
                assert event.trace_id is None
                assert event.span_id is None
                assert event.trace_resource_container is None
                assert event.trace_type is None
            elif event.name == "test_threading.py:152":
                assert event.trace_id == trace_id
                assert event.span_id == span_id
                assert event.trace_resource_container is None
                assert event.trace_type == t.span_type


def test_lock_release_events():
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=100):
        lock = threading.Lock()
        lock.acquire()
        lock.release()
    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 1
    event = r.events[collector_threading.ThreadingLockReleaseEvent][0]
    assert event.lock_name == "test_threading.py:202"
    assert event.thread_id == nogevent.thread_get_ident()
    assert event.locked_for_ns >= 0
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[0] == (__file__.replace(".pyc", ".py"), 204, "test_lock_release_events", "")
    assert event.sampling_pct == 100


@pytest.mark.skipif(not TESTING_GEVENT, reason="Not testing gevent")
def test_lock_gevent_tasks():
    r = recorder.Recorder()

    def play_with_lock():
        lock = threading.Lock()
        lock.acquire()
        lock.release()

    with collector_threading.ThreadingLockCollector(r, capture_pct=100):
        t = threading.Thread(name="foobar", target=play_with_lock)
        t.start()
        t.join()

    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) >= 1
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) >= 1

    for event in r.events[collector_threading.ThreadingLockAcquireEvent]:
        if event.lock_name == "test_threading.py:223":
            assert event.thread_id == nogevent.main_thread_id
            assert event.wait_time_ns >= 0
            assert event.task_id == t.ident
            assert event.task_name == "foobar"
            # It's called through pytest so I'm sure it's gonna be that long, right?
            assert len(event.frames) > 3
            assert event.nframes > 3
            assert event.frames[0] == (__file__.replace(".pyc", ".py"), 224, "play_with_lock", "")
            assert event.sampling_pct == 100
            assert event.task_id == t.ident
            assert event.task_name == "foobar"
            break
    else:
        pytest.fail("Lock event not found")

    for event in r.events[collector_threading.ThreadingLockReleaseEvent]:
        if event.lock_name == "test_threading.py:223":
            assert event.thread_id == nogevent.main_thread_id
            assert event.locked_for_ns >= 0
            assert event.task_id == t.ident
            assert event.task_name == "foobar"
            # It's called through pytest so I'm sure it's gonna be that long, right?
            assert len(event.frames) > 3
            assert event.nframes > 3
            assert event.frames[0] == (__file__.replace(".pyc", ".py"), 225, "play_with_lock", "")
            assert event.sampling_pct == 100
            assert event.task_id == t.ident
            assert event.task_name == "foobar"
            break
    else:
        pytest.fail("Lock event not found")


@pytest.mark.benchmark(
    group="threading-lock-create",
)
def test_lock_create_speed_patched(benchmark):
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r):
        benchmark(threading.Lock)


@pytest.mark.benchmark(
    group="threading-lock-create",
)
def test_lock_create_speed(benchmark):
    benchmark(threading.Lock)


def _lock_acquire_release(lock):
    lock.acquire()
    lock.release()


@pytest.mark.benchmark(
    group="threading-lock-acquire-release",
)
@pytest.mark.parametrize(
    "pct",
    range(5, 61, 5),
)
def test_lock_acquire_release_speed_patched(benchmark, pct):
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=pct):
        benchmark(_lock_acquire_release, threading.Lock())


@pytest.mark.benchmark(
    group="threading-lock-acquire-release",
)
def test_lock_acquire_release_speed(benchmark):
    benchmark(_lock_acquire_release, threading.Lock())
