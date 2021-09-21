# -*- encoding: utf-8 -*-
import os
import threading
import time
import timeit
import typing
import uuid

import pytest
import six

from ddtrace.internal import nogevent
from ddtrace.profiling import collector
from ddtrace.profiling import event as event_mod
from ddtrace.profiling import profiler
from ddtrace.profiling import recorder
from ddtrace.profiling.collector import _task
from ddtrace.profiling.collector import _threading
from ddtrace.profiling.collector import stack

from . import test_collector


TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)


def func1():
    return func2()


def func2():
    return func3()


def func3():
    return func4()


def func4():
    return func5()


def func5():
    return nogevent.sleep(1)


def test_collect_truncate():
    r = recorder.Recorder()
    c = stack.StackCollector(r, nframes=5)
    c.start()
    func1()
    while not r.events[stack.StackSampleEvent]:
        pass
    c.stop()
    for e in r.events[stack.StackSampleEvent]:
        if e.thread_name == "MainThread":
            assert len(e.frames) <= c.nframes
            break
    else:
        pytest.fail("Unable to find the main thread")


def test_collect_once():
    r = recorder.Recorder()
    s = stack.StackCollector(r)
    s._init()
    all_events = s.collect()
    assert len(all_events) == 2
    stack_events = all_events[0]
    for e in stack_events:
        if e.thread_name == "MainThread":
            if TESTING_GEVENT and _task._gevent_tracer is not None:
                assert e.task_id > 0
                assert e.task_name == e.thread_name
            else:
                assert e.task_id is None
                assert e.task_name is None
            assert e.thread_id > 0
            assert len(e.frames) >= 1
            assert e.frames[0][0].endswith(".py")
            assert e.frames[0][1] > 0
            assert isinstance(e.frames[0][2], str)
            break
    else:
        pytest.fail("Unable to find MainThread")


def _fib(n):
    if n == 1:
        return 1
    elif n == 0:
        return 0
    else:
        return _fib(n - 1) + _fib(n - 2)


@pytest.mark.skipif(_task._gevent_tracer is None, reason="gevent tasks not supported")
def test_collect_gevent_thread_task():
    r = recorder.Recorder()
    s = stack.StackCollector(r)

    # Start some (green)threads

    def _dofib():
        for _ in range(10):
            # spend some time in CPU so the profiler can catch something
            _fib(28)
            # Just make sure gevent switches threads/greenlets
            time.sleep(0)

    threads = []
    with s:
        for i in range(10):
            t = threading.Thread(target=_dofib, name="TestThread %d" % i)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    for event in r.events[stack.StackSampleEvent]:
        if event.thread_name == "MainThread" and event.task_id in {thread.ident for thread in threads}:
            assert event.task_name.startswith("TestThread ")
            # This test is not uber-reliable as it has timing issue, therefore if we find one of our TestThread with the
            # correct info, we're happy enough to stop here.
            break
    else:
        pytest.fail("No gevent thread found")


def test_max_time_usage():
    r = recorder.Recorder()
    with pytest.raises(ValueError):
        stack.StackCollector(r, max_time_usage_pct=0)


def test_max_time_usage_over():
    r = recorder.Recorder()
    with pytest.raises(ValueError):
        stack.StackCollector(r, max_time_usage_pct=200)


def test_ignore_profiler_single():
    r, c, thread_id = test_collector._test_collector_collect(
        stack.StackCollector, stack.StackSampleEvent, ignore_profiler=True
    )
    events = r.events[stack.StackSampleEvent]
    assert thread_id not in {e.thread_id for e in events}


def test_no_ignore_profiler_single():
    r, c, thread_id = test_collector._test_collector_collect(
        stack.StackCollector, stack.StackSampleEvent, ignore_profiler=False
    )
    events = r.events[stack.StackSampleEvent]
    assert thread_id in {e.thread_id for e in events}


class CollectorTest(collector.PeriodicCollector):
    def collect(self):
        # type: (...) -> typing.Iterable[typing.Iterable[event_mod.Event]]
        _fib(22)
        return []


