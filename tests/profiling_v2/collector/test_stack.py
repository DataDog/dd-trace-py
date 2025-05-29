import _thread
import os
import sys
import threading
import time
from unittest.mock import patch
import uuid

import pytest

from ddtrace import ext
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import stack
from tests.profiling.collector import pprof_utils
from tests.profiling.collector import test_collector


# Python 3.11.9 is not compatible with gevent, https://github.com/gevent/gevent/issues/2040
# https://github.com/python/cpython/issues/117983
# The fix was not backported to 3.11. The fix was first released in 3.12.5 for
# Python 3.12. Tested with Python 3.11.8 and 3.12.5 to confirm the issue.
TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False) and (
    sys.version_info < (3, 11, 9) or sys.version_info >= (3, 12, 5)
)


# Use subprocess as ddup config persists across tests.
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
        # stack v2 adds one extra frame for "%d frames omitted" message
        # Also, it allows max_nframes + 1 frames, so we add 2 here.
        assert len(sample.location_id) <= max_nframes + 2, len(sample.location_id)


def test_stack_locations(tmp_path):
    test_name = "test_stack_locations"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version")
    ddup.start()

    def baz():
        time.sleep(0.1)

    def bar():
        baz()

    def foo():
        bar()

    with stack.StackCollector(_stack_collector_v2_enabled=True):
        for _ in range(10):
            foo()
    ddup.upload(output_filename=pprof_prefix)

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert len(samples) > 0

    expected_sample = pprof_utils.StackEvent(
        thread_id=_thread.get_ident(),
        thread_name="MainThread",
        locations=[
            pprof_utils.StackLocation(
                function_name="baz",
                filename="test_stack.py",
                line_no=baz.__code__.co_firstlineno + 1,
            ),
            pprof_utils.StackLocation(
                function_name="bar",
                filename="test_stack.py",
                line_no=bar.__code__.co_firstlineno + 1,
            ),
            pprof_utils.StackLocation(
                function_name="foo",
                filename="test_stack.py",
                line_no=foo.__code__.co_firstlineno + 1,
            ),
        ],
    )

    pprof_utils.assert_profile_has_sample(profile, samples=samples, expected_sample=expected_sample)


def test_push_span(tmp_path, tracer):
    test_name = "test_push_span"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    tracer._endpoint_call_counter_span_processor.enable()

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version")
    ddup.start()

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    with stack.StackCollector(
        tracer=tracer,
        endpoint_collection_enabled=True,
        ignore_profiler=True,  # this is not necessary, but it's here to trim samples
        _stack_collector_v2_enabled=True,
    ):
        with tracer.trace("foobar", resource=resource, span_type=span_type) as span:
            span_id = span.span_id
            local_root_span_id = span._local_root.span_id
            for _ in range(10):
                time.sleep(0.1)
    ddup.upload(tracer=tracer, output_filename=pprof_prefix)

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "span id")
    assert len(samples) > 0
    for sample in samples:
        pprof_utils.assert_stack_event(
            profile,
            sample,
            expected_event=pprof_utils.StackEvent(
                span_id=span_id,
                local_root_span_id=local_root_span_id,
                trace_type=span_type,
                trace_endpoint=resource,
            ),
        )


def test_push_span_unregister_thread(tmp_path, monkeypatch, tracer):
    with patch("ddtrace.internal.datadog.profiling.stack_v2.unregister_thread") as unregister_thread:
        tracer._endpoint_call_counter_span_processor.enable()

        test_name = "test_push_span_unregister_thread"
        pprof_prefix = str(tmp_path / test_name)
        output_filename = pprof_prefix + "." + str(os.getpid())

        assert ddup.is_available
        ddup.config(env="test", service=test_name, version="my_version")
        ddup.start()

        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.WEB

        def target_fun():
            for _ in range(10):
                time.sleep(0.1)

        with stack.StackCollector(
            tracer=tracer,
            endpoint_collection_enabled=True,
            ignore_profiler=True,  # this is not necessary, but it's here to trim samples
            _stack_collector_v2_enabled=True,
        ):
            with tracer.trace("foobar", resource=resource, span_type=span_type) as span:
                span_id = span.span_id
                local_root_span_id = span._local_root.span_id
                t = threading.Thread(target=target_fun)
                t.start()
                t.join()
                thread_id = t.ident
        ddup.upload(tracer=tracer, output_filename=pprof_prefix)

        profile = pprof_utils.parse_profile(output_filename)
        samples = pprof_utils.get_samples_with_label_key(profile, "span id")
        assert len(samples) > 0
        for sample in samples:
            pprof_utils.assert_stack_event(
                profile,
                sample,
                expected_event=pprof_utils.StackEvent(
                    span_id=span_id,
                    local_root_span_id=local_root_span_id,
                    trace_type=span_type,
                    trace_endpoint=resource,
                ),
            )

        unregister_thread.assert_called_with(thread_id)


