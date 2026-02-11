import os

import pytest


# Skip this test when using uvloop - the weak link feature relies on asyncio internals
# that uvloop doesn't expose the same way
@pytest.mark.skipif(
    os.environ.get("USE_UVLOOP", "0") == "1",
    reason="uvloop does not support weak link detection the same way as asyncio",
)
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_weak_links",
        DD_PROFILING_EXCEPTION_ENABLED="false",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_weak_links_wall_time() -> None:
    import asyncio
    import os

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import async_run

    assert stack.is_available, stack.failure_msg

    async def func_not_awaited() -> None:
        await asyncio.sleep(0.5)

    async def func_awaited() -> None:
        await asyncio.sleep(1)

    async def parent() -> asyncio.Task:
        await asyncio.sleep(0.5)

        t_not_awaited = asyncio.create_task(func_not_awaited(), name="Task-not_awaited")
        t_awaited = asyncio.create_task(func_awaited(), name="Task-awaited")

        await t_awaited

        # At this point, we have not awaited t_not_awaited but it should have finished
        # before t_awaited as the delay is much shorter.
        # Returning it to avoid the warning on unused variable.
        return t_not_awaited

    async def main() -> None:
        await parent()

    p = profiler.Profiler()
    p.start()

    async_run(main())

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())

    profile = pprof_utils.parse_newest_profile(output_filename)

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    def loc(fn: str, file: str = "", line: int = -1) -> pprof_utils.StackLocation:
        return pprof_utils.StackLocation(
            function_name=fn,
            filename=file,
            line_no=line,
        )

    # We should see a stack for (Task-1) / parent / (Task-not_awaited) / not_awaited / sleep
    # Even though Task-1 does not await Task-not_awaited, the fact that there is a weak (parent - child) link
    # means that Task-not_awaited is under Task-1.
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="Task-not_awaited",
            locations=[
                loc("sleep"),
                loc("func_not_awaited", "test_asyncio_weak_links.py", func_not_awaited.__code__.co_firstlineno + 1),
                # loc("Task-not_awaited"),
                loc("parent", "test_asyncio_weak_links.py", parent.__code__.co_firstlineno + 6),
                # loc("Task-1"),
            ],
        ),
        print_samples_on_failure=True,
    )

    # We should see a stack for Task-1 / parent / Task-awaited / awaited / sleep
    # That is because Task-1 is awaiting Task-awaited.
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="Task-awaited",
            locations=[
                loc("sleep"),
                loc("func_awaited", "test_asyncio_weak_links.py", func_awaited.__code__.co_firstlineno + 1),
                # loc("Task-awaited"),
                loc("parent", "test_asyncio_weak_links.py", parent.__code__.co_firstlineno + 6),
                # loc("Task-1"),
            ],
        ),
        print_samples_on_failure=True,
    )
