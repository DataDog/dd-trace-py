# -*- encoding: utf-8 -*-
import os
import threading
import time
import timeit

import pytest

import ddtrace
from ddtrace.vendor import six
from ddtrace.vendor.six.moves import _thread

from ddtrace.profiling import recorder
from ddtrace.profiling.collector import stack

from . import test_collector

TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)

try:
    from gevent import monkey
except ImportError:
    sleep = time.sleep
else:
    sleep = monkey.get_original("time", "sleep")


def func1():
    return func2()


def func2():
    return func3()


def func3():
    return func4()


def func4():
    return func5()


def func5():
    return sleep(1)


def test_collect_truncate():
    r = recorder.Recorder()
    c = stack.StackCollector(r, nframes=5)
    c.start()
    func1()
    while not r.events[stack.StackSampleEvent]:
        pass
    c.stop()
    e = r.events[stack.StackSampleEvent][0]
    assert e.nframes > c.nframes
    assert len(e.frames) == c.nframes


def test_collect_once():
    r = recorder.Recorder()
    s = stack.StackCollector(r)
    # Start the collector as we need to have a start time set
    with s:
        all_events = s.collect()
    assert len(all_events) == 2
    e = all_events[0][0]
    assert e.thread_id > 0
    # Thread name is None with gevent
    assert isinstance(e.thread_name, (str, type(None)))
    assert len(e.frames) >= 1
    assert e.frames[0][0].endswith(".py")
    assert e.frames[0][1] > 0
    assert isinstance(e.frames[0][2], str)


def test_max_time_usage():
    r = recorder.Recorder()
    with pytest.raises(ValueError):
        stack.StackCollector(r, max_time_usage_pct=0)


def test_max_time_usage_over():
    r = recorder.Recorder()
    with pytest.raises(ValueError):
        stack.StackCollector(r, max_time_usage_pct=200)


def test_ignore_profiler():
    r, c, thread_id = test_collector._test_collector_collect(stack.StackCollector, stack.StackSampleEvent)
    events = r.events[stack.StackSampleEvent]
    assert thread_id not in {e.thread_id for e in events}


def test_no_ignore_profiler():
    r, c, thread_id = test_collector._test_collector_collect(
        stack.StackCollector, stack.StackSampleEvent, ignore_profiler=False
    )
    events = r.events[stack.StackSampleEvent]
    assert thread_id in {e.thread_id for e in events}


def test_collect():
    test_collector._test_collector_collect(stack.StackCollector, stack.StackSampleEvent)


def test_restart():
    test_collector._test_restart(stack.StackCollector)


def test_repr():
    test_collector._test_repr(
        stack.StackCollector,
        "StackCollector(status=<ServiceStatus.STOPPED: 'stopped'>, "
        "recorder=Recorder(max_size=49152), max_time_usage_pct=2.0, "
        "nframes=64, ignore_profiler=True, tracer=None)",
    )


