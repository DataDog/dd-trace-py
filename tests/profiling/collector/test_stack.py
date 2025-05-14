# -*- encoding: utf-8 -*-
import _thread
import gc
import os
import sys
import threading
import time
import timeit
from types import FrameType
import typing  # noqa:F401
import uuid

import pytest

import ddtrace  # noqa:F401
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import _threading
from ddtrace.profiling import recorder
from ddtrace.profiling.collector import stack
from ddtrace.profiling.collector import stack_event
from tests.profiling.collector import pprof_utils
from tests.utils import flaky

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


def wait_for_event(collector, cond=lambda _: True, retries=10, interval=1):
    for _ in range(retries):
        events = list(collector.recorder.events[stack_event.StackSampleEvent])
        matched = list(filter(cond, events))
        if matched:
            return matched[0]

        collector.recorder.events[stack_event.StackSampleEvent].clear()
        time.sleep(interval)

    raise RuntimeError("event wait timeout")


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_MAX_FRAMES="5",
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_collect_truncate",
    )
)
def test_collect_truncate():
    import os

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack import func1

    pprof_prefix = os.environ["DD_PROFILING_OUTPUT_PPROF"]
    output_filename = pprof_prefix + "." + str(os.getpid())

    max_nframes = int(os.environ["DD_PROFILING_MAX_FRAMES"])

    p = profiler.Profiler()
    p.start()

    func1()

    p.stop()

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert len(samples) > 0
    for sample in samples:
        assert len(sample.location_id) <= max_nframes, len(sample.location_id)


def test_collect_once(tmp_path):
    test_name = "test_collect_once"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())
    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    s = stack.StackCollector()
    s._init()
    all_events = s.collect()

    ddup.upload()
    # assert len(all_events) == 0
    assert len(all_events) == 2

    stack_events = all_events[0]
    exc_events = all_events[1]
    assert len(stack_events) == 0
    assert len(exc_events) == 0

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert len(samples) > 0


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


def test_collect_once_with_class(tmp_path):
    class SomeClass(object):
        @classmethod
        def sleep_class(cls):
            return cls().sleep_instance()

        def sleep_instance(self):
            for _ in range(10):
                time.sleep(0.1)

    test_name = "test_collect_once_with_class"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    with stack.StackCollector():
        SomeClass.sleep_class()

    ddup.upload()

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[
                pprof_utils.StackLocation(
                    function_name="sleep_instance",
                    filename="test_stack.py",
                    line_no=SomeClass.sleep_instance.__code__.co_firstlineno + 2,
                ),
                pprof_utils.StackLocation(
                    function_name="sleep_class",
                    filename="test_stack.py",
                    line_no=SomeClass.sleep_class.__code__.co_firstlineno + 2,
                ),
                pprof_utils.StackLocation(
                    function_name="test_collect_once_with_class",
                    filename="test_stack.py",
                    line_no=test_collect_once_with_class.__code__.co_firstlineno + 19,
                ),
            ],
        ),
    )


def test_collect_once_with_class_not_right_type(tmp_path):
    class SomeClass(object):
        @classmethod
        def sleep_class(foobar, cls):
            return foobar().sleep_instance(cls)

        def sleep_instance(foobar, self):
            for _ in range(10):
                time.sleep(0.1)

    test_name = "test_collect_once_with_class"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    with stack.StackCollector():
        SomeClass.sleep_class(123)

    ddup.upload()

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[
                pprof_utils.StackLocation(
                    function_name="sleep_instance",
                    filename="test_stack.py",
                    line_no=SomeClass.sleep_instance.__code__.co_firstlineno + 2,
                ),
                pprof_utils.StackLocation(
                    function_name="sleep_class",
                    filename="test_stack.py",
                    line_no=SomeClass.sleep_class.__code__.co_firstlineno + 2,
                ),
                pprof_utils.StackLocation(
                    function_name="test_collect_once_with_class_not_right_type",
                    filename="test_stack.py",
                    line_no=test_collect_once_with_class_not_right_type.__code__.co_firstlineno + 19,
                ),
            ],
        ),
    )


def _fib(n):
    if n == 1:
        return 1
    elif n == 0:
        return 0
    else:
        return _fib(n - 1) + _fib(n - 2)


