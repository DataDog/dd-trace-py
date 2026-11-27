"""Verify that gather task links survive under high task churn.

Tasks are created and destroyed rapidly while the profiler
samples, so the cleanup code in unwind_tasks must correctly distinguish
live links from stale ones.
"""

import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_churn_links",
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    ),
    err=None,
)
def test_asyncio_churn_links() -> None:
    """Gather links remain correct while short-lived linked tasks churn."""
    import asyncio
    import os

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import async_run

    assert stack.is_available, stack.failure_msg

    async def short_lived() -> None:
        """Many of these run and finish quickly, creating churn in the link map."""
        await asyncio.sleep(0)

    async def long_child() -> None:
        await asyncio.sleep(2)

    async def churn() -> None:
        # Each gather links its short-lived children, which then finish quickly,
        # so the link map keeps gaining and pruning entries.
        for _ in range(50):
            await asyncio.gather(*(asyncio.create_task(short_lived()) for _ in range(5)))
            await asyncio.sleep(0.02)

    async def parent() -> None:
        churner = asyncio.create_task(churn())

        # The gather links the long-lived children to parent.
        # These must survive the cleanup despite the concurrent churn above.
        await asyncio.gather(
            asyncio.create_task(long_child(), name="GatherChild-0"),
            asyncio.create_task(long_child(), name="GatherChild-1"),
        )
        await churner

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

    def loc(fn: str) -> pprof_utils.StackLocation:
        return pprof_utils.StackLocation(function_name=fn, filename="", line_no=-1)

    # Both gather children should have their parent chain intact:
    # long_child / parent / main
    for task_name in ("GatherChild-0", "GatherChild-1"):
        pprof_utils.assert_profile_has_sample(
            profile,
            samples,
            expected_sample=pprof_utils.StackEvent(
                thread_name="MainThread",
                task_name=task_name,
                locations=[
                    loc("sleep"),
                    loc("long_child"),
                    loc("parent"),
                    loc("main"),
                ],
            ),
            print_samples_on_failure=True,
        )
