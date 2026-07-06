import pytest


@pytest.mark.subprocess
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_generators_stacks() -> None:
    import time
    from typing import Generator

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.utils import with_profiling_test_agent

    assert stack.is_available, stack.failure_msg

    def generator2() -> Generator[int, None, None]:
        time.sleep(0.1)
        yield 42

    def generator() -> Generator[int, None, None]:
        yield from generator2()

    def my_function() -> int:
        gen = generator()
        return next(gen)

    with with_profiling_test_agent() as agent_client:
        p = profiler.Profiler()
        p.start()

        # Run the generator code multiple times to ensure we get samples
        for _ in range(10):
            result = my_function()
            assert result == 42
            time.sleep(0.05)

        p.stop()

        profile = pprof_utils.get_profile_from_agent(agent_client)

    # Get all samples
    samples = list(profile.sample)
    assert len(samples) > 0

    # Test that we have samples with the expected stack trace
    # Main Thread should have: my_function -> generator -> generator2
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
