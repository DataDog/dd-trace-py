import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_unwind_tasks_rss_stable",
    ),
    out=None,
    err=None,
)
def test_asyncio_unwind_tasks_rss_stable() -> None:
    """Verify sustained asyncio task unwinding does not grow RSS unboundedly.

    Before the fix, every sample iteration allocated a fresh StackInfo (with an
    internal FrameStack buffer) per leaf asyncio task. In services with many
    long-lived idle tasks this created continuous allocator churn. After the fix
    the per-thread StackInfo/FrameStack buffers are reused across samples, so RSS
    should plateau quickly after a warmup window.
    """
    import asyncio
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from ddtrace.vendor import psutil

    N_IDLE = 1000
    STACK_DEPTH = 30
    WARMUP_S = 3.0
    MEASURE_S = 10.0
    SAMPLE_PERIOD_S = 1.0
    MAX_GROWTH_MB = 20  # generous; regressions typically show hundreds of MB

    async def _idle_deep(depth):
        if depth > 0:
            await _idle_deep(depth - 1)
        else:
            await asyncio.sleep(1000)

    async def main():
        idles = [asyncio.create_task(_idle_deep(STACK_DEPTH)) for _ in range(N_IDLE)]
        await asyncio.sleep(0.1)  # let tasks register and reach asyncio.sleep()

        proc = psutil.Process()
        t_end = time.monotonic() + WARMUP_S
        while time.monotonic() < t_end:
            await asyncio.sleep(SAMPLE_PERIOD_S)

        rss_after_warmup = proc.memory_info().rss

        samples = []
        t_end = time.monotonic() + MEASURE_S
        while time.monotonic() < t_end:
            await asyncio.sleep(SAMPLE_PERIOD_S)
            samples.append(proc.memory_info().rss)

        for task in idles:
            task.cancel()
        await asyncio.gather(*idles, return_exceptions=True)

        return rss_after_warmup, samples

    p = profiler.Profiler()
    p.start()
    stack.set_interval(0.005)  # 5ms (minimum allowed) for aggressive sampling
    stack.set_adaptive_sampling(False)
    try:
        rss_after_warmup, samples = asyncio.run(main())
    finally:
        p.stop()

    peak_rss = max(samples)
    growth_bytes = peak_rss - rss_after_warmup
    growth_mb = growth_bytes / (1024 * 1024)

    assert growth_mb < MAX_GROWTH_MB, (
        f"RSS grew {growth_mb:.1f} MB during {MEASURE_S:.0f}s of sustained "
        f"unwind_tasks sampling (after {WARMUP_S:.0f}s warmup, "
        f"baseline {rss_after_warmup / (1024 * 1024):.1f} MB, peak "
        f"{peak_rss / (1024 * 1024):.1f} MB). Per-sample stack-collector "
        f"buffers may no longer be reused."
    )
