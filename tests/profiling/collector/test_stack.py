# -*- encoding: utf-8 -*-
import _thread
import os
import sys
import threading
import time
import timeit
import typing  # noqa:F401
import uuid

import pytest

import ddtrace  # noqa:F401
from ddtrace import ext
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import stack
from tests.profiling.collector import pprof_utils

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

    profile = pprof_utils.parse_newest_profile(output_filename)
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

    profile = pprof_utils.parse_newest_profile(output_filename)
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

    profile = pprof_utils.parse_newest_profile(output_filename)
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


def test_max_time_usage():
    with pytest.raises(ValueError):
        stack.StackCollector(max_time_usage_pct=0)


def test_max_time_usage_over():
    with pytest.raises(ValueError):
        stack.StackCollector(max_time_usage_pct=200)


# def test_collect():
#     test_collector._test_collector_collect(stack.StackCollector, stack_event.StackSampleEvent)


# def test_restart():
#     test_collector._test_restart(stack.StackCollector)


def test_repr():
    test_collector._test_repr(
        stack.StackCollector,
        "StackCollector(status=<ServiceStatus.STOPPED: 'stopped'>, "
        "min_interval_time=0.01, max_time_usage_pct=1.0, "
        "nframes=64, endpoint_collection_enabled=None, tracer=None)",
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

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0


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

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0


# if you don't need to check the output profile, you can use this fixture
@pytest.fixture
def tracer_and_collector(tracer, request, tmp_path):
    test_name = request.node.name
    pprof_prefix = str(tmp_path / test_name)

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    c = stack.StackCollector(tracer=tracer)
    c.start()
    try:
        yield tracer, c
    finally:
        c.stop()
        ddup.upload(tracer=tracer)


def test_collect_span_id(tracer, tmp_path):
    test_name = "test_collect_span_id"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    tracer._endpoint_call_counter_span_processor.enable()
    with stack.StackCollector(tracer=tracer):
        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.WEB
        with tracer.start_span("foobar", activate=True, resource=resource, span_type=span_type) as span:
            for _ in range(10):
                time.sleep(0.1)
            span_id = span.span_id
            local_root_span_id = span._local_root.span_id

    ddup.upload(tracer=tracer)

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "trace endpoint")
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            span_id=span_id,
            trace_type=span_type,
            local_root_span_id=local_root_span_id,
            trace_endpoint=resource,
            locations=[
                pprof_utils.StackLocation(
                    filename=os.path.basename(__file__),
                    function_name=test_name,
                    line_no=test_collect_span_id.__code__.co_firstlineno + 15,
                )
            ],
        ),
    )


def test_collect_span_resource_after_finish(tracer, tmp_path, request):
    test_name = request.node.name
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    tracer._endpoint_call_counter_span_processor.enable()
    with stack.StackCollector(tracer=tracer, endpoint_collection_enabled=True):
        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.WEB
        span = tracer.start_span("foobar", activate=True, span_type=span_type, resource=resource)
        for _ in range(10):
            time.sleep(0.1)
    ddup.upload(tracer=tracer)
    span.finish()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = profile.sample
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            span_id=span.span_id,
            trace_type=span_type,
            # Looks like the endpoint is not collected if the span is not finished
            # trace_endpoint=resource,
            locations=[
                pprof_utils.StackLocation(
                    filename=os.path.basename(__file__),
                    function_name=test_name,
                    line_no=test_collect_span_resource_after_finish.__code__.co_firstlineno + 14,
                )
            ],
        ),
    )


def test_resource_not_collected(tmp_path, tracer):
    test_name = "test_resource_not_collected"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    with stack.StackCollector(endpoint_collection_enabled=False, tracer=tracer):
        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.WEB
        with tracer.start_span("foobar", activate=True, resource=resource, span_type=span_type) as span:
            _fib(28)

    ddup.upload(tracer=tracer)

    profile = pprof_utils.parse_newest_profile(output_filename)
    pprof_utils.assert_profile_has_sample(
        profile,
        profile.sample,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            span_id=span.span_id,
            trace_type=span_type,
            locations=[
                pprof_utils.StackLocation(
                    filename=os.path.basename(__file__),
                    function_name=test_name,
                    line_no=test_resource_not_collected.__code__.co_firstlineno + 13,
                )
            ],
        ),
    )


def test_collect_nested_span_id(tmp_path, tracer, request):
    test_name = request.node.name
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    tracer._endpoint_call_counter_span_processor.enable()
    with stack.StackCollector(tracer=tracer, endpoint_collection_enabled=True):
        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.WEB
        with tracer.start_span("foobar", activate=True, resource=resource, span_type=span_type):
            with tracer.start_span("foobar", activate=True, resource=resource, span_type=span_type) as child_span:
                for _ in range(10):
                    time.sleep(0.1)
    ddup.upload(tracer=tracer)

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "span id")
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            span_id=child_span.span_id,
            trace_type=span_type,
            local_root_span_id=child_span._local_root.span_id,
            trace_endpoint=resource,
            locations=[
                pprof_utils.StackLocation(
                    filename=os.path.basename(__file__),
                    function_name=test_name,
                    line_no=test_collect_nested_span_id.__code__.co_firstlineno + 16,
                )
            ],
        ),
    )


def test_stress_trace_collection(tracer_and_collector):
    tracer, _ = tracer_and_collector

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
