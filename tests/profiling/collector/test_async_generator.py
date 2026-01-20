import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_async_generator",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_async_generator() -> None:
    import asyncio
    import os
    import typing
    import uuid

    from ddtrace import ext
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    async def deep_dependency():
        await asyncio.sleep(0.05)

    async def async_generator_dep(i: int) -> typing.AsyncGenerator[int, None]:
        for j in range(i):
            await deep_dependency()
            yield j

    async def async_generator() -> typing.AsyncGenerator[int, None]:
        for i in range(10):
            async for j in async_generator_dep(i):
                yield j

    async def asynchronous_function() -> None:
        async for i in async_generator():
            pass

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    p = profiler.Profiler(tracer=tracer)
    p.start()
    with tracer.trace("test_asyncio", resource=resource, span_type=span_type) as span:
        span_id = span.span_id
        local_root_span_id = span._local_root.span_id

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        main_task = loop.create_task(asynchronous_function(), name="asynchronous_function")
        loop.run_until_complete(main_task)

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())

    profile = pprof_utils.parse_newest_profile(output_filename)

    samples_with_span_id = pprof_utils.get_samples_with_label_key(profile, "span id")
    assert len(samples_with_span_id) > 0

    # get samples with task_name
    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    # The next fails if stack is not properly configured with asyncio task
    # tracking via ddtrace.profiling._asyncio
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="asynchronous_function",
            span_id=span_id,
            local_root_span_id=local_root_span_id,
            locations=[
                pprof_utils.StackLocation(
                    function_name="sleep",
                    filename="",  # r"^.*tasks\.py$",
                    line_no=-1,
                ),
                pprof_utils.StackLocation(
                    function_name="deep_dependency",
                    filename="test_async_generator.py",
                    line_no=deep_dependency.__code__.co_firstlineno + 1,
                ),
                pprof_utils.StackLocation(
                    function_name="async_generator_dep",
                    filename="test_async_generator.py",
                    line_no=async_generator_dep.__code__.co_firstlineno + 2,
                ),
                pprof_utils.StackLocation(
                    function_name="async_generator",
                    filename="test_async_generator.py",
                    line_no=async_generator.__code__.co_firstlineno + 2,
                ),
                pprof_utils.StackLocation(
                    function_name="asynchronous_function",
                    filename="test_async_generator.py",
                    line_no=asynchronous_function.__code__.co_firstlineno + 1,
                ),
            ],
        ),
    )