@pytest.mark.skipif(not TESTING_GEVENT, reason="Not testing gevent")
@pytest.mark.subprocess(ddtrace_run=True)
def test_collect_gevent_thread_task():
    from gevent import monkey  # noqa:F401

    monkey.patch_all()

    import os
    import threading
    import time

    import pytest

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack import _fib

    test_name = "test_collect_gevent_thread_task"
    pprof_prefix = "/tmp/" + test_name
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    # Start some (green)threads
    def _dofib():
        for _ in range(10):
            # spend some time in CPU so the profiler can catch something
            _fib(28)
            # Just make sure gevent switches threads/greenlets
            time.sleep(0)

    threads = []
    with stack.StackCollector():
        for i in range(10):
            t = threading.Thread(target=_dofib, name="TestThread %d" % i)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    ddup.upload()

    expected_task_ids = {thread.ident for thread in threads}

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "task id")
    assert len(samples) > 0

    checked_thread = False

    for sample in samples:
        task_id_label = pprof_utils.get_label_with_key(profile.string_table, sample, "task id")
        task_id = int(task_id_label.num)
        if task_id in expected_task_ids:
            pprof_utils.assert_stack_event(
                profile,
                sample,
                pprof_utils.StackEvent(
                    task_name=r"TestThread \d+$",
                    task_id=task_id,
                ),
            )
            checked_thread = True

    assert checked_thread, "No samples found for the expected threads"


def test_max_time_usage():
    r = recorder.Recorder()
    with pytest.raises(ValueError):
        stack.StackCollector(r, max_time_usage_pct=0)


def test_max_time_usage_over():
    r = recorder.Recorder()
    with pytest.raises(ValueError):
        stack.StackCollector(r, max_time_usage_pct=200)


@pytest.mark.parametrize("ignore_profiler", [True, False])
def test_ignore_profiler(tmp_path, ignore_profiler):
    test_name = "test_ignore_profiler"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    s = stack.StackCollector(ignore_profiler=ignore_profiler)
    collector_worker_thread_id = None

    with s:
        for _ in range(10):
            time.sleep(0.1)
        collector_worker_thread_id = s._worker.ident

    ddup.upload()

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "thread id")

    thread_ids = set()

    for sample in samples:
        thread_id_label = pprof_utils.get_label_with_key(profile.string_table, sample, "thread id")
        thread_id = int(thread_id_label.num)
        thread_ids.add(thread_id)

    if ignore_profiler:
        assert collector_worker_thread_id not in thread_ids, (collector_worker_thread_id, thread_ids)
    else:
        assert collector_worker_thread_id in thread_ids, (collector_worker_thread_id, thread_ids)


@pytest.mark.skipif(not TESTING_GEVENT, reason="Not testing gevent")
@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        DD_PROFILING_IGNORE_PROFILER="1",
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_ignore_profiler_gevent_task",
    ),
)
def test_ignore_profiler_gevent_task():
    import gevent.monkey

    gevent.monkey.patch_all()

    import os
    import threading
    import time
    import typing

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling import collector
    from ddtrace.profiling import event as event_mod
    from ddtrace.profiling import profiler
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack import _fib

    test_name = "test_ignore_profiler_gevent_task"
    pprof_prefix = os.environ["DD_PROFILING_OUTPUT_PPROF"]
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    s = stack.StackCollector()
    collector_worker_thread_id = None

    with s:
        for _ in range(10):
            time.sleep(0.1)
        collector_worker_thread_id = s._worker.ident

    ddup.upload()

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "thread id")

    thread_ids = set()

    for sample in samples:
        thread_id_label = pprof_utils.get_label_with_key(profile.string_table, sample, "thread id")
        thread_id = int(thread_id_label.num)
        thread_ids.add(thread_id)

    assert collector_worker_thread_id not in thread_ids, (collector_worker_thread_id, thread_ids)


# def test_collect():
#     test_collector._test_collector_collect(stack.StackCollector, stack_event.StackSampleEvent)


# def test_restart():
#     test_collector._test_restart(stack.StackCollector)


def test_repr():
    test_collector._test_repr(
        stack.StackCollector,
        "StackCollector(status=<ServiceStatus.STOPPED: 'stopped'>, "
        "recorder=Recorder(default_max_events=16384, max_events={}), min_interval_time=0.01, max_time_usage_pct=1.0, "
        "nframes=64, ignore_profiler=False, endpoint_collection_enabled=None, tracer=None)",
    )


def test_new_interval():
    c = stack.StackCollector(max_time_usage_pct=2)
    new_interval = c._compute_new_interval(1000000)
    assert new_interval == 0.049
    new_interval = c._compute_new_interval(2000000)
    assert new_interval == 0.098
    c = stack.StackCollector(max_time_usage_pct=10)
    new_interval = c._compute_new_interval(200000)
    assert new_interval == 0.01
    new_interval = c._compute_new_interval(1)
    assert new_interval == c.min_interval_time


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


