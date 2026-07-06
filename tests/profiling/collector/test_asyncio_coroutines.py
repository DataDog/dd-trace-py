import pytest


@pytest.mark.subprocess
def test_asyncio_coroutines() -> None:
    import asyncio
    import math
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.utils import with_profiling_test_agent

    assert stack.is_available, stack.failure_msg

    sleep_time = 0.2
    loop_run_time = 0.75
    cpu_run_time = 1.0

    async def background_task_func() -> None:
        await asyncio.sleep(2.0)

    async def background_math_function() -> None:
        target = time.time() + cpu_run_time
        s = 0.0
        i = 0
        while time.time() < target:
            s += math.sin(i)
            i += 1

    async def sub_coro() -> None:
        start_time = time.time()
        while time.time() < start_time + loop_run_time:
            await asyncio.sleep(sleep_time)

    async def main_coro() -> None:
        await sub_coro()
        await asyncio.sleep(0.25)

    async def outer_function() -> None:
        background_task = loop.create_task(background_task_func(), name="background_wait")
        math_task = loop.create_task(background_math_function(), name="background_math")
        assert background_task is not None

        await main_coro()

        await background_task
        await math_task

    with with_profiling_test_agent() as agent_client:
        p = profiler.Profiler()
        p.start()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        main_task = loop.create_task(outer_function(), name="main")
        loop.run_until_complete(main_task)

        p.stop()

        profile = pprof_utils.get_profile_from_agent(agent_client)

        wall_time_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
        samples = wall_time_samples

        # Test that we see the sub_coro function
        pprof_utils.assert_profile_has_sample(
            profile,
            samples,
            expected_sample=pprof_utils.StackEvent(
                thread_name="MainThread",
                task_name="main",
                locations=[
                    pprof_utils.StackLocation(
                        function_name="sleep",
                        filename="",
                        line_no=-1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="sub_coro",
                        filename="test_asyncio_coroutines.py",
                        line_no=sub_coro.__code__.co_firstlineno + 3,
                    ),
                    pprof_utils.StackLocation(
                        function_name="main_coro",
                        filename="test_asyncio_coroutines.py",
                        line_no=main_coro.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="outer_function",
                        filename="test_asyncio_coroutines.py",
                        line_no=outer_function.__code__.co_firstlineno + 5,
                    ),
                ],
            ),
            print_samples_on_failure=True,
        )

        # Test that we see the background_task_func task
        pprof_utils.assert_profile_has_sample(
            profile,
            samples,
            expected_sample=pprof_utils.StackEvent(
                thread_name="MainThread",
                task_name="background_wait",
                locations=[
                    pprof_utils.StackLocation(
                        function_name="sleep",
                        filename="",
                        line_no=-1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="background_task_func",
                        filename="test_asyncio_coroutines.py",
                        line_no=background_task_func.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="outer_function",
                        filename="test_asyncio_coroutines.py",
                        line_no=outer_function.__code__.co_firstlineno + 7,
                    ),
                ],
            ),
            print_samples_on_failure=True,
        )

        # Test that we see the background_math_function task in stack (wall-time) samples
        pprof_utils.assert_profile_has_sample(
            profile,
            wall_time_samples,
            expected_sample=pprof_utils.StackEvent(
                thread_name="MainThread",
                task_name="background_math",
                locations=[
                    pprof_utils.StackLocation(
                        function_name="background_math_function",
                        filename="test_asyncio_coroutines.py",
                        line_no=-1,  # any line
                    ),
                    # TODO: We should see outer_function, but for some reason we simply do not...
                    # pprof_utils.StackLocation(
                    #     function_name="outer_function",
                    #     filename="test_asyncio_coroutines.py",
                    #     line_no=outer_function.__code__.co_firstlineno + 9,
                    # ),
                ],
            ),
            print_samples_on_failure=True,
        )
