import pytest


@pytest.mark.xfail(
    reason="ThreadSpanLinks retains the last span after its tracing context deactivates",
    strict=True,
)
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_clears_finished_endpoint",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_clears_finished_endpoint_before_untraced_work():
    import os
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    endpoint = "sync-endpoint"

    def traced_work():
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            time.sleep(0.01)

    def untraced_work():
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            time.sleep(0.01)

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    with tracer.trace("sync.request", resource=endpoint, span_type=ext.SpanTypes.WEB):
        traced_work()
    untraced_work()
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_time_index = pprof_utils.get_sample_type_index(profile, "wall-time")

    def function_names(sample):
        names = set()
        for location_id in sample.location_id:
            location = pprof_utils.get_location_with_id(profile, location_id)
            line = location.line[0]
            function = pprof_utils.get_function_with_id(profile, line.function_id)
            names.add(profile.string_table[function.name])
        return names

    def endpoint_label(sample):
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "trace endpoint")
        return None if label is None else profile.string_table[label.str]

    traced_samples = []
    untraced_samples = []
    for sample in profile.sample:
        if sample.value[wall_time_index] <= 0:
            continue
        names = function_names(sample)
        if "traced_work" in names:
            traced_samples.append(sample)
        if "untraced_work" in names:
            untraced_samples.append(sample)

    assert traced_samples
    assert untraced_samples
    assert all(endpoint_label(sample) == endpoint for sample in traced_samples)
    assert all(endpoint_label(sample) is None for sample in untraced_samples), [
        endpoint_label(sample) for sample in untraced_samples
    ]


@pytest.mark.xfail(
    reason="ThreadSpanLinks stores one span per thread rather than one per asyncio task",
    strict=True,
)
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_asyncio_task_endpoints",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_keeps_asyncio_task_endpoints_separate():
    import asyncio
    import os
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    endpoint_a = "async-endpoint-a"
    endpoint_b = "async-endpoint-b"

    async def task_a(a_active, b_active):
        with tracer.trace("async.request.a", resource=endpoint_a, span_type=ext.SpanTypes.WEB):
            a_active.set()
            await b_active.wait()
            deadline = time.thread_time_ns() + 500_000_000
            while time.thread_time_ns() < deadline:
                pass

    async def task_b(a_active, b_active):
        await a_active.wait()
        with tracer.trace("async.request.b", resource=endpoint_b, span_type=ext.SpanTypes.WEB):
            b_active.set()
            await asyncio.sleep(0.6)

    async def main():
        a_active = asyncio.Event()
        b_active = asyncio.Event()
        await asyncio.gather(
            asyncio.create_task(task_a(a_active, b_active), name="endpoint-task-a"),
            asyncio.create_task(task_b(a_active, b_active), name="endpoint-task-b"),
        )

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    asyncio.run(main())
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_time_index = pprof_utils.get_sample_type_index(profile, "wall-time")

    def function_names(sample):
        names = set()
        for location_id in sample.location_id:
            location = pprof_utils.get_location_with_id(profile, location_id)
            line = location.line[0]
            function = pprof_utils.get_function_with_id(profile, line.function_id)
            names.add(profile.string_table[function.name])
        return names

    def endpoint_label(sample):
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "trace endpoint")
        return None if label is None else profile.string_table[label.str]

    task_a_samples = []
    for sample in profile.sample:
        if sample.value[wall_time_index] <= 0:
            continue
        if "task_a" in function_names(sample):
            task_a_samples.append(sample)

    assert task_a_samples
    assert all(endpoint_label(sample) == endpoint_a for sample in task_a_samples), [
        endpoint_label(sample) for sample in task_a_samples
    ]
