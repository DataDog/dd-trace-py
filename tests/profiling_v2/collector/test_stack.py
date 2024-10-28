import os
import sys
import time
import uuid

import pytest

from ddtrace import ext
from ddtrace import tracer
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import stack
from tests.profiling.collector import pprof_utils


@pytest.mark.parametrize("stack_v2_enabled", [True, False])
def test_stack_v2_locations(stack_v2_enabled, tmp_path):
    if sys.version_info[:2] == (3, 7) and stack_v2_enabled:
        pytest.skip("stack_v2 is not supported on Python 3.7")

    test_name = "test_locations"
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

    expected_locations = [
        pprof_utils.StackLocation(
            function_name="baz",
            filename="test_stack.py",
        ),
        pprof_utils.StackLocation(
            function_name="bar",
            filename="test_stack.py",
        ),
        pprof_utils.StackLocation(
            function_name="foo",
            filename="test_stack.py",
        ),
    ]

    assert pprof_utils.has_sample_with_locations(
        profile, expected_locations
    ), "Sample with expected locations not found"


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
