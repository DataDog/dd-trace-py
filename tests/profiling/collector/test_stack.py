# -*- encoding: utf-8 -*-
import _thread
import os
import sys
import threading
import time
import timeit
import typing  # noqa:F401

import pytest

import ddtrace  # noqa:F401
from ddtrace.profiling import _threading
from ddtrace.profiling.collector import stack

from . import test_collector


# Python 3.11.9 is not compatible with gevent, https://github.com/gevent/gevent/issues/2040
# https://github.com/python/cpython/issues/117983
# The fix was not backported to 3.11. The fix was first released in 3.12.5 for
# Python 3.12. Tested with Python 3.11.8 and 3.12.5 to confirm the issue.
TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False) and (
    sys.version_info < (3, 11, 9) or sys.version_info >= (3, 12, 5)
)


def func1():
    return func2()


def func2():
    return func3()


def func3():
    return func4()


def func4():
    return func5()


def func5():
    return time.sleep(1)


def _find_sleep_event(events, class_name):
    class_method_found = False
    class_classmethod_found = False

    for e in events:
        for frame in e.frames:
            if frame[0] == __file__.replace(".pyc", ".py") and frame[2] == "sleep_class" and frame[3] == class_name:
                class_method_found = True
            elif (
                frame[0] == __file__.replace(".pyc", ".py") and frame[2] == "sleep_instance" and frame[3] == class_name
            ):
                class_classmethod_found = True

        if class_method_found and class_classmethod_found:
            return True

    return False


def _fib(n):
    if n == 1:
        return 1
    elif n == 0:
        return 0
    else:
        return _fib(n - 1) + _fib(n - 2)


def test_restart():
    test_collector._test_restart(stack.StackCollector)


def test_repr():
    test_collector._test_repr(
        stack.StackCollector,
        "StackCollector(status=<ServiceStatus.STOPPED: 'stopped'>, "
        "min_interval_time=0.01, max_time_usage_pct=1.0, "
        "nframes=64, ignore_profiler=False, endpoint_collection_enabled=None, tracer=None)",
    )


# Function to use for stress-test of polling
MAX_FN_NUM = 30
FN_TEMPLATE = """def _f{num}():
  return _f{nump1}()"""

for num in range(MAX_FN_NUM):
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
    for _ in range(NB_THREADS):
        t = threading.Thread(target=_f0)  # noqa: E149,F821
        t.start()
        threads.append(t)

    s = stack.StackCollector()
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
    for _ in range(NB_THREADS):
        t = threading.Thread(target=_f0)  # noqa: E149,F821
        t.start()
        threads.append(t)

    s = stack.StackCollector()
    # This mainly check nothing bad happens when we collect a lot of threads
    with s:
        time.sleep(3)
    for t in threads:
        t.join()


@pytest.fixture
def tracer_and_collector(tracer):
    c = stack.StackCollector(endpoint_collection_enabled=True, tracer=tracer)
    c.start()
    try:
        yield tracer, c
    finally:
        c.stop()
        tracer.shutdown()


def test_thread_to_span_thread_isolation(tracer_and_collector):
    t, c = tracer_and_collector
    root = t.start_span("root", activate=True)
    thread_id = _thread.get_ident()
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
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == root
    assert c._thread_span_links.get_active_span_from_thread_id(th.ident) == store["span2"]
    # Do not quit the thread before we test, otherwise the collector might clean up the thread from the list of spans
    quit_thread.set()
    th.join()


def test_thread_to_span_multiple(tracer_and_collector):
    t, c = tracer_and_collector
    root = t.start_span("root", activate=True)
    thread_id = _thread.get_ident()
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
    thread_id = _thread.get_ident()
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == root
    c._thread_span_links.clear_threads(set())
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) is None


def test_thread_to_child_span_multiple_more_children(tracer_and_collector):
    t, c = tracer_and_collector
    root = t.start_span("root", activate=True)
    thread_id = _thread.get_ident()
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == root
    subspan = t.start_span("subtrace", child_of=root, activate=True)
    subsubspan = t.start_span("subsubtrace", child_of=subspan, activate=True)
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == subsubspan
    subsubspan2 = t.start_span("subsubtrace2", child_of=subspan, activate=True)
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == subsubspan2
    # ⚠ subspan is not supposed to finish before its children, but the API authorizes it
    subspan.finish()
    assert c._thread_span_links.get_active_span_from_thread_id(thread_id) == subsubspan2


def test_stress_trace_collection(tracer_and_collector):
    tracer, collector = tracer_and_collector

    def _trace():
        for _ in range(5000):
            with tracer.trace("hello"):
                time.sleep(0.001)

    NB_THREADS = 30

    threads = []
    for _ in range(NB_THREADS):
        t = threading.Thread(target=_trace)
        threads.append(t)

    for t in threads:
        t.start()

    for t in threads:
        t.join()


def test_thread_time_cache():
    tt = stack._ThreadTime()

    lock = threading.Lock()
    lock.acquire()

    t = threading.Thread(target=lock.acquire)
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