@pytest.mark.skipif(_task._gevent_tracer is None, reason="gevent tasks not supported")
@pytest.mark.parametrize("ignore", (True, False))
def test_ignore_profiler_gevent_task(monkeypatch, ignore):
    monkeypatch.setenv("DD_PROFILING_API_TIMEOUT", "0.1")
    monkeypatch.setenv("DD_PROFILING_IGNORE_PROFILER", str(ignore))
    p = profiler.Profiler()
    p.start()
    # This test is particularly useful with gevent enabled: create a test collector that run often and for long so we're
    # sure to catch it with the StackProfiler and that it's not ignored.
    c = CollectorTest(p._profiler._recorder, interval=0.00001)
    c.start()
    # Wait forever and stop when we finally find an event with our collector task id
    while True:
        events = p._profiler._recorder.reset()
        ids = {e.task_id for e in events[stack.StackSampleEvent]}
        if (c._worker.ident in ids) != ignore:
            break
        # Give some time for gevent to switch greenlets
        time.sleep(0.1)
    c.stop()
    p.stop(flush=False)


def test_collect():
    test_collector._test_collector_collect(stack.StackCollector, stack.StackSampleEvent)


def test_restart():
    test_collector._test_restart(stack.StackCollector)


def test_repr():
    test_collector._test_repr(
        stack.StackCollector,
        "StackCollector(status=<ServiceStatus.STOPPED: 'stopped'>, "
        "recorder=Recorder(default_max_events=32768, max_events={}), min_interval_time=0.01, max_time_usage_pct=1.0, "
        "nframes=64, ignore_profiler=False, tracer=None)",
    )


def test_new_interval():
    r = recorder.Recorder()
    c = stack.StackCollector(r, max_time_usage_pct=2)
    new_interval = c._compute_new_interval(1000000)
    assert new_interval == 0.049
    new_interval = c._compute_new_interval(2000000)
    assert new_interval == 0.098
    c = stack.StackCollector(r, max_time_usage_pct=10)
    new_interval = c._compute_new_interval(200000)
    assert new_interval == 0.01
    new_interval = c._compute_new_interval(1)
    assert new_interval == c.min_interval_time


# Function to use for stress-test of polling
MAX_FN_NUM = 30
FN_TEMPLATE = """def _f{num}():
  return _f{nump1}()"""

for num in range(MAX_FN_NUM):
    if six.PY3:
        exec(FN_TEMPLATE.format(num=num, nump1=num + 1))
    else:
        exec(FN_TEMPLATE.format(num=num, nump1=num + 1))

exec(
    """def _f{MAX_FN_NUM}():
    try:
      raise ValueError('test')
    except Exception:
      time.sleep(2)""".format(
        MAX_FN_NUM=MAX_FN_NUM
    )
)


def test_stress_threads():
    NB_THREADS = 40

    threads = []
    for i in range(NB_THREADS):
        t = threading.Thread(target=_f0)  # noqa: E149,F821
        t.start()
        threads.append(t)

    s = stack.StackCollector(recorder=recorder.Recorder())
    number = 20000
    s._init()
    exectime = timeit.timeit(s.collect, number=number)
    # Threads are fake threads with gevent, so result is actually for one thread, not NB_THREADS
    exectime_per_collect = exectime / number
    print("%.3f ms per call" % (1000.0 * exectime_per_collect))
    print(
        "CPU overhead for %d threads with %d functions long at %d Hz: %.2f%%"
        % (
            NB_THREADS,
            MAX_FN_NUM,
            1 / s.min_interval_time,
            100 * exectime_per_collect / s.min_interval_time,
        )
    )
    for t in threads:
        t.join()


def test_stress_threads_run_as_thread():
    NB_THREADS = 40

    threads = []
    for i in range(NB_THREADS):
        t = threading.Thread(target=_f0)  # noqa: E149,F821
        t.start()
        threads.append(t)

    r = recorder.Recorder()
    s = stack.StackCollector(recorder=r)
    # This mainly check nothing bad happens when we collect a lot of threads and store the result in the Recorder
    with s:
        time.sleep(3)
    assert r.events[stack.StackSampleEvent]
    for t in threads:
        t.join()


@pytest.mark.skipif(not stack.FEATURES["stack-exceptions"], reason="Stack exceptions not supported")
@pytest.mark.skipif(TESTING_GEVENT, reason="Test not compatible with gevent")
def test_exception_collection_threads():
    NB_THREADS = 5

    threads = []
    for i in range(NB_THREADS):
        t = threading.Thread(target=_f0)  # noqa: E149,F821
        t.start()
        threads.append(t)

    r, c, thread_id = test_collector._test_collector_collect(stack.StackCollector, stack.StackExceptionSampleEvent)
    exception_events = r.events[stack.StackExceptionSampleEvent]
    e = exception_events[0]
    assert e.timestamp > 0
    assert e.sampling_period > 0
    assert e.thread_id in {t.ident for t in threads}
    assert isinstance(e.thread_name, str)
    assert e.frames == [("<string>", 5, "_f30")]
    assert e.nframes == 1
    assert e.exc_type == ValueError
    for t in threads:
        t.join()


