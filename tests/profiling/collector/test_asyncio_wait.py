import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_wait",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_wait() -> None:
    import asyncio
    import os
    import time
    import uuid

    from ddtrace import ext
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    sleep_time = 0.2
    loop_run_time = 3

    async def inner1() -> None:
        start_time = time.time()
        while time.time() < start_time + loop_run_time:
            await asyncio.sleep(sleep_time)

    async def inner2() -> None:
        start_time = time.time()
        while time.time() < start_time + loop_run_time:
            await asyncio.sleep(sleep_time)

    async def outer() -> None:
        t1 = asyncio.create_task(inner1(), name="inner 1")
        t2 = asyncio.create_task(inner2(), name="inner 2")
        await asyncio.wait(fs=(t1, t2), return_when=asyncio.ALL_COMPLETED)

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    p = profiler.Profiler(tracer=tracer)
    p.start()
    with tracer.trace("test_asyncio", resource=resource, span_type=span_type) as span:
        span_id = span.span_id
        local_root_span_id = span._local_root.span_id

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        main_task = loop.create_task(outer(), name="outer")
        loop.run_until_complete(main_task)

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())

    profile = pprof_utils.parse_newest_profile(output_filename)

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    # Note: there currently is a bug somewhere that makes one of the Tasks show up under the parent Task and the
    # other Tasks be under their own Task name. We need to fix this.
    # For the time being, though, which Task is "independent" is non-deterministic which means we must
    # test both possibilities ("inner 2" is part of "outer" or "inner 1" is part of "outer").
    try:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples,
            expected_sample=pprof_utils.StackEvent(
                thread_name="MainThread",
                task_name="outer",  # TODO: This is a bug and we need to fix it, it should be "inner 1"
                span_id=span_id,
                local_root_span_id=local_root_span_id,
                locations=[
                    pprof_utils.StackLocation(
                        function_name="inner1",
                        filename="test_asyncio_wait.py",
                        line_no=inner1.__code__.co_firstlineno + 3,
                    ),
                    pprof_utils.StackLocation(
                        function_name="outer",
                        filename="test_asyncio_wait.py",
                        line_no=outer.__code__.co_firstlineno + 3,
                    ),
                ],
            ),
        )

        pprof_utils.assert_profile_has_sample(
            profile,
            samples,
            expected_sample=pprof_utils.StackEvent(
                thread_name="MainThread",
                task_name="inner 2",
                span_id=span_id,
                local_root_span_id=local_root_span_id,
                locations=[
                    pprof_utils.StackLocation(
                        function_name="inner2",
                        filename="test_asyncio_wait.py",
                        line_no=inner2.__code__.co_firstlineno + 3,
                    ),
                    pprof_utils.StackLocation(
                        function_name="outer",
                        filename="test_asyncio_wait.py",
                        line_no=outer.__code__.co_firstlineno + 3,
                    ),
                ],
            ),
        )
    except AssertionError:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples,
            expected_sample=pprof_utils.StackEvent(
                thread_name="MainThread",
                task_name="outer",  # TODO: This is a bug and we need to fix it, it should be "inner 1"
                span_id=span_id,
                local_root_span_id=local_root_span_id,
                locations=[
                    pprof_utils.StackLocation(
                        function_name="inner2",
                        filename="test_asyncio_wait.py",
                        line_no=inner2.__code__.co_firstlineno + 3,
                    ),
                    pprof_utils.StackLocation(
                        function_name="outer",
                        filename="test_asyncio_wait.py",
                        line_no=outer.__code__.co_firstlineno + 3,
                    ),
                ],
            ),
        )

        pprof_utils.assert_profile_has_sample(
            profile,
            samples,
            expected_sample=pprof_utils.StackEvent(
                thread_name="MainThread",
                task_name="inner 1",
                span_id=span_id,
                local_root_span_id=local_root_span_id,
                locations=[
                    pprof_utils.StackLocation(
                        function_name="inner1",
                        filename="test_asyncio_wait.py",
                        line_no=inner1.__code__.co_firstlineno + 3,
                    ),
                    pprof_utils.StackLocation(
                        function_name="outer",
                        filename="test_asyncio_wait.py",
                        line_no=outer.__code__.co_firstlineno + 3,
                    ),
                ],
            ),
        )
