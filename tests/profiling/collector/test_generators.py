import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_generators",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_generators_stacks() -> None:
    import os
    import sys
    import time
    from typing import Generator

    from ddtrace.internal.datadog.profiling import stack_v2
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    assert stack_v2.is_available, stack_v2.failure_msg

    def generator2() -> Generator[int, None, None]:
        time.sleep(0.1)
        yield 42

    def generator() -> Generator[int, None, None]:
        yield from generator2()

    def my_function() -> int:
        gen = generator()
        return next(gen)

    p = profiler.Profiler()
    p.start()

    # Run the generator code multiple times to ensure we get samples
    for _ in range(10):
        result = my_function()
        assert result == 42
        time.sleep(0.05)

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())

    profile = pprof_utils.parse_newest_profile(output_filename)

    # Get all samples
    samples = list(profile.sample)
    assert len(samples) > 0

    # In Python 3.14+, generator frames intentionally break the frame chain by setting
    # previous = NULL to prevent dangling pointers. This means we cannot unwind from
    # generator frames back to their callers. See docs/python-3.14-generator-frame-limitation.md
    # for details.
    #
    # Expected behavior:
    # - Python < 3.14: my_function -> generator -> generator2 (full stack trace)
    # - Python >= 3.14: generator -> generator2 (cannot unwind to my_function)
    if sys.version_info >= (3, 14):
        # Python 3.14+: Generator frames have previous = NULL, so we can only unwind
        # generator -> generator2, but not generator -> my_function
        pprof_utils.assert_profile_has_sample(
            profile,
            samples,
            expected_sample=pprof_utils.StackEvent(
                thread_name="MainThread",
                locations=[
                    pprof_utils.StackLocation(
                        function_name="generator2",
                        filename="test_generators.py",
                        line_no=generator2.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="generator",
                        filename="test_generators.py",
                        line_no=generator.__code__.co_firstlineno + 1,
                    ),
                ],
            ),
        )
    else:
        # Python < 3.14: Full stack trace should be available
        pprof_utils.assert_profile_has_sample(
            profile,
            samples,
            expected_sample=pprof_utils.StackEvent(
                thread_name="MainThread",
                locations=[
                    pprof_utils.StackLocation(
                        function_name="generator2",
                        filename="test_generators.py",
                        line_no=generator2.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="generator",
                        filename="test_generators.py",
                        line_no=generator.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="my_function",
                        filename="test_generators.py",
                        line_no=my_function.__code__.co_firstlineno + 2,
                    ),
                ],
            ),
        )