def test_push_non_web_span(tmp_path, tracer):
    tracer._endpoint_call_counter_span_processor.enable()

    test_name = "test_push_non_web_span"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version")
    ddup.start()

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.SQL

    with stack.StackCollector(
        tracer=tracer,
        endpoint_collection_enabled=True,
        ignore_profiler=True,  # this is not necessary, but it's here to trim samples
        _stack_collector_v2_enabled=True,
    ):
        with tracer.trace("foobar", resource=resource, span_type=span_type) as span:
            span_id = span.span_id
            local_root_span_id = span._local_root.span_id
            for _ in range(10):
                time.sleep(0.1)
    ddup.upload(tracer=tracer, output_filename=pprof_prefix)

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "span id")
    assert len(samples) > 0
    for sample in samples:
        pprof_utils.assert_stack_event(
            profile,
            sample,
            expected_event=pprof_utils.StackEvent(
                span_id=span_id,
                local_root_span_id=local_root_span_id,
                trace_type=span_type,
                # trace_endpoint is not set for non-web spans
            ),
        )


def test_push_span_none_span_type(tmp_path, tracer):
    # Test for https://github.com/DataDog/dd-trace-py/issues/11141
    test_name = "test_push_span_none_span_type"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version")
    ddup.start()

    tracer._endpoint_call_counter_span_processor.enable()

    resource = str(uuid.uuid4())

    with stack.StackCollector(
        tracer=tracer,
        endpoint_collection_enabled=True,
        ignore_profiler=True,  # this is not necessary, but it's here to trim samples
        _stack_collector_v2_enabled=True,
    ):
        # Explicitly set None span_type as the default could change in the
        # future.
        with tracer.trace("foobar", resource=resource, span_type=None) as span:
            span_id = span.span_id
            local_root_span_id = span._local_root.span_id
            for _ in range(10):
                time.sleep(0.1)
    ddup.upload(tracer=tracer, output_filename=pprof_prefix)

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "span id")
    assert len(samples) > 0
    for sample in samples:
        pprof_utils.assert_stack_event(
            profile,
            sample,
            expected_event=pprof_utils.StackEvent(
                span_id=span_id,
                local_root_span_id=local_root_span_id,
                # span_type is None
                # trace_endpoint is not set for non-web spans
            ),
        )


@pytest.mark.skipif(not stack.FEATURES["stack-exceptions"], reason="Stack exceptions are not supported")
@pytest.mark.parametrize("stack_v2_enabled", [True, False])
def test_exception_collection(stack_v2_enabled, tmp_path):
    test_name = "test_exception_collection"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version")
    ddup.start()

    with stack.StackCollector(ignore_profiler=True, _stack_collector_v2_enabled=stack_v2_enabled):
        try:
            raise ValueError("hello")
        except Exception:
            time.sleep(1)

    ddup.upload(output_filename=pprof_prefix)

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "exception type")

    if stack_v2_enabled:
        # DEV: update the test once we have exception profiling for stack v2
        # using echion
        assert len(samples) == 0
    else:
        assert len(samples) > 0
        for sample in samples:
            pprof_utils.assert_stack_event(
                profile,
                sample,
                expected_event=pprof_utils.StackEvent(
                    thread_id=_thread.get_ident(),
                    thread_name="MainThread",
                    exception_type="builtins.ValueError",
                    locations=[
                        pprof_utils.StackLocation(
                            filename="test_stack.py",
                            function_name="test_exception_collection",
                            line_no=test_exception_collection.__code__.co_firstlineno + 15,
                        ),
                    ],
                ),
            )


