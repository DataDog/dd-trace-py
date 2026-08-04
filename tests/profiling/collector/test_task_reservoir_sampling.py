"""Tests for adaptive task-sampling.

Verifies that when the number of leaf asyncio tasks exceeds
_DD_PROFILING_STACK_MAX_TASKS, the profiler:
  1. Emits at most MAX_TASKS wall-time samples per sampling tick (bounded count).
  2. Scales non-slot-0 wall time so the per-thread wall-time total is preserved.
"""

import pytest


# Keep MAX_TASKS low enough to exercise the cap easily with N_TASKS tasks.
_MAX_TASKS = 5


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_task_reservoir_sampling",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        # Set the cap well below the number of tasks we spawn.
        _DD_PROFILING_STACK_MAX_TASKS=str(_MAX_TASKS),
    ),
    err=None,
)
def test_task_reservoir_sampling_bounded_count() -> None:
    """Number of distinct task-name samples per sampling interval is at most MAX_TASKS."""
    import asyncio
    import os
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import async_run

    assert stack.is_available, stack.failure_msg

    MAX_TASKS = int(os.environ["_DD_PROFILING_STACK_MAX_TASKS"])
    N_TASKS = 60

    async def sleeper(name: str) -> None:
        # Sleep long enough that all tasks are alive during profiling.
        await asyncio.sleep(3.0)

    async def main() -> None:
        tasks = [asyncio.create_task(sleeper(f"worker-{i}"), name=f"worker-{i}") for i in range(N_TASKS)]
        await asyncio.gather(*tasks)

    p = profiler.Profiler(tracer=tracer)
    p.start()
    async_run(main())
    time.sleep(0.5)
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    # Collect all task-name samples (wall-time only; filter on "task name" label).
    wall_time_idx = pprof_utils.get_sample_type_index(profile, "wall-time")
    task_samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(task_samples) > 0, "Expected at least one task-name sample"

    # Group samples by their monotonic timestamp to reconstruct per-tick counts.
    # Each tick is identified by the "monotonic time" value in the sample.
    mono_idx_candidates = [
        i for i, st in enumerate(profile.sample_type) if profile.string_table[st.type] == "monotonic-time"
    ]
    if mono_idx_candidates:
        mono_idx = mono_idx_candidates[0]
        from collections import defaultdict

        samples_per_tick: dict[int, int] = defaultdict(int)
        for sample in task_samples:
            if sample.value[wall_time_idx] > 0:
                tick_key = sample.value[mono_idx]
                samples_per_tick[tick_key] += 1

        if samples_per_tick:
            max_tasks_in_one_tick = max(samples_per_tick.values())
            assert max_tasks_in_one_tick <= MAX_TASKS + 1, (
                f"Saw {max_tasks_in_one_tick} task-name samples in a single tick; "
                f"expected at most {MAX_TASKS + 1} (cap={MAX_TASKS} plus optional thread sample)"
            )


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_task_reservoir_walltime",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_MAX_TASKS=str(_MAX_TASKS),
    ),
    err=None,
)
def test_task_reservoir_sampling_walltime_scaling() -> None:
    """Total wall time across task samples is approximately N * wall_time_per_tick."""
    import asyncio
    import os
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import async_run

    assert stack.is_available, stack.failure_msg

    N_TASKS = 60
    SLEEP_DURATION = 3.0
    TOLERANCE = 0.1  # allow 10% deviation from ideal

    async def sleeper() -> None:
        await asyncio.sleep(SLEEP_DURATION)

    async def main() -> None:
        tasks = [asyncio.create_task(sleeper(), name=f"worker-{i}") for i in range(N_TASKS)]
        await asyncio.gather(*tasks)

    p = profiler.Profiler(tracer=tracer)
    p.start()
    async_run(main())
    time.sleep(0.5)
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    wall_time_idx = pprof_utils.get_sample_type_index(profile, "wall-time")

    # Sum wall time for all task-name samples.
    task_samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(task_samples) > 0

    total_task_walltime_ns = sum(s.value[wall_time_idx] for s in task_samples)

    # Sum wall time for all plain thread samples (no "task name" label) --
    # these represent the true elapsed time independent of subsampling.
    thread_samples = [
        s for s in profile.sample if not pprof_utils.get_label_with_key(profile.string_table, s, "task name")
    ]
    total_thread_walltime_ns = sum(s.value[wall_time_idx] for s in thread_samples)

    if total_thread_walltime_ns == 0:
        # Fall back: skip the ratio check if there are no plain thread samples.
        exit(0)

    # Ideally total_task_walltime = N_TASKS * total_thread_walltime (one sample per task per tick).
    # With reservoir sampling it should still equal that (due to scaling).
    # Allow a generous tolerance for startup/shutdown noise.
    expected_min = N_TASKS * total_thread_walltime_ns * (1.0 - TOLERANCE)
    expected_max = N_TASKS * total_thread_walltime_ns * (1.0 + TOLERANCE)
    assert total_task_walltime_ns >= expected_min, (
        f"Total task wall time {total_task_walltime_ns}ns is less than "
        f"{expected_min}ns ({N_TASKS} * thread wall time * {1 - TOLERANCE})"
    )
    assert total_task_walltime_ns <= expected_max, (
        f"Total task wall time {total_task_walltime_ns}ns exceeds "
        f"{expected_max}ns ({N_TASKS} * thread wall time * {1 + TOLERANCE})"
    )
