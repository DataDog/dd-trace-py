import _thread
import os
import sys
import threading
import uuid

import mock
import pytest

from ddtrace.profiling import recorder
from ddtrace.profiling.collector import _lock
from ddtrace.profiling.collector import threading as collector_threading

from . import test_collector
from .utils import get_lock_linenos
from .utils import init_linenos


TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)

init_linenos(__file__)


def test_repr():
    test_collector._test_repr(
        collector_threading.ThreadingLockCollector,
        "ThreadingLockCollector(status=<ServiceStatus.STOPPED: 'stopped'>, "
        "recorder=Recorder(default_max_events=16384, max_events={}), capture_pct=1.0, nframes=64, "
        "endpoint_collection_enabled=True, export_libdd_enabled=False, tracer=None)",
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
        lock = threading.Lock()  # !CREATE! test_lock_acquire_events
        lock.acquire()  # !ACQUIRE! test_lock_acquire_events
    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 0
    event = r.events[collector_threading.ThreadingLockAcquireEvent][0]
    linenos = get_lock_linenos("test_lock_acquire_events")
    assert linenos.create > 0
    assert event.lock_name == "test_threading.py:{}:lock".format(linenos.create)
    assert event.thread_id == _thread.get_ident()
    assert event.wait_time_ns >= 0
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[0] == (__file__.replace(".pyc", ".py"), linenos.acquire, "test_lock_acquire_events", "")
    assert event.sampling_pct == 100


def test_lock_acquire_events_class():
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=100):

        class Foobar(object):
            def lockfunc(self):
                lock = threading.Lock()  # !CREATE! test_lock_acquire_events_class
                lock.acquire()  # !ACQUIRE! test_lock_acquire_events_class

        Foobar().lockfunc()

    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 0
    event = r.events[collector_threading.ThreadingLockAcquireEvent][0]
    linenos = get_lock_linenos("test_lock_acquire_events_class")
    assert event.lock_name == "test_threading.py:{}:lock".format(linenos.create)
    assert event.thread_id == _thread.get_ident()
    assert event.wait_time_ns >= 0
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[0] == (__file__.replace(".pyc", ".py"), linenos.acquire, "lockfunc", "Foobar")
    assert event.sampling_pct == 100


def test_lock_events_tracer(tracer):
    resource = str(uuid.uuid4())
    span_type = str(uuid.uuid4())
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, tracer=tracer, capture_pct=100):
        lock1 = threading.Lock()  # !CREATE! test_lock_events_tracer1
        lock1.acquire()  # !ACQUIRE! test_lock_events_tracer1
        with tracer.trace("test", resource=resource, span_type=span_type) as t:
            lock2 = threading.Lock()  # !CREATE! test_lock_events_tracer2
            lock2.acquire()  # !ACQUIRE! test_lock_events_tracer2
            lock1.release()  # !RELEASE! test_lock_events_tracer1
            span_id = t.span_id
        lock2.release()  # !RELEASE! test_lock_events_tracer2
    events = r.reset()
    linenos1 = get_lock_linenos("test_lock_events_tracer1")
    linenos2 = get_lock_linenos("test_lock_events_tracer2")
    lock1_name = "test_threading.py:{}:lock1".format(linenos1.create)
    lock2_name = "test_threading.py:{}:lock2".format(linenos2.create)
    lines_with_trace = [linenos2.create, linenos2.acquire, linenos1.release]
    lines_without_trace = [linenos1.create, linenos1.acquire, linenos2.release]
    # The tracer might use locks, so we need to look into every event to assert we got ours
    assert len(events.keys()) == 2  # Only Threading locks in this test
    for event_type in (collector_threading.ThreadingLockAcquireEvent, collector_threading.ThreadingLockReleaseEvent):
        if event_type == collector_threading.ThreadingLockAcquireEvent:
            assert len(events[event_type]) > 0
            assert {lock1_name, lock2_name}.issubset({e.lock_name for e in events[event_type]})
        elif event_type == collector_threading.ThreadingLockReleaseEvent:
            assert len(events[event_type]) > 0
            assert {lock1_name, lock2_name}.issubset({e.lock_name for e in events[event_type]})

        hits = 0  # If we don't keep track of hits, then the test can pass if we didn't check anything!
        for event in events[event_type]:
            if event.lock_name in [lock1_name, lock2_name]:
                file_name, lineno, function_name, class_name = event.frames[0]
                assert file_name == __file__.replace(".pyc", ".py")
                assert lineno in lines_with_trace + lines_without_trace
                assert function_name == "test_lock_events_tracer"
                assert class_name == ""
                if lineno in lines_without_trace:
                    assert event.span_id is None
                    assert event.trace_resource_container is None
                    assert event.trace_type is None
                    hits += 1
                elif lineno in lines_with_trace:
                    assert event.span_id == span_id
                    assert event.trace_resource_container[0] == resource
                    assert event.trace_type == span_type
                    hits += 1
        assert hits == 2


