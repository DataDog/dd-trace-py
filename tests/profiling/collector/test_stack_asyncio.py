import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_stack_asyncio",
    ),
    err=None,
)
def test_asyncio():
    import asyncio
    import os
    import time
    import uuid

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    sleep_time = 0.2
    loop_run_time = 3

    async def stuff() -> None:
        start_time = time.time()
        while time.time() < start_time + loop_run_time:
            await asyncio.sleep(sleep_time)

    async def hello():
        t1 = asyncio.create_task(stuff(), name="sleep 1")
        t2 = asyncio.create_task(stuff(), name="sleep 2")
        await stuff()
        return (t1, t2)

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    p = profiler.Profiler(tracer=tracer)
    p.start()
    with tracer.trace("test_asyncio", resource=resource, span_type=span_type) as span:
        span_id = span.span_id
        local_root_span_id = span._local_root.span_id

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        maintask = loop.create_task(hello(), name="main")

        t1, t2 = loop.run_until_complete(maintask)
    p.stop()

    t1_name = t1.get_name()
    t2_name = t2.get_name()

    assert t1_name == "sleep 1"
    assert t2_name == "sleep 2"

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())

    profile = pprof_utils.parse_profile(output_filename)

    samples_with_span_id = pprof_utils.get_samples_with_label_key(profile, "span id")
    assert len(samples_with_span_id) > 0

    # get samples with task_name
    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="main",
            span_id=span_id,
            local_root_span_id=local_root_span_id,
            locations=[
                pprof_utils.StackLocation(
                    function_name="hello", filename="test_stack_asyncio.py", line_no=hello.__code__.co_firstlineno + 3
                )
            ],
        ),
    )

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name=t1_name,
            span_id=span_id,
            local_root_span_id=local_root_span_id,
            locations=[
                pprof_utils.StackLocation(
                    function_name="stuff", filename="test_stack_asyncio.py", line_no=stuff.__code__.co_firstlineno + 3
                ),
            ],
        ),
    )

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name=t2_name,
            span_id=span_id,
            local_root_span_id=local_root_span_id,
            locations=[
                pprof_utils.StackLocation(
                    function_name="stuff", filename="test_stack_asyncio.py", line_no=stuff.__code__.co_firstlineno + 3
                ),
            ],
        ),
    )