@pytest.mark.skipif(not stack.FEATURES["stack-exceptions"], reason="Stack exceptions are not supported")
@pytest.mark.parametrize("stack_v2_enabled", [True, False])
def test_exception_collection_threads(stack_v2_enabled, tmp_path):
    test_name = "test_exception_collection_threads"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version")
    ddup.start()

    with stack.StackCollector(ignore_profiler=True, _stack_collector_v2_enabled=stack_v2_enabled):

        def target_fun():
            try:
                raise ValueError("hello")
            except Exception:
                time.sleep(1)

        threads = []
        for _ in range(10):
            t = threading.Thread(target=target_fun)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

    ddup.upload(output_filename=pprof_prefix)

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "exception type")

    if stack_v2_enabled:
        assert len(samples) == 0
    else:
        assert len(samples) > 0
        for sample in samples:
            thread_id_label = pprof_utils.get_label_with_key(profile.string_table, sample, "thread id")
            thread_id = int(thread_id_label.num)
            assert thread_id in [t.ident for t in threads]

            pprof_utils.assert_stack_event(
                profile,
                sample,
                expected_event=pprof_utils.StackEvent(
                    exception_type="builtins.ValueError",
                    thread_name=r"Thread-\d+ \(target_fun\)" if sys.version_info[:2] > (3, 9) else r"Thread-\d+",
                    locations=[
                        pprof_utils.StackLocation(
                            filename="test_stack.py",
                            function_name="target_fun",
                            line_no=target_fun.__code__.co_firstlineno + 4,
                        ),
                    ],
                ),
            )


@pytest.mark.skipif(not stack.FEATURES["stack-exceptions"], reason="Stack exceptions are not supported")
@pytest.mark.parametrize("stack_v2_enabled", [True, False])
def test_exception_collection_trace(stack_v2_enabled, tmp_path, tracer):
    test_name = "test_exception_collection_trace"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    tracer._endpoint_call_counter_span_processor.enable()

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version")
    ddup.start()

    with stack.StackCollector(tracer=tracer, ignore_profiler=True, _stack_collector_v2_enabled=stack_v2_enabled):
        with tracer.trace("foobar", resource="resource", span_type=ext.SpanTypes.WEB):
            try:
                raise ValueError("hello")
            except Exception:
                time.sleep(1)

    ddup.upload(tracer=tracer, output_filename=pprof_prefix)

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "exception type")

    if stack_v2_enabled:
        assert len(samples) == 0
    else:
        assert len(samples) > 0
        for sample in samples:
            pprof_utils.assert_stack_event(
                profile,
                sample,
                expected_event=pprof_utils.StackEvent(
                    thread_id=_thread.get_ident(),
                    thread_name="MainThread",
                    exception_type="builtins.ValueError",
                    trace_type=ext.SpanTypes.WEB,
                    trace_endpoint="resource",
                    locations=[
                        pprof_utils.StackLocation(
                            filename="test_stack.py",
                            function_name="test_exception_collection_trace",
                            line_no=test_exception_collection_trace.__code__.co_firstlineno + 18,
                        ),
                    ],
                ),
            )


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
    ddup.config(env="test", service=test_name, version="my_version")
    ddup.start()

    with stack.StackCollector(ignore_profiler=True, _stack_collector_v2_enabled=True):
        SomeClass.sleep_class()

    ddup.upload(output_filename=pprof_prefix)

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
    ddup.config(env="test", service=test_name, version="my_version")
    ddup.start()

    with stack.StackCollector(ignore_profiler=True, _stack_collector_v2_enabled=True):
        SomeClass.sleep_class(123)

    ddup.upload(output_filename=pprof_prefix)

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
    # TODO(taegyunkim): update echion to support gevent and test with stack v2

    from gevent import monkey

    monkey.patch_all()

    import os
    import threading
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils
    from tests.profiling_v2.collector.test_stack import _fib

    test_name = "test_collect_gevent_thread_task"
    pprof_prefix = "/tmp/" + test_name
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version")
    ddup.start()

    # Start some (green)threads
    def _dofib():
        for _ in range(5):
            # spend some time in CPU so the profiler can catch something
            # On a Mac w/ Apple M3 MAX with Python 3.11 it takes about 200ms to calculate _fib(32)
            # And _fib() is called 5 times so it should take about 1 second
            # We use 5 threads below so it should take about 5 seconds
            _fib(32)
            # Just make sure gevent switches threads/greenlets
            time.sleep(0)

    threads = []

    with stack.StackCollector(_stack_collector_v2_enabled=False):
        for i in range(5):
            t = threading.Thread(target=_dofib, name="TestThread %d" % i)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    ddup.upload(output_filename=pprof_prefix)

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
    with pytest.raises(ValueError):
        stack.StackCollector(max_time_usage_pct=0)


