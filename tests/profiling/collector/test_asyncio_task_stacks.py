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
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_asyncio_running_parent"},
    err=None,
)
def test_running_parent_sync_frames_follow_suspended_child_frames() -> None:
    import asyncio
    import os
    import sys
    import time

    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import ProfilerContextManager

    def parent_sync() -> None:
        time.sleep(1)

    async def child(ready: asyncio.Event, done: asyncio.Event) -> None:
        ready.set()
        await done.wait()

    async def parent() -> None:
        ready = asyncio.Event()
        done = asyncio.Event()
        task = asyncio.create_task(child(ready, done), name="suspended_child")
        await ready.wait()
        parent_sync()
        done.set()
        await task

    with ProfilerContextManager():
        asyncio.run(parent())

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    child_stacks = []
    for sample in pprof_utils.get_samples_with_function(profile, samples, "parent_sync"):
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "task name")
        if label is not None and profile.string_table[label.str] == "suspended_child":
            locations = [pprof_utils.get_location_from_id(profile, location_id) for location_id in sample.location_id]
            child_stacks.append([loc.function_name for loc in locations if loc.filename != "<native>"])

    assert child_stacks
    wait_name = "Event.wait" if sys.version_info >= (3, 11) else "wait"
    assert all(names[:4] == [wait_name, "child", "parent_sync", "parent"] for names in child_stacks), child_stacks


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