def test_lock_events_tracer_late_finish(tracer):
    resource = str(uuid.uuid4())
    span_type = str(uuid.uuid4())
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, tracer=tracer, capture_pct=100):
        lock1 = threading.Lock()  # !CREATE! test_lock_events_tracer_late_finish1
        lock1.acquire()  # !ACQUIRE! test_lock_events_tracer_late_finish1
        span = tracer.start_span("test", span_type=span_type)
        lock2 = threading.Lock()  # !CREATE! test_lock_events_tracer_late_finish2
        lock2.acquire()  # !ACQUIRE! test_lock_events_tracer_late_finish2
        lock1.release()  # !RELEASE! test_lock_events_tracer_late_finish1
        lock2.release()  # !RELEASE! test_lock_events_tracer_late_finish2
    span.resource = resource
    span.finish()
    events = r.reset()
    lineno1 = get_lock_linenos("test_lock_events_tracer_late_finish1")
    lineno2 = get_lock_linenos("test_lock_events_tracer_late_finish2")
    lock1_name = "test_threading.py:{}:lock1".format(lineno1.create)
    lock2_name = "test_threading.py:{}:lock2".format(lineno2.create)
    # The tracer might use locks, so we need to look into every event to assert we got ours
    assert len(events.keys()) == 2  # Only Threading locks in this test
    for event_type in (collector_threading.ThreadingLockAcquireEvent, collector_threading.ThreadingLockReleaseEvent):
        if event_type == collector_threading.ThreadingLockAcquireEvent:
            assert {lock1_name, lock2_name}.issubset({e.lock_name for e in events[event_type]})
        elif event_type == collector_threading.ThreadingLockReleaseEvent:
            assert {lock1_name, lock2_name}.issubset({e.lock_name for e in events[event_type]})
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
        lock1 = threading.Lock()  # !CREATE! test_resource_not_collected1
        lock1.acquire()  # !ACQUIRE! test_resource_not_collected1
        with tracer.trace("test", resource=resource, span_type=span_type) as t:
            lock2 = threading.Lock()  # !CREATE! test_resource_not_collected2
            lock2.acquire()  # !ACQUIRE! test_resource_not_collected2
            lock1.release()  # !RELEASE! test_resource_not_collected1
            span_id = t.span_id
        lock2.release()  # !RELEASE! test_resource_not_collected2
    events = r.reset()
    linenos_1 = get_lock_linenos("test_resource_not_collected1")
    linenos_2 = get_lock_linenos("test_resource_not_collected2")
    lock1_name = "test_threading.py:{}:lock1".format(linenos_1.create)
    lock2_name = "test_threading.py:{}:lock2".format(linenos_2.create)
    lines_with_trace = [linenos_2.create, linenos_2.acquire, linenos_1.release]
    lines_without_trace = [linenos_1.create, linenos_1.acquire, linenos_2.release]
    # The tracer might use locks, so we need to look into every event to assert we got ours
    for event_type in (collector_threading.ThreadingLockAcquireEvent, collector_threading.ThreadingLockReleaseEvent):
        if event_type == collector_threading.ThreadingLockAcquireEvent:
            assert {lock1_name, lock2_name}.issubset({e.lock_name for e in events[event_type]})
        elif event_type == collector_threading.ThreadingLockReleaseEvent:
            assert {lock1_name, lock2_name}.issubset({e.lock_name for e in events[event_type]})
        else:
            # We don't have any other kind of events we track
            assert False, "Unknown event type"
        hits = 0
        for event in events[event_type]:
            if event.lock_name in [lock1_name, lock2_name]:
                file_name, lineno, function_name, class_name = event.frames[0]
                assert file_name == __file__.replace(".pyc", ".py")
                assert lineno in lines_with_trace + lines_without_trace
                assert function_name == "test_resource_not_collected"
                assert class_name == ""
                if lineno in lines_without_trace:
                    assert event.span_id is None
                    assert event.trace_resource_container is None
                    assert event.trace_type is None
                    hits += 1
                elif lineno in lines_with_trace:
                    assert event.span_id == span_id
                    assert event.trace_resource_container[0] == resource
                    assert event.trace_type == span_type
                    hits += 1
        assert hits == 2


