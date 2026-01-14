from sys import version_info as PYVERSION

import pytest


@pytest.mark.xfail(
    condition=PYVERSION >= (3, 13), reason="Sampling async context manager stacks does not work on >=3.13"
)
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_context_manager",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_context_manager_wall_time() -> None:
    import asyncio
    import os
    from sys import version_info as PYVERSION
    from types import TracebackType
    from typing import Union

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    async def context_manager_dep() -> None:
        await asyncio.sleep(0.25)

    async def some_function() -> None:
        await asyncio.sleep(0.25)

    class AsyncContextManager:
        async def __aenter__(self) -> None:
            await context_manager_dep()

        async def __aexit__(
            self,
            exc_type: Union[type, None],
            exc_value: Union[object, None],
            traceback: Union[TracebackType, None],
        ) -> None:
            await asyncio.sleep(0.25)

    async def asynchronous_function() -> None:
        async with AsyncContextManager():
            await some_function()

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

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    # Test that we see the context manager functions
    if PYVERSION >= (3, 11):
        # Context Manager Enter
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
                        function_name="context_manager_dep",
                        filename="test_asyncio_context_manager.py",
                        line_no=context_manager_dep.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="AsyncContextManager.__aenter__",
                        filename="test_asyncio_context_manager.py",
                        line_no=AsyncContextManager.__aenter__.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="asynchronous_function",
                        filename="test_asyncio_context_manager.py",
                        line_no=asynchronous_function.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="main",
                        filename="test_asyncio_context_manager.py",
                        line_no=main.__code__.co_firstlineno + 1,
                    ),
                ],
            ),
        )

        # Actual function call
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
                        function_name="some_function",
                        filename="test_asyncio_context_manager.py",
                        line_no=some_function.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="asynchronous_function",
                        filename="test_asyncio_context_manager.py",
                        line_no=asynchronous_function.__code__.co_firstlineno + 2,
                    ),
                    pprof_utils.StackLocation(
                        function_name="main",
                        filename="test_asyncio_context_manager.py",
                        line_no=main.__code__.co_firstlineno + 1,
                    ),
                ],
            ),
        )

        # Context Manager Exit
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
                        function_name="AsyncContextManager.__aexit__",
                        filename="test_asyncio_context_manager.py",
                        line_no=AsyncContextManager.__aexit__.__code__.co_firstlineno + 6,
                    ),
                    pprof_utils.StackLocation(
                        function_name="asynchronous_function",
                        filename="test_asyncio_context_manager.py",
                        line_no=asynchronous_function.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="main",
                        filename="test_asyncio_context_manager.py",
                        line_no=main.__code__.co_firstlineno + 1,
                    ),
                ],
            ),
        )
    else:
        # Context Manager Enter
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
                        function_name="context_manager_dep",
                        filename="test_asyncio_context_manager.py",
                        line_no=context_manager_dep.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="__aenter__",
                        filename="test_asyncio_context_manager.py",
                        line_no=AsyncContextManager.__aenter__.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="asynchronous_function",
                        filename="test_asyncio_context_manager.py",
                        line_no=asynchronous_function.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="main",
                        filename="test_asyncio_context_manager.py",
                        line_no=main.__code__.co_firstlineno + 1,
                    ),
                ],
            ),
        )

        # Actual function call
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
                        function_name="some_function",
                        filename="test_asyncio_context_manager.py",
                        line_no=some_function.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="asynchronous_function",
                        filename="test_asyncio_context_manager.py",
                        line_no=asynchronous_function.__code__.co_firstlineno + 2,
                    ),
                    pprof_utils.StackLocation(
                        function_name="main",
                        filename="test_asyncio_context_manager.py",
                        line_no=main.__code__.co_firstlineno + 1,
                    ),
                ],
            ),
        )

        # Context Manager Exit
        if PYVERSION >= (3, 10):
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
                            function_name="__aexit__",
                            filename="test_asyncio_context_manager.py",
                            line_no=AsyncContextManager.__aexit__.__code__.co_firstlineno + 6,
                        ),
                        pprof_utils.StackLocation(
                            function_name="asynchronous_function",
                            filename="test_asyncio_context_manager.py",
                            line_no=asynchronous_function.__code__.co_firstlineno + 1,
                        ),
                        pprof_utils.StackLocation(
                            function_name="main",
                            filename="test_asyncio_context_manager.py",
                            line_no=main.__code__.co_firstlineno + 1,
                        ),
                    ],
                ),
            )
        else:
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
                            function_name="__aexit__",
                            filename="test_asyncio_context_manager.py",
                            line_no=AsyncContextManager.__aexit__.__code__.co_firstlineno + 6,
                        ),
                        pprof_utils.StackLocation(
                            function_name="asynchronous_function",
                            filename="test_asyncio_context_manager.py",
                            line_no=asynchronous_function.__code__.co_firstlineno + 2,
                        ),
                        pprof_utils.StackLocation(
                            function_name="main",
                            filename="test_asyncio_context_manager.py",
                            line_no=main.__code__.co_firstlineno + 1,
                        ),
                    ],
                ),
            )
