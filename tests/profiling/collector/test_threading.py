import os
import sys
import threading
import uuid

import mock
import pytest
from six.moves import _thread

from ddtrace.profiling import recorder
from ddtrace.profiling.collector import _lock
from ddtrace.profiling.collector import threading as collector_threading
from tests.utils import flaky

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
    assert event.lock_name == "test_threading.py:69:lock"
    assert event.thread_id == _thread.get_ident()
    assert event.wait_time_ns >= 0
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[1] == (__file__.replace(".pyc", ".py"), 69, "test_lock_acquire_events", "")
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
    assert event.lock_name == "test_threading.py:90:lock"
    assert event.thread_id == _thread.get_ident()
    assert event.wait_time_ns >= 0
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[1] == (__file__.replace(".pyc", ".py"), 90, "lockfunc", "Foobar")
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
            span_id = t.span_id
        lock2.release()
    events = r.reset()
    lock1_acquire, lock1_release, lock2_acquire, lock2_release = (
        "test_threading.py:113:lock",
        "test_threading.py:117:lock",
        "test_threading.py:116:lock2",
        "test_threading.py:119:lock2",
    )
    # The tracer might use locks, so we need to look into every event to assert we got ours
    for event_type in (collector_threading.ThreadingLockAcquireEvent, collector_threading.ThreadingLockReleaseEvent):
        if event_type == collector_threading.ThreadingLockAcquireEvent:
            assert {lock1_acquire, lock2_acquire}.issubset({e.lock_name for e in events[event_type]})
        elif event_type == collector_threading.ThreadingLockReleaseEvent:
            assert {lock1_release, lock2_release}.issubset({e.lock_name for e in events[event_type]})
        for event in events[event_type]:
            if event.lock_name in [lock1_acquire, lock2_release]:
                assert event.span_id is None
                assert event.trace_resource_container is None
                assert event.trace_type is None
            elif event.lock_name in [lock2_acquire, lock1_release]:
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
        lock2.release()
    span.resource = resource
    span.finish()
    events = r.reset()
    lock1_acquire, lock1_release, lock2_acquire, lock2_release = (
        "test_threading.py:150:lock",
        "test_threading.py:154:lock",
        "test_threading.py:153:lock2",
        "test_threading.py:155:lock2",
    )
    # The tracer might use locks, so we need to look into every event to assert we got ours
    for event_type in (collector_threading.ThreadingLockAcquireEvent, collector_threading.ThreadingLockReleaseEvent):
        if event_type == collector_threading.ThreadingLockAcquireEvent:
            assert {lock1_acquire, lock2_acquire}.issubset({e.lock_name for e in events[event_type]})
        elif event_type == collector_threading.ThreadingLockReleaseEvent:
            assert {lock1_release, lock2_release}.issubset({e.lock_name for e in events[event_type]})
        for event in events[event_type]:
            assert event.span_id is None
            assert event.trace_resource_container is None
            assert event.trace_type is None


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
            span_id = t.span_id
        lock2.release()
    events = r.reset()
    lock1_acquire, lock1_release, lock2_acquire, lock2_release = (
        "test_threading.py:184:lock",
        "test_threading.py:188:lock",
        "test_threading.py:187:lock2",
        "test_threading.py:190:lock2",
    )
    # The tracer might use locks, so we need to look into every event to assert we got ours
    for event_type in (collector_threading.ThreadingLockAcquireEvent, collector_threading.ThreadingLockReleaseEvent):
        if event_type == collector_threading.ThreadingLockAcquireEvent:
            assert {lock1_acquire, lock2_acquire}.issubset({e.lock_name for e in events[event_type]})
        elif event_type == collector_threading.ThreadingLockReleaseEvent:
            assert {lock1_release, lock2_release}.issubset({e.lock_name for e in events[event_type]})
        for event in events[event_type]:
            if event.lock_name in [lock1_acquire, lock2_release]:
                assert event.span_id is None
                assert event.trace_resource_container is None
                assert event.trace_type is None
            elif event.lock_name in [lock2_acquire, lock1_release]:
                assert event.span_id == span_id
                assert event.trace_resource_container[0] == t.resource
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
    assert event.lock_name == "test_threading.py:220:lock"
    assert event.thread_id == _thread.get_ident()
    assert event.locked_for_ns >= 0
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[1] == (__file__.replace(".pyc", ".py"), 220, "test_lock_release_events", "")
    assert event.sampling_pct == 100