def test_lock_release_events():
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=100):
        lock = threading.Lock()  # !CREATE! test_lock_release_events
        lock.acquire()  # !ACQUIRE! test_lock_release_events
        lock.release()  # !RELEASE! test_lock_release_events

    linenos = get_lock_linenos("test_lock_release_events")
    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 1
    event = r.events[collector_threading.ThreadingLockReleaseEvent][0]
    assert event.lock_name == "test_threading.py:{}:lock".format(linenos.create)
    assert event.thread_id == _thread.get_ident()
    assert event.locked_for_ns >= 0
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[0] == (__file__.replace(".pyc", ".py"), linenos.release, "test_lock_release_events", "")
    assert event.sampling_pct == 100


@pytest.mark.skipif(not TESTING_GEVENT, reason="only works with gevent")
@pytest.mark.subprocess(ddtrace_run=True, env={"DD_PROFILING_FILE_PATH": __file__})
def test_lock_gevent_tasks():
    from gevent import monkey  # noqa:F401

    monkey.patch_all()

    import os
    import threading

    import pytest

    from ddtrace.profiling import recorder
    from ddtrace.profiling.collector import threading as collector_threading
    from tests.profiling.collector.utils import get_lock_linenos
    from tests.profiling.collector.utils import init_linenos

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])

    r = recorder.Recorder()

    def play_with_lock():
        lock = threading.Lock()  # !CREATE! test_lock_gevent_tasks
        lock.acquire()  # !ACQUIRE! test_lock_gevent_tasks
        lock.release()  # !RELEASE! test_lock_gevent_tasks

    with collector_threading.ThreadingLockCollector(r, capture_pct=100):
        t = threading.Thread(name="foobar", target=play_with_lock)
        t.start()
        t.join()

    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) >= 1
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) >= 1

    linenos = get_lock_linenos("test_lock_gevent_tasks")
    for event in r.events[collector_threading.ThreadingLockAcquireEvent]:
        if event.lock_name == "test_threading.py:{}:lock".format(linenos.create):
            assert event.wait_time_ns >= 0
            assert event.task_id == t.ident
            assert event.task_name == "foobar"
            # It's called through pytest so I'm sure it's gonna be that long, right?
            assert len(event.frames) > 3
            assert event.nframes > 3
            assert event.frames[0] == (
                "tests/profiling/collector/test_threading.py",
                linenos.acquire,
                "play_with_lock",
                "",
            ), event.frames
            assert event.sampling_pct == 100
            break
    else:
        pytest.fail("Lock event not found")

    for event in r.events[collector_threading.ThreadingLockReleaseEvent]:
        if event.lock_name == "test_threading.py:{}:lock".format(linenos.create):
            assert event.locked_for_ns >= 0
            assert event.task_id == t.ident
            assert event.task_name == "foobar"
            # It's called through pytest so I'm sure it's gonna be that long, right?
            assert len(event.frames) > 3
            assert event.nframes > 3
            assert event.frames[0] == (
                "tests/profiling/collector/test_threading.py",
                linenos.release,
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
        th_lock = threading.Lock()  # !CREATE! !RELEASE! test_lock_enter_exit_events
        with th_lock:  # !ACQUIRE! test_lock_enter_exit_events
            pass

    linenos = get_lock_linenos("test_lock_enter_exit_events")
    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 1
    acquire_event = r.events[collector_threading.ThreadingLockAcquireEvent][0]
    assert acquire_event.lock_name == "test_threading.py:{}:th_lock".format(linenos.create)
    assert acquire_event.thread_id == _thread.get_ident()
    assert acquire_event.wait_time_ns >= 0
    # We know that at least __enter__, this function, and pytest should be
    # in the stack.
    assert len(acquire_event.frames) >= 3
    assert acquire_event.nframes >= 3

    assert acquire_event.frames[0] == (
        __file__.replace(".pyc", ".py"),
        linenos.acquire,
        "test_lock_enter_exit_events",
        "",
    )
    assert acquire_event.sampling_pct == 100

    release_event = r.events[collector_threading.ThreadingLockReleaseEvent][0]
    assert release_event.lock_name == "test_threading.py:{}:th_lock".format(linenos.release)
    assert release_event.thread_id == _thread.get_ident()
    assert release_event.locked_for_ns >= 0
    release_lineno = 389 if sys.version_info >= (3, 10) else 390
    release_lineno = linenos.acquire + (0 if sys.version_info >= (3, 10) else 1)
    assert release_event.frames[0] == (
        __file__.replace(".pyc", ".py"),
        release_lineno,
        "test_lock_enter_exit_events",
        "",
    )
    assert release_event.sampling_pct == 100


class Foo:
    def __init__(self):
        self.foo_lock = threading.Lock()  # !CREATE! foolock

    def foo(self):
        with self.foo_lock:  # !RELEASE! !ACQUIRE! foolock
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

    linenos = get_lock_linenos("foolock")
    expected_lock_name = "test_threading.py:{}:foo_lock".format(linenos.create)
    for e in r.events[collector_threading.ThreadingLockAcquireEvent]:
        assert e.lock_name == expected_lock_name
        assert e.frames[0] == (__file__.replace(".pyc", ".py"), linenos.acquire, "foo", "Foo")
    for e in r.events[collector_threading.ThreadingLockReleaseEvent]:
        assert e.lock_name == expected_lock_name
        release_lineno = linenos.release + (0 if sys.version_info >= (3, 10) else 1)
        assert e.frames[0] == (__file__.replace(".pyc", ".py"), release_lineno, "foo", "Foo")


def test_class_member_lock_no_inspect_dir():
    with mock.patch("ddtrace.settings.profiling.config.lock.name_inspect_dir", False):
        r = recorder.Recorder()
        with collector_threading.ThreadingLockCollector(r, capture_pct=100):
            bar = Bar()
            bar.bar()
        linenos = get_lock_linenos("foolock")
        assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
        assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 1
        expected_lock_name = "test_threading.py:{}".format(linenos.create)
        acquire_event = r.events[collector_threading.ThreadingLockAcquireEvent][0]
        assert acquire_event.lock_name == expected_lock_name
        assert acquire_event.frames[0] == (__file__.replace(".pyc", ".py"), linenos.acquire, "foo", "Foo")
        release_event = r.events[collector_threading.ThreadingLockReleaseEvent][0]
        assert release_event.lock_name == expected_lock_name
        release_lineno = linenos.acquire + (0 if sys.version_info >= (3, 10) else 1)
        assert release_event.frames[0] == (__file__.replace(".pyc", ".py"), release_lineno, "foo", "Foo")


def test_private_lock():
    class Foo:
        def __init__(self):
            self.__lock = threading.Lock()  # !CREATE! test_private_lock

        def foo(self):
            with self.__lock:  # !RELEASE! !ACQUIRE! test_private_lock
                pass

    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=100):
        foo = Foo()
        foo.foo()

    linenos = get_lock_linenos("test_private_lock")
    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 1
    expected_lock_name = "test_threading.py:{}:_Foo__lock".format(linenos.create)
    acquire_event = r.events[collector_threading.ThreadingLockAcquireEvent][0]
    assert acquire_event.lock_name == expected_lock_name
    assert acquire_event.frames[0] == (__file__.replace(".pyc", ".py"), linenos.acquire, "foo", "Foo")
    release_event = r.events[collector_threading.ThreadingLockReleaseEvent][0]
    assert release_event.lock_name == expected_lock_name
    release_lineno = linenos.release + (0 if sys.version_info >= (3, 10) else 1)
    assert release_event.frames[0] == (__file__.replace(".pyc", ".py"), release_lineno, "foo", "Foo")


