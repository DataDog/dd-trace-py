import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_as_completed",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_as_completed() -> None:
    import asyncio
    import os
    import random
    from sys import version_info as PYVERSION

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.logger import get_logger
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    LOG = get_logger(__name__)

    assert stack.is_available, stack.failure_msg

    async def other(t: float) -> None:
        await asyncio.sleep(t)

    async def wait_and_return_delay(t: float) -> float:
        await other(t)
        return t

    async def main() -> None:
        # Create a mix of Tasks and Coroutines
        futures = [
            asyncio.create_task(wait_and_return_delay(float(i) / 10))
            if i % 2 == 0
            else wait_and_return_delay(float(i) / 10)
            for i in range(2, 12)
        ]
        assert len(futures) == 10

        # Randomize the order of the futures
        random.shuffle(futures)

        # Wait for the futures to complete and store their result (each Future will return
        # the time that it slept for)
        result: list[float] = []
        for future in asyncio.as_completed(futures):
            result.append(await future)

        # Validate that the returned results are in ascending order
        # which should be the case since each future will wait x seconds
        # before returning x, and all tasks are started around the same time.
        assert sorted(result) == result

    p = profiler.Profiler()
    p.start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())

    profile = pprof_utils.parse_newest_profile(output_filename)

    task_names_in_profile = sorted(
        set(
            [
                (profile.string_table[label.str])
                for sample in profile.sample
                for label in sample.label
                if profile.string_table[label.key] == "task name"
            ]
        )
    )

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    locations = [
        pprof_utils.StackLocation(
            function_name="wait_and_return_delay",
            filename="test_asyncio_as_completed.py",
            line_no=wait_and_return_delay.__code__.co_firstlineno + 1,
        ),
        pprof_utils.StackLocation(
            function_name="main",
            filename="test_asyncio_as_completed.py",
            line_no=main.__code__.co_firstlineno + 17,
        ),
    ]

    if PYVERSION < (3, 13):
        locations = [
            pprof_utils.StackLocation(
                function_name="sleep",
                filename="",
                line_no=-1,
            ),
            pprof_utils.StackLocation(
                function_name="other",
                filename="test_asyncio_as_completed.py",
                line_no=other.__code__.co_firstlineno + 1,
            ),
        ] + locations

    # Now, check that we have seen those locations for each Task we've created.
    # (They should be named Task-2 .. Task-11, which is the automatic name assigned to Tasks by asyncio.create_task)
    # Note: we expect one Task to not be seen (and thus accept to recover from one failure). The reason
    # is that there is a bug in ddtrace that makes one Task (randomly picked) appear "as part of" the Parent Task,
    # and this Task thus gets the Parent Task's name and not its own.
    seen_all_except_one = True
    seen_task_names: set[str] = set()
    for i in range(2, 12):
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
        except AssertionError:
            if not seen_all_except_one:
                LOG.error(
                    f"More than one Task has not been seen; i = {i} "  # noqa: G004
                    f"seen_task_names = {seen_task_names} "
                    f"task_names_in_profile = {task_names_in_profile}"
                )
                raise

            # This is the bug situation.
            # Check that we have seen the expected locations for the Parent Task (Task-1)
            # If that isn't the case, then something else is broken.
            pprof_utils.assert_profile_has_sample(
                profile,
                samples,
                expected_sample=pprof_utils.StackEvent(
                    task_name="Task-1",
                    thread_name="MainThread",
                    locations=locations,
                ),
            )
            seen_all_except_one = False
