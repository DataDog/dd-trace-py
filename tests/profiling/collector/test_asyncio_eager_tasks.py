import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks require Python 3.12+")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_asyncio_eager_task_first_step",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    # child: the eager child does the work.
    # grandchild: the eager child creates an eager grandchild, which does the work.
    parametrize={"WORKER": ["child", "grandchild"]},
    err=None,
)
def test_asyncio_eager_task_first_step_is_sampled_once() -> None:
    """Sample an eager task's first step once, as the eager task, under its creators' frames."""
    import asyncio
    import os
    import time

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    worker = os.environ["WORKER"]
    task_ids: dict[str, int] = {}

    def eager_step_work() -> None:
        deadline = time.thread_time_ns() + 400_000_000
        while time.thread_time_ns() < deadline:
            pass

    async def eager_grandchild() -> None:
        task_ids["grandchild"] = id(asyncio.current_task())
        eager_step_work()

    async def eager_child() -> None:
        task_ids["child"] = id(asyncio.current_task())
        if worker == "grandchild":
            await asyncio.create_task(eager_grandchild())
        else:
            eager_step_work()

    async def creator() -> None:
        loop = asyncio.get_running_loop()
        loop.set_task_factory(asyncio.eager_task_factory)  # type: ignore[attr-defined]
        try:
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
    # The creators are also running during the step. They must not get their own copies of the sample.
    assert all(pprof_utils.get_num_label(profile, sample, "task id") == task_ids[worker] for sample in samples)
    callers = ["eager_grandchild", "eager_child"] if worker == "grandchild" else ["eager_child"]
    for sample in samples:
        pprof_utils.assert_sample_has_locations(
            profile,
            sample,
            [
                pprof_utils.StackLocation(function_name=name, filename="", line_no=-1)
                for name in ["eager_step_work", *callers, "creator"]
            ],
        )