def test_inner_lock():
    class Bar:
        def __init__(self):
            self.foo = Foo()

        def bar(self):
            with self.foo.foo_lock:  # !RELEASE! !ACQUIRE! test_inner_lock
                pass

    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=100):
        bar = Bar()
        bar.bar()

    linenos_foo = get_lock_linenos("foolock")
    linenos_bar = get_lock_linenos("test_inner_lock")
    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 1
    expected_lock_name = "test_threading.py:{}".format(linenos_foo.create)
    acquire_event = r.events[collector_threading.ThreadingLockAcquireEvent][0]
    assert acquire_event.lock_name == expected_lock_name
    assert acquire_event.frames[0] == (__file__.replace(".pyc", ".py"), linenos_bar.acquire, "bar", "Bar")
    release_event = r.events[collector_threading.ThreadingLockReleaseEvent][0]
    assert release_event.lock_name == expected_lock_name
    release_lineno = linenos_bar.acquire + (0 if sys.version_info >= (3, 10) else 1)
    assert release_event.frames[0] == (__file__.replace(".pyc", ".py"), release_lineno, "bar", "Bar")


def test_anonymous_lock():
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=100):
        with threading.Lock():  # !CREATE! !ACQUIRE! test_anonymous_lock
            pass

    # Now read this entire file and find the line number for the anonymous lock
    linenos = get_lock_linenos("test_anonymous_lock")
    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 1
    expected_lock_name = "test_threading.py:{}".format(linenos.create)
    acquire_event = r.events[collector_threading.ThreadingLockAcquireEvent][0]
    assert acquire_event.lock_name == expected_lock_name
    assert acquire_event.frames[0] == (__file__.replace(".pyc", ".py"), linenos.create, "test_anonymous_lock", "")
    release_event = r.events[collector_threading.ThreadingLockReleaseEvent][0]
    assert release_event.lock_name == expected_lock_name
    release_lineno = linenos.create + (0 if sys.version_info >= (3, 10) else 1)
    assert release_event.frames[0] == (__file__.replace(".pyc", ".py"), release_lineno, "test_anonymous_lock", "")