def test_stress_threads(tmp_path):
    test_name = "test_stress_threads"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    with stack.StackCollector() as s:
        NB_THREADS = 40

        threads = []
        for _ in range(NB_THREADS):
            t = threading.Thread(target=_f0)  # noqa: E149,F821
            t.start()
            threads.append(t)
        number = 20000

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

    ddup.upload()

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")


def test_stress_threads_run_as_thread(tmp_path):
    test_name = "test_stress_threads_run_as_thread"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    quit_thread = threading.Event()

    def wait_for_quit():
        quit_thread.wait()

    with stack.StackCollector():
        NB_THREADS = 40

        threads = []
        for _ in range(NB_THREADS):
            t = threading.Thread(target=wait_for_quit)
            t.start()
            threads.append(t)

        time.sleep(3)

        quit_thread.set()
        for t in threads:
            t.join()

    ddup.upload()

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0


@pytest.mark.skipif(not stack.FEATURES["stack-exceptions"], reason="Stack exceptions not supported")
def test_exception_collection_threads(tmp_path):
    test_name = "test_exception_collection_threads"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    with stack.StackCollector():
        NB_THREADS = 5
        threads = []
        for _ in range(NB_THREADS):
            t = threading.Thread(target=_f0)  # noqa: E149,F821
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

    ddup.upload()

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0

    # r, c, thread_id = test_collector._test_collector_collect(
    #     stack.StackCollector, stack_event.StackExceptionSampleEvent
    # )
    # exception_events = r.events[stack_event.StackExceptionSampleEvent]
    # e = exception_events[0]
    # assert e.sampling_period > 0
    # assert e.thread_id in {t.ident for t in threads}
    # assert isinstance(e.thread_name, str)
    # assert e.frames == [("<string>", 5, "_f30", "")]
    # assert e.nframes == 1
    # assert e.exc_type == ValueError
    # for t in threads:
    #     t.join()


@pytest.mark.skipif(not stack.FEATURES["stack-exceptions"], reason="Stack exceptions not supported")
def test_exception_collection():
    r = recorder.Recorder()
    c = stack.StackCollector(r)
    with c:
        try:
            raise ValueError("hello")
        except Exception:
            time.sleep(1)

    exception_events = r.events[stack_event.StackExceptionSampleEvent]
    assert len(exception_events) >= 1
    e = exception_events[0]
    assert e.sampling_period > 0
    assert e.thread_id == _thread.get_ident()
    assert e.thread_name == "MainThread"
    assert e.frames == [
        (__file__, test_exception_collection.__code__.co_firstlineno + 8, "test_exception_collection", "")
    ]
    assert e.nframes == 1
    assert e.exc_type == ValueError


@pytest.mark.skipif(not stack.FEATURES["stack-exceptions"], reason="Stack exceptions not supported")
def test_exception_collection_trace(
    tracer,  # type: ddtrace.trace.Tracer
):
    # type: (...) -> None
    r = recorder.Recorder()
    c = stack.StackCollector(r, tracer=tracer)
    with c:
        with tracer.trace("test123") as span:
            for _ in range(100):
                try:
                    raise ValueError("hello")
                except Exception:
                    time.sleep(1)

                # Check we caught an event or retry
                exception_events = r.reset()[stack_event.StackExceptionSampleEvent]
                if len(exception_events) >= 1:
                    break
            else:
                pytest.fail("No exception event found")

    e = exception_events[0]
    assert e.sampling_period > 0
    assert e.thread_id == _thread.get_ident()
    assert e.thread_name == "MainThread"
    assert e.frames == [
        (__file__, test_exception_collection_trace.__code__.co_firstlineno + 13, "test_exception_collection_trace", "")
    ]
    assert e.nframes == 1
    assert e.exc_type == ValueError
    assert e.span_id == span.span_id
    assert e.local_root_span_id == span._local_root.span_id


@pytest.fixture
def tracer_and_collector(tracer):
    r = recorder.Recorder()
    c = stack.StackCollector(r, endpoint_collection_enabled=True, tracer=tracer)
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


def test_collect_span_id(tracer_and_collector):
    t, c = tracer_and_collector
    resource = str(uuid.uuid4())
    span_type = str(uuid.uuid4())
    span = t.start_span("foobar", activate=True, resource=resource, span_type=span_type)
    event = wait_for_event(c, lambda e: span.span_id == e.span_id)
    assert span.span_id == event.span_id
    assert event.trace_resource_container[0] == resource
    assert event.trace_type == span_type
    assert event.local_root_span_id == span._local_root.span_id


