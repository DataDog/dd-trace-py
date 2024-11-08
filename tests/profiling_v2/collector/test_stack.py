import _thread
import os
import sys
import threading
import time
from unittest.mock import patch
import uuid

import pytest

from ddtrace import ext
from ddtrace import tracer
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import stack
from ddtrace.settings.profiling import config
from tests.profiling.collector import pprof_utils


@pytest.mark.parametrize("stack_v2_enabled", [True, False])
def test_stack_locations(stack_v2_enabled, tmp_path):
    if sys.version_info[:2] == (3, 7) and stack_v2_enabled:
        pytest.skip("stack_v2 is not supported on Python 3.7")

    test_name = "test_stack_locations"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    def baz():
        time.sleep(0.01)

    def bar():
        baz()

    def foo():
        bar()

    with stack.StackCollector(None, _stack_collector_v2_enabled=stack_v2_enabled):
        for _ in range(5):
            foo()
    ddup.upload()

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


@pytest.mark.parametrize("stack_v2_enabled", [True, False])
def test_push_span(stack_v2_enabled, tmp_path):
    if sys.version_info[:2] == (3, 7) and stack_v2_enabled:
        pytest.skip("stack_v2 is not supported on Python 3.7")

    test_name = "test_push_span"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    tracer._endpoint_call_counter_span_processor.enable()

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    with stack.StackCollector(
        None,
        tracer=tracer,
        endpoint_collection_enabled=True,
        ignore_profiler=True,  # this is not necessary, but it's here to trim samples
        _stack_collector_v2_enabled=stack_v2_enabled,
    ):
        with tracer.trace("foobar", resource=resource, span_type=span_type) as span:
            span_id = span.span_id
            local_root_span_id = span._local_root.span_id
            for _ in range(5):
                time.sleep(0.01)
    ddup.upload()

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


def test_push_span_unregister_thread(tmp_path, monkeypatch):
    if sys.version_info[:2] == (3, 7):
        pytest.skip("stack_v2 is not supported on Python 3.7")

    with patch("ddtrace.internal.datadog.profiling.stack_v2.unregister_thread") as unregister_thread:
        monkeypatch.setattr(config.stack, "v2_enabled", True)
        tracer._endpoint_call_counter_span_processor.enable()

        test_name = "test_push_span_unregister_thread"
        pprof_prefix = str(tmp_path / test_name)
        output_filename = pprof_prefix + "." + str(os.getpid())

        assert ddup.is_available
        ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
        ddup.start()

        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.WEB

        def target_fun():
            for _ in range(5):
                time.sleep(0.01)

        with stack.StackCollector(
            None,
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
        ddup.upload()

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

        unregister_thread.assert_called_once_with(thread_id)


@pytest.mark.parametrize("stack_v2_enabled", [True, False])
def test_push_non_web_span(stack_v2_enabled, tmp_path):
    if sys.version_info[:2] == (3, 7) and stack_v2_enabled:
        pytest.skip("stack_v2 is not supported on Python 3.7")

    tracer._endpoint_call_counter_span_processor.enable()

    test_name = "test_push_non_web_span"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.SQL

    with stack.StackCollector(
        None,
        tracer=tracer,
        endpoint_collection_enabled=True,
        ignore_profiler=True,  # this is not necessary, but it's here to trim samples
        _stack_collector_v2_enabled=stack_v2_enabled,
    ):
        with tracer.trace("foobar", resource=resource, span_type=span_type) as span:
            span_id = span.span_id
            local_root_span_id = span._local_root.span_id
            for _ in range(5):
                time.sleep(0.01)
    ddup.upload()

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


@pytest.mark.parametrize("stack_v2_enabled", [True, False])
def test_push_span_none_span_type(stack_v2_enabled, tmp_path):
    # Test for https://github.com/DataDog/dd-trace-py/issues/11141
    if sys.version_info[:2] == (3, 7) and stack_v2_enabled:
        pytest.skip("stack_v2 is not supported on Python 3.7")

    test_name = "test_push_span_none_span_type"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    tracer._endpoint_call_counter_span_processor.enable()

    resource = str(uuid.uuid4())

    with stack.StackCollector(
        None,
        tracer=tracer,
        endpoint_collection_enabled=True,
        ignore_profiler=True,  # this is not necessary, but it's here to trim samples
        _stack_collector_v2_enabled=stack_v2_enabled,
    ):
        # Explicitly set None span_type as the default could change in the
        # future.
        with tracer.trace("foobar", resource=resource, span_type=None) as span:
            span_id = span.span_id
            local_root_span_id = span._local_root.span_id
            for _ in range(5):
                time.sleep(0.01)
    ddup.upload()

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
    if sys.version_info[:2] == (3, 7) and stack_v2_enabled:
        pytest.skip("stack_v2 is not supported on Python 3.7")

    test_name = "test_exception_collection"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    with stack.StackCollector(None, ignore_profiler=True, _stack_collector_v2_enabled=stack_v2_enabled):
        try:
            raise ValueError("hello")
        except Exception:
            time.sleep(1)

    ddup.upload()

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
                            line_no=test_exception_collection.__code__.co_firstlineno + 18,
                        ),
                    ],
                ),
            )


