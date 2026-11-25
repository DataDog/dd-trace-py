import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_fire_and_forget",
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    ),
    err=None,
)
def test_fire_and_forget_task_is_its_own_root() -> None:
    """A task its creator never awaits renders as its own root, without the creator's frames."""
    import asyncio
    import os
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import async_run

    assert stack.is_available, stack.failure_msg

    def creator_busy(duration: float) -> None:
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            pass

    async def not_awaited_child() -> None:
        await asyncio.sleep(1.5)

    async def awaited_child() -> None:
        await asyncio.sleep(0.5)

    async def creator() -> None:
        t_not_awaited = asyncio.create_task(not_awaited_child(), name="Task-not_awaited")
        await asyncio.sleep(0)
        # Keep the creator on CPU while the child is suspended.
        creator_busy(0.5)
        await asyncio.sleep(0.5)
        # Awaiting a task still links it to the awaiting task.
        await asyncio.create_task(awaited_child(), name="Task-awaited")
        await t_not_awaited

    async def main() -> None:
        current = asyncio.current_task()
        assert current is not None
        current.set_name("Task-creator")
        await creator()

    p = profiler.Profiler()
    p.start()
    async_run(main())
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    def task_stacks(task_name: str) -> list[list[str]]:
        stacks: list[list[str]] = []
        for sample in samples:
            label = pprof_utils.get_label_with_key(profile.string_table, sample, "task name")
            if label is not None and profile.string_table[label.str] == task_name:
                stacks.append(
                    [
                        pprof_utils.get_location_from_id(profile, location_id).function_name
                        for location_id in sample.location_id
                    ]
                )
        return stacks

    not_awaited_stacks = task_stacks("Task-not_awaited")
    assert not_awaited_stacks
    assert all("not_awaited_child" in names for names in not_awaited_stacks), not_awaited_stacks[:5]
    # Before the creator awaits it, the child is not stitched under the creator, so neither the creator's
    # coroutine nor its on-CPU frames appear in the child's samples.
    standalone = [names for names in not_awaited_stacks if "creator" not in names]
    assert standalone, not_awaited_stacks[:5]
    leaked = [names for names in not_awaited_stacks if "creator_busy" in names]
    assert not leaked, leaked[:5]

    # The creator's own samples still show its busy work.
    assert any("creator_busy" in names and "creator" in names for names in task_stacks("Task-creator"))

    # An awaited task is still shown under the task awaiting it.
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="Task-awaited",
            locations=[
                pprof_utils.StackLocation(function_name="sleep", filename="", line_no=-1),
                pprof_utils.StackLocation(function_name="awaited_child", filename="", line_no=-1),
                pprof_utils.StackLocation(function_name="creator", filename="", line_no=-1),
                pprof_utils.StackLocation(function_name="main", filename="", line_no=-1),
            ],
        ),
        print_samples_on_failure=True,
    )