def test_collect_span_resource_after_finish(tracer_and_collector):
    t, c = tracer_and_collector
    resource = str(uuid.uuid4())
    span_type = str(uuid.uuid4())
    span = t.start_span("foobar", activate=True, span_type=span_type)
    event = wait_for_event(c, lambda e: span.span_id == e.span_id)
    assert span.span_id == event.span_id
    assert event.trace_resource_container[0] == "foobar"
    assert event.trace_type == span_type
    span.resource = resource
    span.finish()
    assert event.trace_resource_container[0] == resource


def test_resource_not_collected(monkeypatch, tracer):
    r = recorder.Recorder()
    collector = stack.StackCollector(r, endpoint_collection_enabled=False, tracer=tracer)
    collector.start()
    try:
        resource = str(uuid.uuid4())
        span_type = str(uuid.uuid4())
        span = tracer.start_span("foobar", activate=True, resource=resource, span_type=span_type)
        event = wait_for_event(collector, lambda e: span.span_id == e.span_id)
        assert span.span_id == event.span_id
        assert event.trace_resource_container is None
        assert event.trace_type == span_type
    finally:
        collector.stop()


def test_collect_multiple_span_id(tracer_and_collector):
    t, c = tracer_and_collector
    resource = str(uuid.uuid4())
    span_type = str(uuid.uuid4())
    span = t.start_span("foobar", activate=True, resource=resource, span_type=span_type)
    child = t.start_span("foobar", child_of=span, activate=True)
    event = wait_for_event(c, lambda e: child.span_id == e.span_id)
    assert event.trace_resource_container[0] == resource
    assert event.trace_type == span_type


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


@pytest.mark.skipif(not TESTING_GEVENT or sys.version_info < (3, 9), reason="Not testing gevent")
@pytest.mark.subprocess(ddtrace_run=True)
def test_collect_gevent_threads():
    import gevent.monkey

    gevent.monkey.patch_all()

    import collections
    import threading
    import time

    from ddtrace.profiling import recorder
    from ddtrace.profiling.collector import stack
    from ddtrace.profiling.collector import stack_event

    # type: (...) -> None
    r = recorder.Recorder()
    s = stack.StackCollector(r, max_time_usage_pct=100)

    iteration = 100
    sleep_time = 0.01
    nb_threads = 15

    # Start some greenthreads: they do nothing we just keep switching between them.
    def _nothing():
        for _ in range(iteration):
            # Do nothing and just switch to another greenlet
            time.sleep(sleep_time)

    threads = []
    with s:
        for i in range(nb_threads):
            t = threading.Thread(target=_nothing, name="TestThread %d" % i)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    main_thread_found = False
    sleep_task_found = False
    wall_time_ns_per_thread = collections.defaultdict(lambda: 0)

    events = r.events[stack_event.StackSampleEvent]
    for event in events:
        if event.task_name == "MainThread":
            main_thread_found = True
        elif event.task_id in {t.ident for t in threads}:
            for _filename, _lineno, funcname, _classname in event.frames:
                if funcname in (
                    "_nothing",
                    "sleep",
                ):
                    # Make sure we capture the sleep call and not a gevent hub frame
                    sleep_task_found = True
                    break

            wall_time_ns_per_thread[event.task_id] += event.wall_time_ns

    assert main_thread_found
    assert sleep_task_found

    # sanity check: we don't have duplicate in thread/task ids.
    assert len(wall_time_ns_per_thread) == nb_threads

    # In theory there should be only one value in this set, but due to timing,
    # it's possible one task has less event, so we're not checking the len() of
    # values here.
    values = set(wall_time_ns_per_thread.values())

    # NOTE(jd): I'm disabling this check because it works 90% of the test only.
    # There are some cases where this test is run inside the complete test suite
    # and fails, while it works 100% of the time in its own. Check that the sum
    # of wall time generated for each task is right. Accept a 30% margin though,
    # don't be crazy, we're just doing 5 seconds with a lot of tasks. exact_time
    # = iteration * sleep_time * 1e9 assert (exact_time * 0.7) <= values.pop()
    # <= (exact_time * 1.3)

    assert values.pop() > 0


@flaky(1748750400)
@pytest.mark.skipif(sys.version_info < (3, 11, 0), reason="PyFrameObjects are lazy-created objects in Python 3.11+")
def test_collect_ensure_all_frames_gc():
    # Regression test for memory leak with lazy PyFrameObjects in Python 3.11+
    def _foo():
        pass

    r = recorder.Recorder()
    s = stack.StackCollector(r)

    with s:
        for _ in range(100):
            _foo()

    gc.collect()  # Make sure we don't race with gc when we check frame objects
    # DEV - this is flaky because this line returns `assert 10 == 0` in CI
    assert sum(isinstance(_, FrameType) for _ in gc.get_objects()) == 0
