"""Regression guard: unwind_greenlets reuses its per-thread StackInfo buffer.

The buffer-reuse fix keeps the `current_greenlets` vector (and each entry's
FrameStack) alive between samples, so it only allocates new StackInfo objects
when a sample exceeds the prior peak greenlet count. Before the fix, every
sample allocated a fresh StackInfo (with a std::deque<Frame>) per tracked
greenlet -- a large source of native heap churn under gevent.

RSS/arena size cannot distinguish the two implementations (on the single
sampling thread, freed blocks are reused next sample, so the footprint is
identical). What differs is the *number of allocations*. The native module
exposes a cumulative counter, ``stack._stack._greenlet_buffer_alloc_count()``, that is
incremented only on the buffer-growth path. With reuse it plateaus once the
working set is sampled; a per-sample-allocation regression makes it grow
without bound.

This test asserts the counter stops growing after warmup. It fails on builds
without the fix (the counter symbol does not exist there).
"""

import os
import sys

import pytest


GEVENT_COMPATIBLE_WITH_PYTHON_VERSION = os.getenv("DD_PROFILE_TEST_GEVENT", False) and (
    sys.version_info[:2] < (3, 13) or (sys.version_info[:2] == (3, 13) and sys.version_info[3] != "free-threading")
)


@pytest.mark.skipif(
    not GEVENT_COMPATIBLE_WITH_PYTHON_VERSION,
    reason="gevent not compatible / DD_PROFILE_TEST_GEVENT not set",
)
@pytest.mark.subprocess(
    env=dict(DD_PROFILING_OUTPUT_PPROF="/tmp/test_greenlet_buffer_reuse"),
    out=None,
    err=None,
)
def test_greenlet_unwind_buffer_reuse() -> None:
    from gevent import monkey

    monkey.patch_all()

    import gevent

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    N_IDLE = 500
    STACK_DEPTH = 30
    WARMUP_S = 3.0
    MEASURE_S = 5.0

    def _idle_deep(depth: int) -> None:
        if depth > 0:
            _idle_deep(depth - 1)
        else:
            gevent.sleep(1000)

    def idle_greenlet() -> None:
        _idle_deep(STACK_DEPTH)

    p = profiler.Profiler()
    p.start()
    stack.set_interval(0.005)  # 5ms (minimum) for aggressive sampling
    stack.set_adaptive_sampling(False)
    try:
        idles = [gevent.spawn(idle_greenlet) for _ in range(N_IDLE)]
        gevent.sleep(0.2)  # let them register

        # Warm up: across several full samples the reuse buffer grows to the
        # peak greenlet count and then stops allocating.
        gevent.sleep(WARMUP_S)
        c1 = stack._stack._greenlet_buffer_alloc_count()

        # Sustained sampling: with reuse the counter must not keep climbing.
        gevent.sleep(MEASURE_S)
        c2 = stack._stack._greenlet_buffer_alloc_count()

        gevent.killall(idles, timeout=5)
    finally:
        p.stop()

    # Sanity: the buffer was actually populated, i.e. greenlets were sampled and
    # unwound. One full sample grows one StackInfo per leaf greenlet.
    assert c1 >= N_IDLE // 2, (
        f"greenlet reuse buffer was barely populated (c1={c1}, N_IDLE={N_IDLE}); "
        f"greenlets may not have been sampled, so this guard would be vacuous."
    )

    # The actual guard: after warmup the buffer is reused, so growth ~ 0.
    # Allow generous slack for incidental greenlet churn / parent-chain entries.
    growth = c2 - c1
    assert growth <= N_IDLE // 10, (
        f"unwind_greenlets is allocating StackInfo per sample instead of reusing "
        f"its buffer: it grew by {growth} over {MEASURE_S:.0f}s after warmup "
        f"(c1={c1}, c2={c2}). Per-sample greenlet buffers are no longer reused."
    )
