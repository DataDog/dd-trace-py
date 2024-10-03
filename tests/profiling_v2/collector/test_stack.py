import pytest


@pytest.mark.subprocess(env=dict(DD_PROFILING_STACK_V2_ENABLED="true"))
def test_stack_v2_locations():
    import os
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    test_name = "test_locations"
    pprof_prefix = "/tmp/" + test_name
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

    with stack.StackCollector(None):
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
        profile, samples, expected_locations
    ), "Sample with expected locations not found"


# Tests here are marked as subprocess as they are flaky when not marked as such,
# similar to test_user_threads_have_native_id in test_threading.py. For some
# reason, when the Span is created, it's not linked to the MainThread, and the
# profiler can't find the corresponding Span for the MainThread.
@pytest.mark.subprocess()
def test_push_span():
    import os
    import time
    import uuid

    from ddtrace import ext
    from ddtrace import tracer
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    test_name = "test_push_span"
    pprof_prefix = "/tmp/" + test_name
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    tracer._endpoint_call_counter_span_processor.enable()

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    with stack.StackCollector(
        None,
        tracer=tracer,
        endpoint_collection_enabled=True,
        ignore_profiler=True,  # this is not necessary, but it's here to trim samples
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


# pytest.mark.subprocess doesn't support parametrize, so duplicate code here
@pytest.mark.subprocess(env=dict(DD_PROFILING_STACK_V2_ENABLED="true"))
def test_push_span_v2():
    import os
    import time
    import uuid

    from ddtrace import ext
    from ddtrace import tracer
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    test_name = "test_push_span_v2"
    pprof_prefix = "/tmp/" + test_name
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    tracer._endpoint_call_counter_span_processor.enable()

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    with stack.StackCollector(
        None,
        tracer=tracer,
        endpoint_collection_enabled=True,
        ignore_profiler=True,  # this is not necessary, but it's here to trim samples
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


@pytest.mark.subprocess()
def test_push_non_web_span():
    import os
    import time
    import uuid

    from ddtrace import ext
    from ddtrace import tracer
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    test_name = "test_push_non_web_span"
    pprof_prefix = "/tmp/" + test_name
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()

    tracer._endpoint_call_counter_span_processor.enable()

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.SQL

    with stack.StackCollector(
        None,
        tracer=tracer,
        endpoint_collection_enabled=True,
        ignore_profiler=True,  # this is not necessary, but it's here to trim samples
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