@pytest.mark.skipif(not stack.FEATURES["stack-exceptions"], reason="Stack exceptions are not supported")
@pytest.mark.parametrize("stack_v2_enabled", [True, False])
def test_exception_collection_threads(stack_v2_enabled, tmp_path):
    if sys.version_info[:2] == (3, 7) and stack_v2_enabled:
        pytest.skip("stack_v2 is not supported on Python 3.7")

    test_name = "test_exception_collection_threads"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    with stack.StackCollector(None, ignore_profiler=True, _stack_collector_v2_enabled=stack_v2_enabled):

        def target_fun():
            try:
                raise ValueError("hello")
            except Exception:
                time.sleep(1)

        threads = []
        for _ in range(5):
            t = threading.Thread(target=target_fun)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

    ddup.upload()

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
def test_exception_collection_trace(stack_v2_enabled, tmp_path):
    if sys.version_info[:2] == (3, 7) and stack_v2_enabled:
        pytest.skip("stack_v2 is not supported on Python 3.7")

    test_name = "test_exception_collection_trace"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    tracer._endpoint_call_counter_span_processor.enable()

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    with stack.StackCollector(None, tracer=tracer, ignore_profiler=True, _stack_collector_v2_enabled=stack_v2_enabled):
        with tracer.trace("foobar", resource="resource", span_type=ext.SpanTypes.WEB):
            try:
                raise ValueError("hello")
            except Exception:
                time.sleep(1)

    ddup.upload()

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
                            line_no=test_exception_collection_trace.__code__.co_firstlineno + 21,
                        ),
                    ],
                ),
            )


@pytest.mark.parametrize("stack_v2_enabled", [True, False])
def test_collect_once_with_class(stack_v2_enabled, tmp_path):
    if sys.version_info[:2] == (3, 7) and stack_v2_enabled:
        pytest.skip("stack_v2 is not supported on Python 3.7")

    class SomeClass(object):
        @classmethod
        def sleep_class(cls):
            return cls().sleep_instance()

        def sleep_instance(self):
            for _ in range(5):
                time.sleep(0.01)

    test_name = "test_collect_once_with_class"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    with stack.StackCollector(None, ignore_profiler=True, _stack_collector_v2_enabled=stack_v2_enabled):
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
            class_name="SomeClass" if not stack_v2_enabled else None,
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
                    line_no=test_collect_once_with_class.__code__.co_firstlineno + 23,
                ),
            ],
        ),
    )


@pytest.mark.parametrize("stack_v2_enabled", [True, False])
def test_collect_once_with_class_not_right_type(stack_v2_enabled, tmp_path):
    if sys.version_info[:2] == (3, 7) and stack_v2_enabled:
        pytest.skip("stack_v2 is not supported on Python 3.7")

    class SomeClass(object):
        @classmethod
        def sleep_class(foobar, cls):
            return foobar().sleep_instance(cls)

        def sleep_instance(foobar, self):
            for _ in range(5):
                time.sleep(0.01)

    test_name = "test_collect_once_with_class"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    with stack.StackCollector(None, ignore_profiler=True, _stack_collector_v2_enabled=stack_v2_enabled):
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
            # stack v1 relied on using cls and self to figure out class name
            # so we can't find it here.
            class_name=None,
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
                    line_no=test_collect_once_with_class_not_right_type.__code__.co_firstlineno + 23,
                ),
            ],
        ),
    )
