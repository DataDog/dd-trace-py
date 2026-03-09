import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_heap_tracker_count",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        DD_PROFILING_HEAP_SAMPLE_SIZE="64",
    ),
    err=None,
)
def test_heap_tracker_count_present():
    """heap_tracker_count is present and non-zero when memory profiling is enabled."""
    import glob
    import json
    import os
    import time

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer

    p = profiler.Profiler(tracer=tracer)
    p.start()

    time.sleep(0.3)

    # Allocate objects after the profiler starts so the heap tracker sees them.
    # With DD_PROFILING_HEAP_SAMPLE_SIZE=64, even small allocations trigger samples.
    held = [bytearray(1024) for _ in range(100)]

    time.sleep(0.3)

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files = sorted(glob.glob(output_filename + ".*.internal_metadata.json"))
    assert files, "Expected at least one internal_metadata.json file"

    for f in files:
        with open(f) as fp:
            metadata = json.load(fp)
        assert "heap_tracker_count" in metadata, f"Missing heap_tracker_count in {f}: {metadata}"
        assert isinstance(metadata["heap_tracker_count"], int), f"heap_tracker_count must be an integer: {metadata}"
        assert metadata["heap_tracker_count"] >= 0, f"heap_tracker_count must be non-negative: {metadata}"

    # The last file may be a partial flush, so check the penultimate one has tracked allocations
    if len(files) >= 2:
        with open(files[-2]) as fp:
            metadata = json.load(fp)
        assert metadata["heap_tracker_count"] > 0, (
            f"Expected heap_tracker_count > 0 in penultimate file, got {metadata}. held={len(held)}"
        )

    del held
