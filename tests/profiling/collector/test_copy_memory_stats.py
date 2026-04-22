import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_copy_memory_error_count",
        DD_PROFILING_UPLOAD_INTERVAL="1",
    ),
    err=None,
)
def test_copy_memory_error_count_present():
    """copy_memory_error_count is always emitted (even when 0) and is non-negative."""
    import glob
    import json
    import os
    import time

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer

    p = profiler.Profiler(tracer=tracer)
    p.start()
    time.sleep(3)
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files = sorted(glob.glob(output_filename + ".*.internal_metadata.json"))
    assert files, "Expected at least one internal_metadata.json file"

    for f in files:
        with open(f) as fp:
            metadata = json.load(fp)
        assert "copy_memory_error_count" in metadata, f"Missing copy_memory_error_count in {f}: {metadata}"
        assert metadata["copy_memory_error_count"] >= 0, f"copy_memory_error_count must be non-negative: {metadata}"


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_memory_disabled",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        ECHION_USE_FAST_COPY_MEMORY="",
    ),
    err=None,
)
def test_fast_copy_memory_disabled():
    """fast_copy_memory_enabled is False when ECHION_USE_FAST_COPY_MEMORY is not set."""
    import glob
    import json
    import os
    import time

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer

    p = profiler.Profiler(tracer=tracer)
    p.start()
    time.sleep(3)
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files = sorted(glob.glob(output_filename + ".*.internal_metadata.json"))
    assert files, "Expected at least one internal_metadata.json file"

    for i, f in enumerate(files):
        is_last_file = i == len(files) - 1
        with open(f) as fp:
            metadata = json.load(fp)
        if not is_last_file:
            assert "fast_copy_memory_enabled" in metadata, f"Missing fast_copy_memory_enabled in {f}: {metadata}"
            assert metadata["fast_copy_memory_enabled"] is False, (
                f"Expected fast_copy_memory_enabled=false when ECHION_USE_FAST_COPY_MEMORY is unset: {metadata}"
            )


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_memory_enabled",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        ECHION_USE_FAST_COPY_MEMORY="1",
    ),
    err=None,
)
def test_fast_copy_memory_enabled():
    """fast_copy_memory_enabled is True when ECHION_USE_FAST_COPY_MEMORY=1."""
    import glob
    import json
    import os
    import time

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer

    p = profiler.Profiler(tracer=tracer)
    p.start()
    time.sleep(3)
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files = sorted(glob.glob(output_filename + ".*.internal_metadata.json"))
    assert files, "Expected at least one internal_metadata.json file"

    for i, f in enumerate(files):
        is_last_file = i == len(files) - 1
        with open(f) as fp:
            metadata = json.load(fp)
        if not is_last_file:
            assert "fast_copy_memory_enabled" in metadata, f"Missing fast_copy_memory_enabled in {f}: {metadata}"
            assert metadata["fast_copy_memory_enabled"] is True, (
                f"Expected fast_copy_memory_enabled=true when ECHION_USE_FAST_COPY_MEMORY=1: {metadata}"
            )
