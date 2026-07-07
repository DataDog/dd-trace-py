import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_HEAP_SAMPLE_SIZE="64",
    ),
)
def test_heap_tracker_count_present():
    """heap_tracker_count is present and non-zero when memory profiling is enabled."""
    import time

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.utils import get_all_metadata_from_agent

    p = profiler.Profiler(tracer=tracer)
    p.start()

    time.sleep(0.3)

    # Allocate objects after the profiler starts so the heap tracker sees them.
    # With DD_PROFILING_HEAP_SAMPLE_SIZE=64, even small allocations trigger samples.
    held = [bytearray(1024) for _ in range(100)]

    time.sleep(0.3)

    p.stop()

    files = get_all_metadata_from_agent(min_count=1)
    assert files, "Expected at least one metadata upload"

    for metadata in files:
        assert "heap_tracker_count" in metadata, f"Missing heap_tracker_count in metadata: {metadata}"
        assert isinstance(metadata["heap_tracker_count"], int), f"heap_tracker_count must be an integer: {metadata}"
        assert metadata["heap_tracker_count"] >= 0, f"heap_tracker_count must be non-negative: {metadata}"
        assert "heap_tracker_cap_drops" in metadata, f"Missing heap_tracker_cap_drops in metadata: {metadata}"
        assert isinstance(metadata["heap_tracker_cap_drops"], int), (
            f"heap_tracker_cap_drops must be an integer: {metadata}"
        )

    # The last file may be a partial flush, so check the penultimate one has tracked allocations
    if len(files) >= 2:
        metadata = files[-2]
        assert metadata["heap_tracker_count"] > 0, (
            f"Expected heap_tracker_count > 0 in penultimate metadata, got {metadata}. held={len(held)}"
        )

    del held
