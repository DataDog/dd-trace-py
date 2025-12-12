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
    from sys import version_info as PYVERSION
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    def slow_sync_function() -> None:
        time.sleep(1)

    async def asynchronous_function() -> None:
        await asyncio.get_running_loop().run_in_executor(executor=None, func=slow_sync_function)

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

    # Test that we see the asynchronous function stack
    pprof_utils.assert_profile_has_sample(
        profile,
        task_samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            locations=[
                pprof_utils.StackLocation(
                    function_name="asynchronous_function",
                    filename="test_asyncio_executor.py",
                    line_no=asynchronous_function.__code__.co_firstlineno + 1,
                ),
            ],
        ),
    )

    samples = pprof_utils.get_samples_with_label_key(profile, "thread name")
    assert len(samples) > 0

    if PYVERSION >= (3, 11):
        # Thread Pool Executor
        pprof_utils.assert_profile_has_sample(
            profile,
            samples,
            expected_sample=pprof_utils.StackEvent(
                thread_name="asyncio_0",
                locations=[
                    pprof_utils.StackLocation(
                        function_name="slow_sync_function",
                        filename="test_asyncio_executor.py",
                        line_no=slow_sync_function.__code__.co_firstlineno + 1,
                    ),
                    # pprof_utils.StackLocation(
                    #     function_name="_WorkItem.run",
                    #     filename="",
                    #     line_no=-1,
                    # ),
                    # pprof_utils.StackLocation(
                    #     function_name="_worker",
                    #     filename="",
                    #     line_no=-1,
                    # ),
                    # pprof_utils.StackLocation(
                    #     function_name="Thread.run",
                    #     filename="",
                    #     line_no=-1,
                    # ),
                ],
            ),
        )
    elif PYVERSION >= (3, 9):
        # Thread Pool Executor
        pprof_utils.assert_profile_has_sample(
            profile,
            samples,
            expected_sample=pprof_utils.StackEvent(
                thread_name="asyncio_0",
                locations=[
                    pprof_utils.StackLocation(
                        function_name="slow_sync_function",
                        filename="test_asyncio_executor.py",
                        line_no=slow_sync_function.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="run",
                        filename="",
                        line_no=-1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="_worker",
                        filename="",
                        line_no=-1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="run",
                        filename="",
                        line_no=-1,
                    ),
                ],
            ),
        )
    else:
        # Thread Pool Executor
        pprof_utils.assert_profile_has_sample(
            profile,
            samples,
            expected_sample=pprof_utils.StackEvent(
                thread_name="ThreadPoolExecutor-0_0",
                locations=[
                    pprof_utils.StackLocation(
                        function_name="slow_sync_function",
                        filename="test_asyncio_executor.py",
                        line_no=slow_sync_function.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="run",
                        filename="",
                        line_no=-1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="_worker",
                        filename="",
                        line_no=-1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="run",
                        filename="",
                        line_no=-1,
                    ),
                ],
            ),
        )
