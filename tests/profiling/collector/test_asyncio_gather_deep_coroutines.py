import pytest


@pytest.mark.subprocess
def test_asyncio_gather_deep_coroutines() -> None:
    import asyncio

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    async def deeper() -> None:
        await asyncio.sleep(1.0)

    async def deep() -> None:
        await deeper()

    async def inner() -> None:
        await deep()

    async def main() -> None:
        await asyncio.gather(inner(), inner())

    p = profiler.Profiler()
    p.start()

    asyncio.run(main())

    p.stop()

    profile = pprof_utils.get_profile_from_agent()

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    def loc(f_name: str) -> pprof_utils.StackLocation:
        return pprof_utils.StackLocation(function_name=f_name, filename="", line_no=-1)

    # Test that we see stacks for inner_1 and inner_2
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            locations=list(
                reversed(
                    [
                        # loc("Task-1"),
                        loc("main"),
                        # loc("Task-2"),
                        loc("inner"),
                        loc("deep"),
                        loc("deeper"),
                        loc("sleep"),
                    ]
                ),
            ),
        ),
        print_samples_on_failure=True,
    )