@pytest.mark.skipif(not stack.FEATURES["stack-exceptions"], reason="Stack exceptions not supported")
def test_exception_collection():
    r = recorder.Recorder()
    c = stack.StackCollector(r)
    with c:
        try:
            raise ValueError("hello")
        except Exception:
            nogevent.sleep(1)

    exception_events = r.events[stack.StackExceptionSampleEvent]
    assert len(exception_events) >= 1
    e = exception_events[0]
    assert e.timestamp > 0
    assert e.sampling_period > 0
    assert e.thread_id == nogevent.thread_get_ident()
    assert e.thread_name == "MainThread"
    assert e.frames == [(__file__, 322, "test_exception_collection")]
    assert e.nframes == 1
    assert e.exc_type == ValueError


@pytest.mark.skipif(not stack.FEATURES["stack-exceptions"], reason="Stack exceptions not supported")
def test_exception_collection_trace(tracer):
    r = recorder.Recorder()
    c = stack.StackCollector(r, tracer=tracer)
    with c:
        with tracer.trace("test123") as span:
            try:
                raise ValueError("hello")
            except Exception:
                nogevent.sleep(1)

    exception_events = r.events[stack.StackExceptionSampleEvent]
    assert len(exception_events) >= 1
    e = exception_events[0]
    assert e.timestamp > 0
    assert e.sampling_period > 0
    assert e.thread_id == nogevent.thread_get_ident()
    assert e.thread_name == "MainThread"
    assert e.frames == [(__file__, 345, "test_exception_collection_trace")]
    assert e.nframes == 1
    assert e.exc_type == ValueError
    assert e.span_id == span.span_id
    assert e.trace_id == span.trace_id


@pytest.fixture
def tracer_and_collector(tracer):
    r = recorder.Recorder()
    c = stack.StackCollector(r, tracer=tracer)
    c.start()
    try:
        yield tracer, c
    finally:
        c.stop()


def test_thread_to_span_thread_isolation(tracer_and_collector):
    t, c = tracer_and_collector
    root = t.start_span("root", activate=True)
    thread_id = nogevent.thread_get_ident()
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == root

    quit_thread = threading.Event()
    span_started = threading.Event()

    store = {}

    def start_span():
        store["span2"] = t.start_span("thread2", activate=True)
        span_started.set()
        quit_thread.wait()

    th = threading.Thread(target=start_span)
    th.start()
    span_started.wait()
    if TESTING_GEVENT:
        # We track *real* threads, gevent is using only one in this case
        assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == store["span2"]
        assert c._thread_span_links.get_active_span_from_thread_id(th.ident) is None
    else:
        assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == root
        assert c._thread_span_links.get_active_span_from_thread_id(th.ident) == store["span2"]
    # Do not quit the thread before we test, otherwise the collector might clean up the thread from the list of spans
    quit_thread.set()
    th.join()


def test_thread_to_span_multiple(tracer_and_collector):
    t, c = tracer_and_collector
    root = t.start_span("root", activate=True)
    thread_id = nogevent.thread_get_ident()
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == root
    subspan = t.start_span("subtrace", child_of=root, activate=True)
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == subspan
    subspan.finish()
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == root
    root.finish()
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) is None


def test_thread_to_child_span_multiple_unknown_thread(tracer_and_collector):
    t, c = tracer_and_collector
    t.start_span("root", activate=True)
    assert c._thread_span_links.get_active_span_from_thread_id(3456789) is None


def test_thread_to_child_span_clear(tracer_and_collector):
    t, c = tracer_and_collector
    root = t.start_span("root", activate=True)
    thread_id = nogevent.thread_get_ident()
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == root
    c._thread_span_links.clear_threads(set())
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) is None


