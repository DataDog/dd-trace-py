import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_shield",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_shield() -> None:
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

    async def main() -> None:
        # Create tasks and shield them
        tasks = [asyncio.create_task(wait_and_return_delay(float(i) / 10)) for i in range(2, 7)]

        # Shield each task
        shielded_tasks = [asyncio.shield(task) for task in tasks]

        # Wait for all shielded tasks to complete
        results = await asyncio.gather(*shielded_tasks)
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
            filename="test_asyncio_shield.py",
            line_no=other.__code__.co_firstlineno + 1,
        ),
        pprof_utils.StackLocation(
            function_name="wait_and_return_delay",
            filename="test_asyncio_shield.py",
            line_no=wait_and_return_delay.__code__.co_firstlineno + 1,
        ),
        pprof_utils.StackLocation(
            function_name="main",
            filename="test_asyncio_shield.py",
            line_no=main.__code__.co_firstlineno + 8,
        ),
    ]

    # Check that we have seen samples for the shielded tasks (Task-2 .. Task-6)
    seen_task_names: set[str] = set()
    exceptions: list[AssertionError] = []
    for i in range(2, 7):
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
            seen_task_names.add(f"Task-{i}")
        except AssertionError as e:
            exceptions.append(e)

    if len(exceptions) > 0:
        pprof_utils.print_all_samples(profile)
        for e in exceptions:
            print(e)

        raise exceptions[0]
