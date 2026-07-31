"""Tests for adaptive task-sampling.

Verifies that when the number of leaf asyncio tasks exceeds
_DD_PROFILING_STACK_MAX_TASKS, the profiler:
  1. Emits at most MAX_TASKS wall-time samples per sampling tick (bounded count).
  2. Scales non-slot-0 wall time so the per-thread wall-time total is preserved.

Both tests reconstruct sampling ticks from the numeric "end_timestamp_ns" label: every
sample produced during one thread-sampling cycle carries the same monotonic timestamp,
because render_task_begin reuses thread_state.now_time_ns recorded by render_thread_begin.

Note that these tests deliberately do not shorten DD_PROFILING_UPLOAD_INTERVAL. With a
short interval the profiler rotates pprof files while the workload runs, and the newest
file (the one flushed by p.stop()) covers only the window after the event loop is gone,
where no asyncio task exists.
"""

import pytest


# Keep the cap low enough that the tasks each test spawns comfortably exceed it. Only the cap
# can be shared here: subprocess test bodies run in a fresh interpreter and cannot see module
# globals, so they reread it from the environment.
_MAX_TASKS = 5


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_task_reservoir_sampling",
        # Set the cap well below the number of tasks we spawn.
        _DD_PROFILING_STACK_MAX_TASKS=str(_MAX_TASKS),
    ),
    err=None,
)
def test_task_reservoir_sampling_bounded_count() -> None:
    """No sampling tick emits more than MAX_TASKS task samples, and the cap does engage."""
    import asyncio
    from collections import defaultdict
    import os

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    MAX_TASKS = int(os.environ["_DD_PROFILING_STACK_MAX_TASKS"])
    N_TASKS = 60

    async def sleeper() -> None:
        # Sleep long enough that all tasks are alive across many sampling ticks.
        await asyncio.sleep(3.0)

    async def main() -> None:
        tasks = [asyncio.create_task(sleeper(), name=f"worker-{i}") for i in range(N_TASKS)]
        await asyncio.gather(*tasks)

    p = profiler.Profiler()
    p.start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    task_samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(task_samples) > 0, "Expected at least one task-name sample"

    # Group by (thread, tick) so that concurrent event loops cannot be conflated.
    samples_per_tick: dict[tuple[int, int], int] = defaultdict(int)
    for sample in task_samples:
        tick = pprof_utils.get_label_with_key(profile.string_table, sample, "end_timestamp_ns")
        thread = pprof_utils.get_label_with_key(profile.string_table, sample, "thread id")
        assert tick is not None, "Task sample is missing the 'end_timestamp_ns' label"
        assert thread is not None, "Task sample is missing the 'thread id' label"
        samples_per_tick[(thread.num, tick.num)] += 1

    max_in_one_tick = max(samples_per_tick.values())
    assert max_in_one_tick <= MAX_TASKS, (
        f"Saw {max_in_one_tick} task samples in a single tick, expected at most {MAX_TASKS}. "
        f"Per-tick counts: {sorted(samples_per_tick.values(), reverse=True)[:10]}"
    )
    # With N_TASKS well above the cap the reservoir has to fill up, so a tick that emits
    # exactly MAX_TASKS samples must exist. Without this the bound above is vacuous.
    assert max_in_one_tick == MAX_TASKS, (
        f"Reservoir cap never engaged: the busiest tick had {max_in_one_tick} task samples, "
        f"expected {MAX_TASKS} with {N_TASKS} concurrent tasks"
    )


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_task_reservoir_walltime",
        _DD_PROFILING_STACK_MAX_TASKS=str(_MAX_TASKS),
    ),
    err=None,
)
def test_task_reservoir_sampling_walltime_scaling() -> None:
    """Wall-time scaling preserves the per-tick total across the sampled reservoir.

    Within one tick slot 0 carries the unscaled thread interval T and each of the other
    n_selected-1 slots carries T * (n_total-1)/(n_selected-1), so the tick total is
    T + (n_total-1)*T == n_total * T. Since slot 0 is the only unscaled slot it is also the
    smallest, which makes sum/min == n_total a self-contained check that needs no reference
    thread: a thread that has tasks emits no task-less sample, because render_task_begin
    reuses the sample render_thread_begin created for the first task.
    """
    import asyncio
    from collections import defaultdict
    import os
    import statistics

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    MAX_TASKS = int(os.environ["_DD_PROFILING_STACK_MAX_TASKS"])
    N_TASKS = 60
    TOLERANCE = 0.15  # allow 15% deviation from the ideal ratio

    async def sleeper() -> None:
        await asyncio.sleep(3.0)

    async def main() -> None:
        tasks = [asyncio.create_task(sleeper(), name=f"worker-{i}") for i in range(N_TASKS)]
        await asyncio.gather(*tasks)

    p = profiler.Profiler()
    p.start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    wall_time_idx = pprof_utils.get_sample_type_index(profile, "wall-time")
    task_samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(task_samples) > 0, "Expected at least one task-name sample"

    walltimes_per_tick: dict[tuple[int, int], list[int]] = defaultdict(list)
    for sample in task_samples:
        tick = pprof_utils.get_label_with_key(profile.string_table, sample, "end_timestamp_ns")
        thread = pprof_utils.get_label_with_key(profile.string_table, sample, "thread id")
        assert tick is not None, "Task sample is missing the 'end_timestamp_ns' label"
        assert thread is not None, "Task sample is missing the 'thread id' label"
        walltimes_per_tick[(thread.num, tick.num)].append(sample.value[wall_time_idx])

    # Only ticks that filled the reservoir tell us about scaling; partially populated ticks
    # happen while the tasks are still being created or are winding down.
    ratios = [
        sum(walltimes) / min(walltimes)
        for walltimes in walltimes_per_tick.values()
        if len(walltimes) == MAX_TASKS and min(walltimes) > 0
    ]
    assert ratios, (
        f"No tick emitted {MAX_TASKS} task samples with non-zero wall time; "
        f"per-tick sample counts: {sorted({len(w) for w in walltimes_per_tick.values()})}"
    )

    # The median keeps the check robust against the few ramp-up/ramp-down ticks that see
    # fewer than N_TASKS leaf tasks and therefore scale by a smaller factor.
    median_ratio = statistics.median(ratios)
    assert abs(median_ratio - N_TASKS) <= N_TASKS * TOLERANCE, (
        f"Median per-tick wall-time ratio {median_ratio:.1f} deviates from the expected "
        f"{N_TASKS} by more than {TOLERANCE:.0%} (scaling should preserve the per-thread total). "
        f"Sampled {len(ratios)} full ticks."
    )