def test_new_interval():
    r = recorder.Recorder()
    c = stack.StackCollector(r)
    new_interval = c._compute_new_interval(1000000)
    assert new_interval == 0.049
    new_interval = c._compute_new_interval(2000000)
    assert new_interval == 0.098
    c = stack.StackCollector(r, max_time_usage_pct=10)
    new_interval = c._compute_new_interval(200000)
    assert new_interval == 0.01
    new_interval = c._compute_new_interval(1)
    assert new_interval == c.MIN_INTERVAL_TIME


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
      sleep(2)""".format(
        MAX_FN_NUM=MAX_FN_NUM
    )
)


@pytest.mark.skipif(TESTING_GEVENT, reason="Test not compatible with gevent")
def test_stress_threads():
    NB_THREADS = 20

    threads = []
    for i in range(NB_THREADS):
        t = threading.Thread(target=_f0)  # noqa: E149,F821
        t.start()
        threads.append(t)

    s = stack.StackCollector(recorder=recorder.Recorder())
    number = 10000
    with s:
        exectime = timeit.timeit(s.collect, number=number)
    print("%.3f ms per call" % (1000.0 * exectime / number))
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
    c.start()
    try:
        raise ValueError("hello")
    except Exception:
        sleep(1)
    c.stop()

    exception_events = r.events[stack.StackExceptionSampleEvent]
    assert len(exception_events) >= 1
    e = exception_events[0]
    assert e.timestamp > 0
    assert e.sampling_period > 0
    if not TESTING_GEVENT:
        assert e.thread_id == _thread.get_ident()
        assert e.thread_name == "MainThread"
    assert e.frames == [(__file__, 209, "test_exception_collection")]
    assert e.nframes == 1
    assert e.exc_type == ValueError


@pytest.fixture
def tracer_and_collector():
    t = ddtrace.Tracer()
    r = recorder.Recorder()
    c = stack.StackCollector(r, tracer=t)
    c.start()
    try:
        yield t, c
    finally:
        c.stop()


def test_thread_to_span_thread_isolation(tracer_and_collector):
    t, c = tracer_and_collector
    root = t.start_span("root")
    thread_id = stack._thread_get_ident()
    assert c._thread_span_links.get_active_leaf_spans_from_thread_id(thread_id) == {root}

    store = {}

    def start_span():
        store["span2"] = t.start_span("thread2")

    th = threading.Thread(target=start_span)
    th.start()
    th.join()
    if TESTING_GEVENT:
        # We track *real* threads, gevent is using only one in this case
        assert c._thread_span_links.get_active_leaf_spans_from_thread_id(thread_id) == {root, store["span2"]}
        assert c._thread_span_links.get_active_leaf_spans_from_thread_id(th.ident) == set()
    else:
        assert c._thread_span_links.get_active_leaf_spans_from_thread_id(thread_id) == {root}
        assert c._thread_span_links.get_active_leaf_spans_from_thread_id(th.ident) == {store["span2"]}


def test_thread_to_span_multiple(tracer_and_collector):
    t, c = tracer_and_collector
    root = t.start_span("root")
    thread_id = stack._thread_get_ident()
    assert c._thread_span_links.get_active_leaf_spans_from_thread_id(thread_id) == {root}
    subspan = t.start_span("subtrace", child_of=root)
    assert c._thread_span_links.get_active_leaf_spans_from_thread_id(thread_id) == {subspan}
    subspan.finish()
    assert c._thread_span_links.get_active_leaf_spans_from_thread_id(thread_id) == {root}
    root.finish()
    assert c._thread_span_links.get_active_leaf_spans_from_thread_id(thread_id) == set()


def test_thread_to_child_span_multiple_unknown_thread(tracer_and_collector):
    t, c = tracer_and_collector
    t.start_span("root")
    assert c._thread_span_links.get_active_leaf_spans_from_thread_id(3456789) == set()


def test_thread_to_child_span_clear(tracer_and_collector):
    t, c = tracer_and_collector
    root = t.start_span("root")
    thread_id = stack._thread_get_ident()
    assert c._thread_span_links.get_active_leaf_spans_from_thread_id(thread_id) == {root}
    c._thread_span_links.clear_threads(set())
    assert c._thread_span_links.get_active_leaf_spans_from_thread_id(thread_id) == set()


def test_thread_to_child_span_multiple_more_children(tracer_and_collector):
    t, c = tracer_and_collector
    root = t.start_span("root")
    thread_id = stack._thread_get_ident()
    assert c._thread_span_links.get_active_leaf_spans_from_thread_id(thread_id) == {root}
    subspan = t.start_span("subtrace", child_of=root)
    subsubspan = t.start_span("subsubtrace", child_of=subspan)
    assert c._thread_span_links.get_active_leaf_spans_from_thread_id(thread_id) == {subsubspan}
    subsubspan2 = t.start_span("subsubtrace2", child_of=subspan)
    assert c._thread_span_links.get_active_leaf_spans_from_thread_id(thread_id) == {subsubspan, subsubspan2}
    # ⚠ subspan is not supposed to finish before its children, but the API authorizes it
    # In that case, we would return also the root span as it's becoming a parent without children 🤷
    subspan.finish()
    assert c._thread_span_links.get_active_leaf_spans_from_thread_id(thread_id) == {root, subsubspan, subsubspan2}


def test_collect_span_ids(tracer_and_collector):
    t, c = tracer_and_collector
    span = t.start_span("foobar")
    # This test will run forever if it fails. Don't make it fail.
    while True:
        try:
            event = c.recorder.events[stack.StackSampleEvent].pop()
        except IndexError:
            # No event left or no event yet
            continue
        if span.trace_id in event.trace_ids:
            break


def test_collect_multiple_span_ids(tracer_and_collector):
    t, c = tracer_and_collector
    span = t.start_span("foobar")
    child = t.start_span("foobar", child_of=span)
    # This test will run forever if it fails. Don't make it fail.
    while True:
        try:
            event = c.recorder.events[stack.StackSampleEvent].pop()
        except IndexError:
            # No event left or no event yet
            continue
        if child.trace_id in event.trace_ids:
            break
