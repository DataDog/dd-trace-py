import pytest


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        DD_PROFILING_ENABLED="1",
        DD_PROFILING_HEAP_SAMPLE_SIZE="128",
    ),
    err=None,
)
def test_memalloc_no_crash_on_fork_with_allocations() -> None:
    """Test that memory profiler doesn't crash on fork when allocations exist.

    Regression test for IR-48649: ensures that forking with active memory
    allocations tracked by the profiler doesn't cause crashes due to
    freed string IDs in libdatadog.
    """
    import gc
    import os
    import time

    def create_deep_stack_allocations() -> list[bytearray]:
        """Create allocations with deep stack traces."""

        def deep_function_level_5() -> bytearray:
            return bytearray(8192)

        def deep_function_level_4() -> bytearray:
            return deep_function_level_5()

        def deep_function_level_3() -> bytearray:
            return deep_function_level_4()

        def deep_function_level_2() -> bytearray:
            return deep_function_level_3()

        def deep_function_level_1() -> bytearray:
            return deep_function_level_2()

        data: list[bytearray] = []
        for _ in range(100):
            data.append(deep_function_level_1())
            data.append(bytearray(16384))
        return data

    def force_memory_operations() -> None:
        """Force memory operations that trigger untrack calls."""
        for _ in range(50):
            temp_objs = [bytearray(32768) for _ in range(10)]
            del temp_objs
            gc.collect()

    # Give profiler time to start
    time.sleep(0.5)

    # Create allocations to populate string interning
    data = create_deep_stack_allocations()
    time.sleep(0.2)

    # Fork - child should not crash
    pid = os.fork()
    if pid == 0:
        # Child process
        time.sleep(0.1)
        force_memory_operations()
        create_deep_stack_allocations()
        gc.collect()
        os._exit(0)
    else:
        # Parent process
        _, status = os.waitpid(pid, 0)
        # Child should exit cleanly (not via signal)
        assert not os.WIFSIGNALED(status), f"Child crashed with signal {os.WTERMSIG(status)}"
        assert os.WEXITSTATUS(status) == 0

    del data