def test_wrapt_c_ext_config():
    if os.environ.get("WRAPT_DISABLE_EXTENSIONS"):
        assert _lock.WRAPT_C_EXT is False
    else:
        try:
            import wrapt._wrappers as _w
        except ImportError:
            assert _lock.WRAPT_C_EXT is False
        else:
            assert _lock.WRAPT_C_EXT is True
            del _w
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=100):
        th_lock = threading.Lock()  # !CREATE! test_wrapt_c_ext_config
        with th_lock:  # !ACQUIRE! !RELEASE! test_wrapt_c_ext_config
            pass

    linenos = get_lock_linenos("test_wrapt_c_ext_config")
    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 1
    acquire_event = r.events[collector_threading.ThreadingLockAcquireEvent][0]
    assert acquire_event.lock_name == "test_threading.py:{}:th_lock".format(linenos.create)
    assert acquire_event.frames[0] == (__file__.replace(".pyc", ".py"), linenos.acquire, "test_wrapt_c_ext_config", "")
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 1
    release_event = r.events[collector_threading.ThreadingLockReleaseEvent][0]
    assert release_event.lock_name == "test_threading.py:{}:th_lock".format(linenos.create)
    release_lineno = linenos.acquire + (0 if sys.version_info >= (3, 10) else 1)
    assert release_event.frames[0] == (__file__.replace(".pyc", ".py"), release_lineno, "test_wrapt_c_ext_config", "")


def test_global_locks():
    r = recorder.Recorder()
    with collector_threading.ThreadingLockCollector(r, capture_pct=100):
        from . import global_locks

        global_locks.foo()
        global_locks.bar_instance.bar()

    assert len(r.events[collector_threading.ThreadingLockAcquireEvent]) == 2
    assert len(r.events[collector_threading.ThreadingLockReleaseEvent]) == 2
    expected_lock_names = ["global_locks.py:4:global_lock", "global_locks.py:15:bar_lock"]
    expected_filename = __file__.replace(".pyc", ".py").replace("test_threading", "global_locks")
    for e in r.events[collector_threading.ThreadingLockAcquireEvent]:
        assert e.lock_name in expected_lock_names
        if e.lock_name == expected_lock_names[0]:
            assert e.frames[0] == (expected_filename, 9, "foo", "")
        elif e.lock_name == expected_lock_names[1]:
            assert e.frames[0] == (expected_filename, 18, "bar", "Bar")
    for e in r.events[collector_threading.ThreadingLockReleaseEvent]:
        assert e.lock_name in expected_lock_names
        if e.lock_name == expected_lock_names[0]:
            release_lineno = 9 if sys.version_info >= (3, 10) else 10
            assert e.frames[0] == (expected_filename, release_lineno, "foo", "")
        elif e.lock_name == expected_lock_names[1]:
            release_lineno = 18 if sys.version_info >= (3, 10) else 19
            assert e.frames[0] == (expected_filename, release_lineno, "bar", "Bar")
