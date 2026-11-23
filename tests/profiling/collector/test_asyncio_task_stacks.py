from importlib.util import find_spec

import pytest


@pytest.mark.subprocess(
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_asyncio_off_cpu_sibling"},
    err=None,
)
def test_suspended_task_does_not_inherit_running_sibling_frames() -> None:
    import asyncio
    import os
    import time

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    def busy_sync() -> None:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            pass

    async def busy(done: asyncio.Event) -> None:
        busy_sync()
        done.set()

    async def sleeper(done: asyncio.Event) -> None:
        await done.wait()

    async def main() -> None:
        done = asyncio.Event()
        await asyncio.gather(asyncio.create_task(sleeper(done), name="sleeper"), asyncio.create_task(busy(done)))

    p = profiler.Profiler()
    p.start()
    asyncio.run(main())
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)
    sleeper_stacks = []
    for sample in pprof_utils.get_samples_with_label_key(profile, "task name"):
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "task name")
        if label is not None and profile.string_table[label.str] == "sleeper":
            sleeper_stacks.append(
                [
                    pprof_utils.get_location_from_id(profile, location_id).function_name
                    for location_id in sample.location_id
                ]
            )

    assert sleeper_stacks
    leaked = [names for names in sleeper_stacks if {"busy_sync", "busy"}.intersection(names)]
    assert not leaked, leaked


@pytest.mark.subprocess(
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_asyncio_switching_sibling"},
    err=None,
)
def test_suspended_task_does_not_inherit_switching_sibling_frames() -> None:
    # The sampler reads the thread stack while the thread keeps running. When the running
    # coroutine suspends between two reads, the stack ends at that coroutine instead of
    # reaching the event loop. A sibling that suspends thousands of times per second
    # makes such stacks common.
    import asyncio
    import os
    import time

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    p = profiler.Profiler()

    async def switching(done: asyncio.Event) -> None:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            await asyncio.sleep(0)
        # Stop before waking sleeper so that every sample sees it suspended.
        p.stop()
        done.set()

    async def sleeper(done: asyncio.Event) -> None:
        await done.wait()

    async def main() -> None:
        done = asyncio.Event()
        await asyncio.gather(asyncio.create_task(sleeper(done), name="sleeper"), asyncio.create_task(switching(done)))

    p.start()
    asyncio.run(main())

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)
    sleeper_stacks = []
    for sample in pprof_utils.get_samples_with_label_key(profile, "task name"):
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "task name")
        if label is not None and profile.string_table[label.str] == "sleeper":
            sleeper_stacks.append(
                [
                    pprof_utils.get_location_from_id(profile, location_id).function_name
                    for location_id in sample.location_id
                ]
            )

    assert sleeper_stacks
    leaked = [names for names in sleeper_stacks if {"switching", "sleep", "__sleep0"}.intersection(names)]
    assert not leaked, leaked


@pytest.mark.skipif(find_spec("uvloop") is None, reason="uvloop is not installed in this environment")
@pytest.mark.subprocess(
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_asyncio_uvloop_wrapper"},
    err=None,
)
def test_uvloop_running_task_stack_omits_wrapper() -> None:
    import os
    import time

    import uvloop

    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import ProfilerContextManager

    def sync_leaf() -> None:
        time.sleep(1)

    async def main() -> None:
        sync_leaf()

    with ProfilerContextManager():
        uvloop.run(main())

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    stacks = [
        [pprof_utils.get_location_from_id(profile, location_id).function_name for location_id in sample.location_id]
        for sample in samples
    ]
    assert any("sync_leaf" in names and "main" in names for names in stacks), stacks
    wrappers = {"wrapper", "run.<locals>.wrapper"}
    assert not any(wrappers.intersection(names) for names in stacks), stacks