def test_thread_to_child_span_multiple_more_children(tracer_and_collector):
    t, c = tracer_and_collector
    root = t.start_span("root", activate=True)
    thread_id = nogevent.thread_get_ident()
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == root
    subspan = t.start_span("subtrace", child_of=root, activate=True)
    subsubspan = t.start_span("subsubtrace", child_of=subspan, activate=True)
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == subsubspan
    subsubspan2 = t.start_span("subsubtrace2", child_of=subspan, activate=True)
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == subsubspan2
    # ⚠ subspan is not supposed to finish before its children, but the API authorizes it
    subspan.finish()
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == subsubspan2


def test_collect_span_id(tracer_and_collector):
    t, c = tracer_and_collector
    resource = str(uuid.uuid4())
    span_type = str(uuid.uuid4())
    span = t.start_span("foobar", activate=True, resource=resource, span_type=span_type)
    # This test will run forever if it fails. Don't make it fail.
    while True:
        try:
            event = c.recorder.events[stack.StackSampleEvent].pop()
        except IndexError:
            # No event left or no event yet
            continue
        if span.trace_id == event.trace_id and span.span_id == event.span_id:
            assert event.trace_resource == resource
            assert event.trace_type == span_type
            break


def test_collect_span_resource_after_finish(tracer_and_collector):
    t, c = tracer_and_collector
    resource = str(uuid.uuid4())
    span_type = str(uuid.uuid4())
    span = t.start_span("foobar", activate=True, span_type=span_type)
    # This test will run forever if it fails. Don't make it fail.
    while True:
        try:
            event = c.recorder.events[stack.StackSampleEvent].pop()
        except IndexError:
            # No event left or no event yet
            continue
        if span.trace_id == event.trace_id and span.span_id == event.span_id:
            assert event.trace_resource == "foobar"
            assert event.trace_type == span_type
            break
    span.resource = resource
    span.finish()
    assert event.trace_resource == resource


def test_collect_multiple_span_id(tracer_and_collector):
    t, c = tracer_and_collector
    resource = str(uuid.uuid4())
    span_type = str(uuid.uuid4())
    span = t.start_span("foobar", activate=True, resource=resource, span_type=span_type)
    child = t.start_span("foobar", child_of=span, activate=True)
    # This test will run forever if it fails. Don't make it fail.
    while True:
        try:
            event = c.recorder.events[stack.StackSampleEvent].pop()
        except IndexError:
            # No event left or no event yet
            continue
        if child.trace_id == event.trace_id and child.span_id == event.span_id:
            assert event.trace_resource == resource
            assert event.trace_type == span_type
            break


def test_stress_trace_collection(tracer_and_collector):
    tracer, collector = tracer_and_collector

    def _trace():
        for _ in range(5000):
            with tracer.trace("hello"):
                time.sleep(0.001)

    NB_THREADS = 30

    threads = []
    for i in range(NB_THREADS):
        t = threading.Thread(target=_trace)
        threads.append(t)

    for t in threads:
        t.start()

    for t in threads:
        t.join()


@pytest.mark.skipif(TESTING_GEVENT, reason="Test not compatible with gevent")
def test_thread_time_cache():
    tt = stack._ThreadTime()

    lock = nogevent.Lock()
    lock.acquire()

    t = nogevent.Thread(target=lock.acquire)
    t.start()

    main_thread_id = threading.current_thread().ident

    threads = [
        main_thread_id,
        t.ident,
    ]

    cpu_time = tt(threads)

    assert sorted(k[0] for k in cpu_time.keys()) == sorted([main_thread_id, t.ident])
    assert all(t >= 0 for t in cpu_time.values())

    cpu_time = tt(threads)

    assert sorted(k[0] for k in cpu_time.keys()) == sorted([main_thread_id, t.ident])
    assert all(t >= 0 for t in cpu_time.values())

    if stack.FEATURES["cpu-time"]:
        assert set(tt._get_last_thread_time().keys()) == set(
            (pthread_id, _threading.get_thread_native_id(pthread_id)) for pthread_id in threads
        )

    lock.release()

    threads = {
        main_thread_id: _threading.get_thread_native_id(main_thread_id),
    }

    cpu_time = tt(threads)
    assert sorted(k[0] for k in cpu_time.keys()) == sorted([main_thread_id])
    assert all(t >= 0 for t in cpu_time.values())

    if stack.FEATURES["cpu-time"]:
        assert set(tt._get_last_thread_time().keys()) == set(
            (pthread_id, _threading.get_thread_native_id(pthread_id)) for pthread_id in threads
        )
