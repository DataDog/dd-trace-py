# -*- encoding: utf-8 -*-
import _thread
import os
import sys
import threading
import time
import uuid

import pytest

from ddtrace import ext
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import stack
from tests.conftest import get_original_test_name
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

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert len(samples) > 0
    for sample in samples:
        assert len(sample.location_id) <= max_nframes, len(sample.location_id)


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


@pytest.mark.skipif(not TESTING_GEVENT, reason="Not testing gevent")
@pytest.mark.subprocess(ddtrace_run=True)
def test_collect_gevent_thread_task():
    from gevent import monkey  # noqa:F401

    monkey.patch_all()

    import os
    import threading
    import time

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

    profile = pprof_utils.parse_newest_profile(output_filename)
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


def test_repr():
    test_collector._test_repr(
        stack.StackCollector,
        "StackCollector(status=<ServiceStatus.STOPPED: 'stopped'>, nframes=64, tracer=None)",
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
    test_name = get_original_test_name(request)
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
    test_name = get_original_test_name(request)
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    tracer._endpoint_call_counter_span_processor.enable()
    with stack.StackCollector(tracer=tracer):
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

    with stack.StackCollector(tracer=tracer):
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
    test_name = get_original_test_name(request)
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    tracer._endpoint_call_counter_span_processor.enable()
    with stack.StackCollector(tracer=tracer):
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


@pytest.mark.skipif(not TESTING_GEVENT or sys.version_info < (3, 9), reason="Not testing gevent")
@pytest.mark.subprocess(ddtrace_run=True)
def test_collect_gevent_threads():
    import gevent.monkey

    gevent.monkey.patch_all()

    import os
    import threading
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    iteration = 100
    sleep_time = 0.01
    nb_threads = 15

    # Start some greenthreads: they do nothing we just keep switching between them.
    def _nothing():
        for _ in range(iteration):
            # Do nothing and just switch to another greenlet
            time.sleep(sleep_time)

    test_name = "test_collect_gevent_threads"
    pprof_prefix = "/tmp/" + test_name
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service="test_collect_gevent_threads", version="my_version", output_filename=pprof_prefix)
    ddup.start()

    with stack.StackCollector():
        threads = []
        i_to_tid = {}
        for i in range(nb_threads):
            t = threading.Thread(target=_nothing, name="TestThread %d" % i)
            i_to_tid[i] = t.ident
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    for task_id in range(nb_threads):
        pprof_utils.assert_profile_has_sample(
            profile,
            samples,
            expected_sample=pprof_utils.StackEvent(
                task_name="TestThread %d" % task_id,
                task_id=i_to_tid[task_id],
                thread_id=i_to_tid[task_id],
                locations=[
                    pprof_utils.StackLocation(
                        filename="test_stack.py",
                        function_name="_nothing",
                        line_no=_nothing.__code__.co_firstlineno + 3,
                    )
                ],
            ),
        )
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            task_name="MainThread",
        ),
    )
