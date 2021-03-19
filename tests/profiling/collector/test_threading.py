import threading

import pytest

from ddtrace.profiling import recorder
from ddtrace.profiling.collector import threading as collector_threading
from ddtrace.vendor.six.moves import _thread

from . import test_collector


def test_repr():
    test_collector._test_repr(
        collector_threading.LockCollector,
        "LockCollector(status=<ServiceStatus.STOPPED: 'stopped'>, "
        "recorder=Recorder(default_max_events=32768, max_events={}), capture_pct=2.0, nframes=64, tracer=None)",
    )


def test_wrapper():
    r = recorder.Recorder()
    collector = collector_threading.LockCollector(r)
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
    collector = collector_threading.LockCollector(r)
    collector.start()
    assert lock == collector.original
    # wrapt makes this true
    assert lock == threading.Lock
    collector.stop()
    assert lock == threading.Lock
    assert collector.original == threading.Lock


def test_lock_acquire_events():
    r = recorder.Recorder()
    with collector_threading.LockCollector(r, capture_pct=100):
        lock = threading.Lock()
        lock.acquire()
    assert len(r.events[collector_threading.LockAcquireEvent]) == 1
    assert len(r.events[collector_threading.LockReleaseEvent]) == 0
    event = r.events[collector_threading.LockAcquireEvent][0]
    assert event.lock_name == "test_threading.py:58"
    assert event.thread_id == _thread.get_ident()
    assert event.wait_time_ns > 0
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[0] == (__file__, 59, "test_lock_acquire_events")
    assert event.sampling_pct == 100


def test_lock_events_tracer(tracer):
    r = recorder.Recorder()
    with collector_threading.LockCollector(r, tracer=tracer, capture_pct=100):
        lock = threading.Lock()
        lock.acquire()
        with tracer.trace("test") as t:
            lock2 = threading.Lock()
            lock2.acquire()
            lock.release()
            trace_id = t.trace_id
            span_id = t.span_id
        lock2.release()
    assert len(r.events[collector_threading.LockAcquireEvent]) == 2
    assert len(r.events[collector_threading.LockReleaseEvent]) == 2
    lock_event_1 = r.events[collector_threading.LockAcquireEvent][0]
    assert lock_event_1.trace_ids is None
    assert lock_event_1.span_ids is None
    lock_event_2 = r.events[collector_threading.LockAcquireEvent][1]
    assert lock_event_2.trace_ids == {trace_id}
    assert lock_event_2.span_ids == {span_id}
    lock_release_1 = r.events[collector_threading.LockReleaseEvent][0]
    assert lock_release_1.trace_ids == {trace_id}
    assert lock_release_1.span_ids == {span_id}
    lock_release_2 = r.events[collector_threading.LockReleaseEvent][1]
    assert lock_release_2.trace_ids is None
    assert lock_release_2.span_ids is None


def test_lock_release_events():
    r = recorder.Recorder()
    with collector_threading.LockCollector(r, capture_pct=100):
        lock = threading.Lock()
        lock.acquire()
        lock.release()
    assert len(r.events[collector_threading.LockAcquireEvent]) == 1
    assert len(r.events[collector_threading.LockReleaseEvent]) == 1
    event = r.events[collector_threading.LockReleaseEvent][0]
    assert event.lock_name == "test_threading.py:104"
    assert event.thread_id == _thread.get_ident()
    assert event.locked_for_ns >= 0.1
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[0] == (__file__, 106, "test_lock_release_events")
    assert event.sampling_pct == 100


@pytest.mark.benchmark(
    group="threading-lock-create",
)
def test_lock_create_speed_patched(benchmark):
    r = recorder.Recorder()
    with collector_threading.LockCollector(r):
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
    with collector_threading.LockCollector(r, capture_pct=pct):
        benchmark(_lock_acquire_release, threading.Lock())


@pytest.mark.benchmark(
    group="threading-lock-acquire-release",
)
def test_lock_acquire_release_speed(benchmark):
    benchmark(_lock_acquire_release, threading.Lock())
