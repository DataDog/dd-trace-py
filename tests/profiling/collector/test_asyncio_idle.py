import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_run_frames_captured",
    ),
    err=None,
)
def test_asyncio_run_frames_captured():
    """
    Regression test for bug where asyncio frames were not captured when using asyncio.run().

    The bug was caused by upper_python_stack_size defaulting to 0 when the "_run" frame
    was not found in the Python stack. This caused start_index = python_stack.size() - 0,
    which meant the loop to add frames never executed.
    """
    import asyncio
    import os
    import sys

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import async_run

    assert stack.is_available, stack.failure_msg

    p = profiler.Profiler()
    p.start()

    async def my_coroutine(n: float) -> None:
        await asyncio.sleep(n)

    async def main() -> None:
        # Give the profiler some time to start up
        await asyncio.sleep(0.5)

        execution_time_sec = 2.0

        short_task = asyncio.create_task(my_coroutine(execution_time_sec / 2), name="short_task")

        # asyncio.gather will automatically wrap my_coroutine into a Task
        await asyncio.gather(short_task, my_coroutine(execution_time_sec))

    async_run(main())

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    # Get samples with task_name - this is the key check
    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0, "No task names found - asyncio task tracking failed!"

    use_uvloop = os.environ.get("USE_UVLOOP", "0") == "1"

    # uvloop uses a C-based event loop that doesn't go through Python's BaseEventLoop methods
    # so we only expect these frames when NOT using uvloop
    if use_uvloop:
        event_loop_frames = []
    else:
        base_event_loop_prefix = "BaseEventLoop." if sys.version_info >= (3, 11) else ""
        selector_prefix = (
            ("KqueueSelector." if sys.platform == "darwin" else "EpollSelector.") if sys.version_info >= (3, 11) else ""
        )
        event_loop_frames = [
            pprof_utils.StackLocation(function_name=f"{selector_prefix}select", filename="", line_no=-1),
            pprof_utils.StackLocation(function_name=f"{base_event_loop_prefix}_run_once", filename="", line_no=-1),
            pprof_utils.StackLocation(function_name=f"{base_event_loop_prefix}run_forever", filename="", line_no=-1),
            pprof_utils.StackLocation(
                function_name=f"{base_event_loop_prefix}run_until_complete", filename="", line_no=-1
            ),
        ]

    def loc(f_name: str, filename: str = "", line_no: int = -1) -> pprof_utils.StackLocation:
        return pprof_utils.StackLocation(function_name=f_name, filename=filename, line_no=line_no)

    # Check that we have main -> sleep (the one where we wait for the Profiler to be ready)
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="Task-1",
            locations=[
                loc("sleep"),
                loc("main", filename="test_asyncio_idle.py", line_no=main.__code__.co_firstlineno + 2),
            ]
            + event_loop_frames
            + ([loc("Runner.run")] if sys.version_info >= (3, 11) else [])
            + [loc("run")],
        ),
        print_samples_on_failure=True,
    )

    # Check that we have (asyncio) -> main -> my_coroutine -> sleep
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="short_task",
            locations=[
                loc("sleep"),
                loc(
                    my_coroutine.__name__,
                    filename="test_asyncio_idle.py",
                    line_no=my_coroutine.__code__.co_firstlineno + 1,
                ),
                loc("main", filename="test_asyncio_idle.py", line_no=main.__code__.co_firstlineno + 9),
            ]
            + event_loop_frames
            + ([loc("Runner.run")] if sys.version_info >= (3, 11) else [])
            + [loc("run")],
        ),
        print_samples_on_failure=True,
    )

    # Check that we have (asyncio) -> main -> my_coroutine -> sleep
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            locations=[
                loc("sleep"),
                loc(
                    my_coroutine.__name__,
                    filename="test_asyncio_idle.py",
                    line_no=my_coroutine.__code__.co_firstlineno + 1,
                ),
                loc("main", filename="test_asyncio_idle.py", line_no=main.__code__.co_firstlineno + 9),
            ]
            + event_loop_frames
            + ([loc("Runner.run")] if sys.version_info >= (3, 11) else [])
            + [loc("run")],
        ),
        print_samples_on_failure=True,
    )
