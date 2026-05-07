import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_taskgroup",
    ),
    err=None,
    pytest_args=["-k", "test_asyncio_taskgroup"],
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
@pytest.mark.skipif(
    __import__("sys").version_info < (3, 11),
    reason="asyncio.TaskGroup requires Python 3.11+",
)
def test_asyncio_taskgroup() -> None:
    import asyncio
    import os

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    async def other(t: float) -> None:
        await asyncio.sleep(t)

    async def wait_and_return_delay(t: float) -> float:
        await other(t)
        return t

    async def task_with_taskgroup(delay: float) -> float:
        async with asyncio.TaskGroup() as tg:
            task = tg.create_task(wait_and_return_delay(delay))
            return await task

    async def main() -> None:
        # Create tasks that will use TaskGroup
        tasks = [asyncio.create_task(task_with_taskgroup(float(i) / 10)) for i in range(2, 7)]

        # Wait for all tasks to complete
        results = await asyncio.gather(*tasks)
        assert len(results) == 5
        assert results == [0.2, 0.3, 0.4, 0.5, 0.6]

    p = profiler.Profiler()
    p.start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())

    profile = pprof_utils.parse_newest_profile(output_filename)

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    locations = [
        pprof_utils.StackLocation(
            function_name="sleep",
            filename="",
            line_no=-1,
        ),
        pprof_utils.StackLocation(
            function_name="other",
            filename="test_asyncio_taskgroup.py",
            line_no=other.__code__.co_firstlineno + 1,
        ),
        pprof_utils.StackLocation(
            function_name="wait_and_return_delay",
            filename="test_asyncio_taskgroup.py",
            line_no=wait_and_return_delay.__code__.co_firstlineno + 1,
        ),
        pprof_utils.StackLocation(
            function_name="task_with_taskgroup",
            filename="test_asyncio_taskgroup.py",
            line_no=task_with_taskgroup.__code__.co_firstlineno + 3,
        ),
        pprof_utils.StackLocation(
            function_name="main",
            filename="test_asyncio_taskgroup.py",
            line_no=main.__code__.co_firstlineno + 5,
        ),
    ]

    # Check that we have seen samples for the tasks created by TaskGroup
    # TaskGroup creates tasks Task-7 through Task-11 (after the main tasks Task-2 through Task-6)
    exceptions: list[AssertionError] = []
    for i in range(7, 12):
        try:
            pprof_utils.assert_profile_has_sample(
                profile,
                samples,
                expected_sample=pprof_utils.StackEvent(
                    task_name=f"Task-{i}",
                    thread_name="MainThread",
                    locations=locations,
                ),
            )
        except AssertionError as e:
            exceptions.append(e)

    if len(exceptions) > 0:
        pprof_utils.print_all_samples(profile)
        for e in exceptions:
            print(e)

        raise exceptions[0]
