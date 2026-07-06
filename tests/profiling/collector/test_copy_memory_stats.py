import pytest


@pytest.mark.subprocess()
def test_copy_memory_error_count_present():
    """copy_memory_error_count is always emitted (even when 0) and is non-negative."""
    import time

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.utils import get_all_metadata_from_agent
    from tests.profiling.utils import with_profiling_test_agent

    with with_profiling_test_agent() as agent_client:
        p = profiler.Profiler(tracer=tracer)
        p.start()
        time.sleep(3)
        p.stop()

        files = get_all_metadata_from_agent(agent_client, min_count=1)
        assert files, "Expected at least one metadata upload"

        for metadata in files:
            assert "copy_memory_error_count" in metadata, f"Missing copy_memory_error_count in metadata: {metadata}"
            assert metadata["copy_memory_error_count"] >= 0, f"copy_memory_error_count must be non-negative: {metadata}"


@pytest.mark.subprocess(
    env=dict(
        _DD_PROFILING_STACK_FAST_COPY="false",
    ),
)
def test_fast_copy_memory_disabled():
    """fast_copy_memory_enabled is False when _DD_PROFILING_STACK_FAST_COPY=false."""
    import time

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.utils import get_all_metadata_from_agent
    from tests.profiling.utils import with_profiling_test_agent

    with with_profiling_test_agent() as agent_client:
        p = profiler.Profiler(tracer=tracer)
        p.start()
        time.sleep(3)
        p.stop()

        files = get_all_metadata_from_agent(agent_client, min_count=1)
        assert files, "Expected at least one metadata upload"

        for i, metadata in enumerate(files):
            is_last_file = i == len(files) - 1
            if not is_last_file:
                assert "fast_copy_memory_enabled" in metadata, (
                    f"Missing fast_copy_memory_enabled in metadata: {metadata}"
                )
                assert metadata["fast_copy_memory_enabled"] is False, (
                    f"Expected fast_copy_memory_enabled=false when _DD_PROFILING_STACK_FAST_COPY=false: {metadata}"
                )


@pytest.mark.subprocess(
    env=dict(
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
)
def test_fast_copy_memory_enabled():
    """fast_copy_memory_enabled is True when _DD_PROFILING_STACK_FAST_COPY=1."""
    import time

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.utils import get_all_metadata_from_agent
    from tests.profiling.utils import with_profiling_test_agent

    with with_profiling_test_agent() as agent_client:
        p = profiler.Profiler(tracer=tracer)
        p.start()
        time.sleep(3)
        p.stop()

        files = get_all_metadata_from_agent(agent_client, min_count=1)
        assert files, "Expected at least one metadata upload"

        for i, metadata in enumerate(files):
            is_last_file = i == len(files) - 1
            if not is_last_file:
                assert "fast_copy_memory_enabled" in metadata, (
                    f"Missing fast_copy_memory_enabled in metadata: {metadata}"
                )
                assert metadata["fast_copy_memory_enabled"] is True, (
                    f"Expected fast_copy_memory_enabled=true by default: {metadata}"
                )
