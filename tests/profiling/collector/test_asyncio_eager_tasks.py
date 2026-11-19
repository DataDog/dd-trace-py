import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks require Python 3.12+")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_asyncio_eager_task_first_step",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        # Timestamps let the test group samples by sampling tick.
        "DD_PROFILING_TIMELINE_ENABLED": "1",
    },
    # child: the eager child does the work.
    # grandchild: the eager child creates an eager grandchild, which does the work.
    parametrize={"WORKER": ["child", "grandchild"]},
    err=None,
)
def test_asyncio_eager_task_first_step_is_sampled_once() -> None:
    """Sample an eager task's first step once, as the eager task, under its creators' frames."""
    import asyncio
    from collections import Counter
    import os
    import time

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    worker = os.environ["WORKER"]
    task_ids: dict[str, int] = {}
    p = profiler.Profiler()

    def eager_step_work() -> None:
        deadline = time.thread_time_ns() + 400_000_000
        while time.thread_time_ns() < deadline:
            pass

    def profile_eager_step() -> None:
        eager_step_work()
        # Stop before the eager task can finish between the sampler's stack and task reads.
        p.stop()

    async def eager_grandchild() -> None:
        task_ids["grandchild"] = id(asyncio.current_task())
        profile_eager_step()

    async def eager_child() -> None:
        task_ids["child"] = id(asyncio.current_task())
        if worker == "grandchild":
            await asyncio.create_task(eager_grandchild())
        else:
            profile_eager_step()

    async def creator() -> None:
        loop = asyncio.get_running_loop()
        loop.set_task_factory(asyncio.eager_task_factory)  # type: ignore[attr-defined]
        try:
            await asyncio.create_task(eager_child())
        finally:
            loop.set_task_factory(None)

    p.start()
    asyncio.run(creator())

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    samples = pprof_utils.get_samples_with_function(profile, wall_samples, "eager_step_work")

    assert samples
    # Every sample from one sampling tick of a thread has the same timestamp. A second sample in a tick would count
    # that tick's wall and CPU time twice.
    samples_per_tick = Counter(
        (
            pprof_utils.get_label_with_key(profile.string_table, sample, "thread id").num,
            pprof_utils.get_label_with_key(profile.string_table, sample, "end_timestamp_ns").num,
        )
        for sample in samples
    )
    assert set(samples_per_tick.values()) == {1}, samples_per_tick
    # The creators are also running during the step. They must not get their own copies of the sample.
    assert all(pprof_utils.get_num_label(profile, sample, "task id") == task_ids[worker] for sample in samples)
    callers = ["eager_grandchild", "eager_child"] if worker == "grandchild" else ["eager_child"]
    for sample in samples:
        pprof_utils.assert_sample_has_locations(
            profile,
            sample,
            [
                pprof_utils.StackLocation(function_name=name, filename="", line_no=-1)
                for name in ["eager_step_work", "profile_eager_step", *callers, "creator"]
            ],
        )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks require Python 3.12+")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_asyncio_eager_task_step_edges",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        # Timestamps let the test group samples by sampling tick.
        "DD_PROFILING_TIMELINE_ENABLED": "1",
    },
    err=None,
)
def test_asyncio_eager_task_step_edges_are_sampled_once() -> None:
    """Sample a tick once even when an eager step starts or ends while the sampler reads the thread."""
    import asyncio
    from collections import Counter
    import os
    import time

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    def eager_step_work() -> None:
        deadline = time.thread_time_ns() + 200_000
        while time.thread_time_ns() < deadline:
            pass

    async def eager_child() -> None:
        eager_step_work()

    # Many short steps, so that many ticks land on the edge of a step.
    async def creator() -> None:
        loop = asyncio.get_running_loop()
        loop.set_task_factory(asyncio.eager_task_factory)  # type: ignore[attr-defined]
        try:
            for _ in range(10000):
                await asyncio.create_task(eager_child())
        finally:
            loop.set_task_factory(None)

    p = profiler.Profiler()
    p.start()
    asyncio.run(creator())
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    samples = pprof_utils.get_samples_with_function(profile, wall_samples, "eager_step_work")

    assert samples
    samples_per_tick = Counter(
        (
            pprof_utils.get_label_with_key(profile.string_table, sample, "thread id").num,
            pprof_utils.get_label_with_key(profile.string_table, sample, "end_timestamp_ns").num,
        )
        for sample in samples
    )
    assert set(samples_per_tick.values()) == {1}, {tick: n for tick, n in samples_per_tick.items() if n > 1}