@pytest.mark.skipif(not TESTING_GEVENT, reason="only works with gevent")
@pytest.mark.subprocess(ddtrace_run=True)
def test_lock_gevent_tasks():
    from gevent import monkey  # noqa:F401

    monkey.patch_all()

    import threading

    import pytest

    from ddtrace.profiling import recorder
    from ddtrace.profiling.collector import threading as collector_threading

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
        if event.lock_name == "test_threading.py:252:lock":
            assert event.wait_time_ns >= 0
            assert event.task_id == t.ident
            assert event.task_name == "foobar"
            # It's called through pytest so I'm sure it's gonna be that long, right?
            assert len(event.frames) > 3
            assert event.nframes > 3
            assert event.frames[1] == (
                "tests/profiling/collector/test_threading.py",
                252,
                "play_with_lock",
                "",
            ), event.frames
            assert event.sampling_pct == 100
            break
    else:
        pytest.fail("Lock event not found")

    for event in r.events[collector_threading.ThreadingLockReleaseEvent]:
        if event.lock_name == "test_threading.py:253:lock":
            assert event.locked_for_ns >= 0
            assert event.task_id == t.ident
            assert event.task_name == "foobar"
            # It's called through pytest so I'm sure it's gonna be that long, right?
            assert len(event.frames) > 3
            assert event.nframes > 3
            assert event.frames[1] == (
                "tests/profiling/collector/test_threading.py",
                253,
                "play_with_lock",
                "",
            ), event.frames
            assert event.sampling_pct == 100
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


@flaky(1719591602)
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="only works on linux")
@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="true"),
    err=None,
)
def test_user_threads_have_native_id():
    from os import getpid
    from threading import Thread
    from threading import _MainThread
    from threading import current_thread
    from time import sleep

    main = current_thread()
    assert isinstance(main, _MainThread)
    # We expect the main thread to have the same ID as the PID
    assert main.native_id == getpid(), (main.native_id, getpid())

    t = Thread(target=lambda: None)
    t.start()

    for _ in range(10):
        try:
            # The TID should be higher than the PID, but not too high
            assert 0 < t.native_id - getpid() < 100, (t.native_id, getpid())
        except AttributeError:
            # The native_id attribute is set by the thread so we might have to
            # wait a bit for it to be set.
            sleep(0.1)
        else:
            break
    else:
        raise AssertionError("Thread.native_id not set")

    t.join()


def test_lock_enter_exit_events():
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=100):
        th_lock = threading.Lock()
        with th_lock:
            pass
    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 1
    acquire_event = r.events[collector_threading.ThreadingLockAcquireEvent][0]
    assert acquire_event.lock_name == "test_threading.py:385:th_lock"
    assert acquire_event.thread_id == _thread.get_ident()
    assert acquire_event.wait_time_ns >= 0
    # We know that at least __enter__, this function, and pytest should be
    # in the stack.
    assert len(acquire_event.frames) >= 3
    assert acquire_event.nframes >= 3
    # To implement 'with lock:', _lock._ProfiledLock implements __enter__ and
    # __exit__. So frames[0] is __enter__ and __exit__ respectively.

    assert acquire_event.frames[0] == (
        _lock.__file__.replace(".pyc", ".py"),
        235,
        "__enter__",
        "_ProfiledThreadingLock",
    )
    assert acquire_event.frames[1] == (__file__.replace(".pyc", ".py"), 385, "test_lock_enter_exit_events", "")
    assert acquire_event.sampling_pct == 100

    release_event = r.events[collector_threading.ThreadingLockReleaseEvent][0]
    release_lineno = 385 if sys.version_info >= (3, 10) else 386
    assert release_event.lock_name == "test_threading.py:%d:th_lock" % release_lineno
    assert release_event.thread_id == _thread.get_ident()
    assert release_event.locked_for_ns >= 0
    assert release_event.frames[0] == (_lock.__file__.replace(".pyc", ".py"), 238, "__exit__", "_ProfiledThreadingLock")
    assert release_event.frames[1] == (
        __file__.replace(".pyc", ".py"),
        release_lineno,
        "test_lock_enter_exit_events",
        "",
    )
    assert release_event.sampling_pct == 100


