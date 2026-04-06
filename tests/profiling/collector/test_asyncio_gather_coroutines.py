import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_gather_coroutines",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_gather_wall_time() -> None:
    import asyncio
    import os

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    async def inner_1() -> None:
        await asyncio.sleep(1)

    async def inner_2() -> None:
        await asyncio.sleep(2)

    async def main() -> None:
        await asyncio.gather(inner_1(), inner_2())

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

    # Test that we see stacks for inner_1 and inner_2
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            locations=[
                pprof_utils.StackLocation(
                    function_name="sleep",
                    filename="",
                    line_no=-1,
                ),
                pprof_utils.StackLocation(
                    function_name="inner_1",
                    filename="test_asyncio_gather_coroutines.py",
                    line_no=inner_1.__code__.co_firstlineno + 1,
                ),
                pprof_utils.StackLocation(
                    function_name="main",
                    filename="test_asyncio_gather_coroutines.py",
                    line_no=main.__code__.co_firstlineno + 1,
                ),
            ],
        ),
    )

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            locations=[
                pprof_utils.StackLocation(
                    function_name="sleep",
                    filename="",
                    line_no=-1,
                ),
                pprof_utils.StackLocation(
                    function_name="inner_2",
                    filename="test_asyncio_gather_coroutines.py",
                    line_no=inner_2.__code__.co_firstlineno + 1,
                ),
                pprof_utils.StackLocation(
                    function_name="main",
                    filename="test_asyncio_gather_coroutines.py",
                    line_no=main.__code__.co_firstlineno + 1,
                ),
            ],
        ),
    )
