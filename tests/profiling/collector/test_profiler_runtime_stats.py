import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_UPLOAD_INTERVAL="1",
    ),
    err=None,
)
def test_get_profiler_runtime_stats_returns_counters():
    """After the profiler runs for a few seconds, get_profiler_runtime_stats()
    returns a dict with non-zero cumulative counters and gauge values.
    """
    import time

    from ddtrace.internal.datadog.profiling.ddup._ddup import get_profiler_runtime_stats
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer

    # Before the profiler starts, should return None
    assert get_profiler_runtime_stats() is None

    p = profiler.Profiler(tracer=tracer)
    p.start()
    time.sleep(3)

    stats = get_profiler_runtime_stats()
    assert stats is not None, "Expected stats dict after profiler has been running"

    # Cumulative counters must be present and non-negative
    assert stats["sample_count"] > 0, f"Expected sample_count > 0, got {stats['sample_count']}"
    assert stats["sampling_event_count"] > 0, f"Expected sampling_event_count > 0, got {stats['sampling_event_count']}"
    assert stats["copy_memory_error_count"] >= 0
    assert stats["sample_capture_cpu_time_us"] >= 0

    # Gauge values (at least sampling_interval_us should be present after sampling cycles)
    assert "sampling_interval_us" in stats, f"Expected sampling_interval_us in stats, got keys: {list(stats.keys())}"
    assert stats["sampling_interval_us"] > 0

    p.stop()


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_UPLOAD_INTERVAL="1",
    ),
    err=None,
)
def test_cumulative_counters_monotonically_increase():
    """Cumulative counter values returned by get_profiler_runtime_stats()
    must not decrease between reads, even across upload intervals.
    """
    import time

    from ddtrace.internal.datadog.profiling.ddup._ddup import get_profiler_runtime_stats
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer

    p = profiler.Profiler(tracer=tracer)
    p.start()
    time.sleep(2)

    stats1 = get_profiler_runtime_stats()
    assert stats1 is not None

    time.sleep(2)

    stats2 = get_profiler_runtime_stats()
    assert stats2 is not None

    for key in ("sample_count", "sampling_event_count", "copy_memory_error_count", "sample_capture_cpu_time_us"):
        assert stats2[key] >= stats1[key], f"Cumulative counter {key} decreased: {stats1[key]} -> {stats2[key]}"

    p.stop()
