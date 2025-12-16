import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_coroutines",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_coroutines() -> None:
    import asyncio
    import math
    import os
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    sleep_time = 0.2
    loop_run_time = 0.75

    async def background_task_func() -> None:
        await asyncio.sleep(2.0)

    async def background_math_function() -> None:
        s = 0.0
        for i in range(100000):
            s += math.sin(i)

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

        result = await main_coro()

        await background_task
        await math_task

        return result

    p = profiler.Profiler()
    p.start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    main_task = loop.create_task(outer_function(), name="main")
    loop.run_until_complete(main_task)

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())

    profile = pprof_utils.parse_newest_profile(output_filename)

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    # Test that we see the sub_coro function
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

    # Test that we see the background_math_function task
    pprof_utils.assert_profile_has_sample(
        profile,
        list(profile.sample),
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
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
