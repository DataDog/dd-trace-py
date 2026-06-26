import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_executor",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_executor_wall_time() -> None:
    import asyncio
    import os

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg
    from typing import AsyncGenerator

    async def deep_dependency() -> None:
        # This is a regular (non-generator) coroutine called
        # by an async generator.
        await asyncio.sleep(0.15)

    async def async_generator_dep(i: int) -> AsyncGenerator[int, None]:
        # This is an async generator called by an async generator.
        # We want to make sure that recursive async generators are correctly sampled.
        for j in range(i):
            await deep_dependency()
            yield j

    async def async_generator() -> AsyncGenerator[int, None]:
        # This is an async generator called by a coroutine.
        # We want to make sure we unwind async generators correctly.
        for i in range(5):
            async for j in async_generator_dep(i):
                yield j

    async def asynchronous_function() -> None:
        # This is a normal (non-generator) coroutine that calls into an async generator.
        # Stack samples should not stopped at this function, they should continue unwinding
        # into the async generator.
        async for _ in async_generator():
            pass

    async def main() -> None:
        await asynchronous_function()

    p = profiler.Profiler()
    p.start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())

    profile = pprof_utils.parse_newest_profile(output_filename)

    task_samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(task_samples) > 0

    def loc(f_name: str) -> pprof_utils.StackLocation:
        return pprof_utils.StackLocation(function_name=f_name, filename="", line_no=-1)

    # Thread Pool Executor
    pprof_utils.assert_profile_has_sample(
        profile,
        task_samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="Task-1",
            locations=list(
                reversed(
                    [
                        # loc("Task-1"),
                        loc("main"),
                        loc("asynchronous_function"),
                        loc("async_generator"),
                        loc("async_generator_dep"),
                        loc("deep_dependency"),
                        loc("sleep"),
                    ]
                )
            ),
        ),
        print_samples_on_failure=True,
    )