class Foo:
    def __init__(self):
        self.foo_lock = threading.Lock()

    def foo(self):
        with self.foo_lock:
            pass


class Bar:
    def __init__(self):
        self.foo = Foo()

    def bar(self):
        self.foo.foo()


def test_class_member_lock():
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=100):
        foobar = Foo()
        foobar.foo()
        bar = Bar()
        bar.bar()

    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 2
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 2

    acquire_lock_names = {e.lock_name for e in r.events[collector_threading.ThreadingLockAcquireEvent]}
    assert acquire_lock_names == {"test_threading.py:429:foo_lock"}

    release_lock_names = {e.lock_name for e in r.events[collector_threading.ThreadingLockReleaseEvent]}
    release_lienno = 429 if sys.version_info >= (3, 10) else 430
    assert release_lock_names == {"test_threading.py:%d:foo_lock" % release_lienno}


def test_class_member_lock_no_inspect_dir():
    with mock.patch("ddtrace.settings.profiling.config.lock.name_inspect_dir", False):
        r = recorder.Recorder()
        with collector_threading.ThreadingLockCollector(r, capture_pct=100):
            bar = Bar()
            bar.bar()
        assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
        assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 1
        acquire_event = r.events[collector_threading.ThreadingLockAcquireEvent][0]
        assert acquire_event.lock_name == "test_threading.py:429"
        release_event = r.events[collector_threading.ThreadingLockReleaseEvent][0]
        release_lineno = 429 if sys.version_info >= (3, 10) else 430
        assert release_event.lock_name == "test_threading.py:%d" % release_lineno


def test_private_lock():
    class Foo:
        def __init__(self):
            self.__lock = threading.Lock()

        def foo(self):
            with self.__lock:
                pass

    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=100):
        foo = Foo()
        foo.foo()

    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 1

    acquire_event = r.events[collector_threading.ThreadingLockAcquireEvent][0]
    assert acquire_event.lock_name == "test_threading.py:481:_Foo__lock"
    release_event = r.events[collector_threading.ThreadingLockReleaseEvent][0]
    release_lineno = 481 if sys.version_info >= (3, 10) else 482
    assert release_event.lock_name == "test_threading.py:%d:_Foo__lock" % release_lineno


def test_inner_lock():
    class Bar:
        def __init__(self):
            self.foo = Foo()

        def bar(self):
            with self.foo.foo_lock:
                pass

    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=100):
        bar = Bar()
        bar.bar()

    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 1

    acquire_lock_names = {e.lock_name for e in r.events[collector_threading.ThreadingLockAcquireEvent]}
    assert acquire_lock_names == {"test_threading.py:505"}

    release_lock_names = {e.lock_name for e in r.events[collector_threading.ThreadingLockReleaseEvent]}
    release_lienno = 505 if sys.version_info >= (3, 10) else 506
    assert release_lock_names == {"test_threading.py:%d" % release_lienno}


def test_anonymous_lock():
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=100):
        with threading.Lock():
            pass

    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 1

    acquire_event = r.events[collector_threading.ThreadingLockAcquireEvent][0]
    assert acquire_event.lock_name == "test_threading.py:527"
    release_event = r.events[collector_threading.ThreadingLockReleaseEvent][0]
    release_lineno = 527 if sys.version_info >= (3, 10) else 528
    assert release_event.lock_name == "test_threading.py:%d" % release_lineno


def test_global_locks():
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=100):
        from . import global_locks

        global_locks.foo()
        global_locks.bar_instance.bar()

    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 2
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 2

    acquire_lock_names = {e.lock_name for e in r.events[collector_threading.ThreadingLockAcquireEvent]}
    assert acquire_lock_names == {"global_locks.py:9:global_lock", "global_locks.py:18:bar_lock"}

    release_lock_names = {e.lock_name for e in r.events[collector_threading.ThreadingLockReleaseEvent]}
    release_lines = (9, 18) if sys.version_info >= (3, 10) else (10, 19)
    assert release_lock_names == {
        "global_locks.py:%d:global_lock" % release_lines[0],
        "global_locks.py:%d:bar_lock" % release_lines[1],
    }
