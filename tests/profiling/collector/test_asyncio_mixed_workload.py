import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_mixed_workload",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_mixed_workload() -> None:
    import asyncio
    import math
    import os
    import sys

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    async def dependency(t: float) -> None:
        await asyncio.sleep(t)

    def heavy_computation() -> int:
        result = 1
        for i in range(150):
            result *= math.factorial(i)
        return result

    async def mixed_workload() -> int:
        await dependency(0.5)

        result = heavy_computation()

        await dependency(0.25)
        return result

    async def async_main() -> None:
        result = await mixed_workload()
        assert result > 0

    def main() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(async_main())

    p = profiler.Profiler()
    p.start()
    main()
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())

    profile = pprof_utils.parse_newest_profile(output_filename)

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    def loc(f: str, filename: str = "", line: int = -1) -> pprof_utils.StackLocation:
        return pprof_utils.StackLocation(
            function_name=f,
            filename=filename,
            line_no=line,
        )

    handle = "Handle." if sys.version_info >= (3, 11) else ""
    base_event_loop = "BaseEventLoop." if sys.version_info >= (3, 11) else ""
    selector = (
        ("KqueueSelector." if sys.platform == "darwin" else "EpollSelector.") if sys.version_info >= (3, 11) else ""
    )

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            locations=list(
                reversed(
                    [
                        loc("<module>"),
                        loc("main"),
                        loc(f"{base_event_loop}run_until_complete"),
                        loc(f"{base_event_loop}run_forever"),
                        loc(f"{base_event_loop}_run_once"),
                        # This is select because we are EITHER running the idle coroutine OR the heavy computation
                        # one, never both. So if we are in dependency/sleep, we are only running the idle coroutine,
                        # so we will see select.
                        loc(f"{selector}select"),
                        # loc("Task-1"),
                        loc("async_main"),
                        loc("mixed_workload"),
                        loc("dependency"),
                        loc("sleep"),
                    ]
                )
            ),
        ),
        print_samples_on_failure=True,
    )

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            locations=list(
                reversed(
                    [
                        loc("<module>"),
                        loc("main"),
                        loc(f"{base_event_loop}run_until_complete"),
                        loc(f"{base_event_loop}run_forever"),
                        loc(f"{base_event_loop}_run_once"),
                        loc(f"{handle}_run"),
                        # loc("Task-1"),
                        loc("async_main"),
                        loc("mixed_workload"),
                        loc("heavy_computation"),
                    ]
                )
            ),
        ),
        print_samples_on_failure=True,
    )
