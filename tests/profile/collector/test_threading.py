import threading
import time

import pytest

from ddtrace.vendor.six.moves import _thread

from ddtrace.profile import recorder
from ddtrace.profile.collector import threading as collector_threading

from . import test_collector


def test_status():
    test_collector._test_collector_status(collector_threading.LockCollector)


def test_repr():
    test_collector._test_repr(
        collector_threading.LockCollector,
        "LockCollector(recorder=Recorder(max_size=49152), capture_pct=5.0, nframes=64)",
    )


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
    assert event.lock_name == "test_threading.py:41"
    assert event.thread_id == _thread.get_ident()
    assert event.wait_time_ns > 0
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[0] == (__file__, 42, "test_lock_acquire_events")
    assert event.sampling_pct == 100


def test_lock_release_events():
    r = recorder.Recorder()
    with collector_threading.LockCollector(r, capture_pct=100):
        lock = threading.Lock()
        lock.acquire()
        time.sleep(0.1)
        lock.release()
    assert len(r.events[collector_threading.LockAcquireEvent]) == 1
    assert len(r.events[collector_threading.LockReleaseEvent]) == 1
    event = r.events[collector_threading.LockReleaseEvent][0]
    assert event.lock_name == "test_threading.py:59"
    assert event.thread_id == _thread.get_ident()
    assert event.locked_for_ns >= 0.1
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[0] == (__file__, 62, "test_lock_release_events")
    assert event.sampling_pct == 100


@pytest.mark.benchmark(group="threading-lock-create",)
def test_lock_create_speed_patched(benchmark):
    r = recorder.Recorder()
    with collector_threading.LockCollector(r):
        benchmark(threading.Lock)


@pytest.mark.benchmark(group="threading-lock-create",)
def test_lock_create_speed(benchmark):
    benchmark(threading.Lock)


def _lock_acquire_release(lock):
    lock.acquire()
    lock.release()


@pytest.mark.benchmark(group="threading-lock-acquire-release",)
@pytest.mark.parametrize(
    "pct", range(5, 61, 5),
)
def test_lock_acquire_release_speed_patched(benchmark, pct):
    r = recorder.Recorder()
    with collector_threading.LockCollector(r, capture_pct=pct):
        benchmark(_lock_acquire_release, threading.Lock())


@pytest.mark.benchmark(group="threading-lock-acquire-release",)
def test_lock_acquire_release_speed(benchmark):
    benchmark(_lock_acquire_release, threading.Lock())