def test_max_time_usage_over():
    with pytest.raises(ValueError):
        stack.StackCollector(max_time_usage_pct=200)


@pytest.mark.parametrize(
    "stack_v2_enabled",
    [True, False],
)
@pytest.mark.parametrize(
    "ignore_profiler",
    [True, False],
)
def test_ignore_profiler(stack_v2_enabled, ignore_profiler, tmp_path):
    if stack_v2_enabled:
        pytest.xfail("Echion doesn't support ignore_profiler yet, and the test flakes")

    test_name = "test_ignore_profiler"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version")
    ddup.start()

    s = stack.StackCollector(ignore_profiler=ignore_profiler, _stack_collector_v2_enabled=stack_v2_enabled)
    collector_worker_thread_id = None

    with s:
        for _ in range(10):
            time.sleep(0.1)
        collector_worker_thread_id = s._worker.ident

    ddup.upload(output_filename=pprof_prefix)

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "thread id")

    thread_ids = set()

    for sample in samples:
        thread_id_label = pprof_utils.get_label_with_key(profile.string_table, sample, "thread id")
        thread_id = int(thread_id_label.num)
        thread_ids.add(thread_id)

    # TODO(taegyunkim): update echion to support ignore_profiler and test with stack v2
    # Echion by default does not track native threads that are not registered
    # after https://github.com/P403n1x87/echion/pull/83.
    if stack_v2_enabled or ignore_profiler:
        assert collector_worker_thread_id not in thread_ids
    else:
        assert collector_worker_thread_id in thread_ids


# TODO: support ignore profiler with stack_v2 and update this test
@pytest.mark.skipif(not TESTING_GEVENT, reason="Not testing gevent")
@pytest.mark.skip(reason="ignore_profiler is not supported with stack v2")
@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_IGNORE_PROFILER="1", DD_PROFILING_OUTPUT_PPROF="/tmp/test_ignore_profiler_gevent_task"),
)
def test_ignore_profiler_gevent_task():
    import gevent.monkey

    gevent.monkey.patch_all()

    import os
    import time
    import typing

    from ddtrace.profiling import collector
    from ddtrace.profiling import event as event_mod
    from ddtrace.profiling import profiler
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    def _fib(n):
        if n == 1:
            return 1
        elif n == 0:
            return 0
        else:
            return _fib(n - 1) + _fib(n - 2)

    class CollectorTest(collector.PeriodicCollector):
        def collect(self) -> typing.Iterable[typing.Iterable[event_mod.Event]]:
            _fib(22)
            return []

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"]

    p = profiler.Profiler()

    p.start()

    for c in p._profiler._collectors:
        if isinstance(c, stack.StackCollector):
            c.ignore_profiler

    c = CollectorTest(None, interval=0.00001)
    c.start()

    time.sleep(3)

    worker_ident = c._worker.ident

    c.stop()
    p.stop()

    profile = pprof_utils.parse_profile(output_filename + "." + str(os.getpid()))

    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")

    thread_ids = set()
    for sample in samples:
        thread_id_label = pprof_utils.get_label_with_key(profile.string_table, sample, "thread id")
        thread_id = int(thread_id_label.num)
        thread_ids.add(thread_id)

    assert worker_ident not in thread_ids


def test_repr():
    test_collector._test_repr(
        stack.StackCollector,
        "StackCollector(status=<ServiceStatus.STOPPED: 'stopped'>, "
        "recorder=Recorder(default_max_events=16384, max_events={}), min_interval_time=0.01, max_time_usage_pct=1.0, "
        "nframes=64, ignore_profiler=False, endpoint_collection_enabled=None, tracer=None)",
    )
