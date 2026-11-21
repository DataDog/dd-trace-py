from importlib.util import find_spec
import sys

import pytest


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_MAX_FRAMES": "4",
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_asyncio_task_frame_budget",
    },
    err=None,
)
def test_task_frames_take_priority_when_the_budget_is_full() -> None:
    import asyncio
    import os
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import async_run

    assert stack.is_available, stack.failure_msg

    def sync_code() -> None:
        deadline = time.time() + 1
        while time.time() < deadline:
            pass

    async def inner3() -> None:
        sync_code()

    async def inner2() -> None:
        await inner3()

    async def inner1() -> None:
        await asyncio.create_task(inner2())

    async def outer() -> None:
        await inner1()

    async def async_main() -> None:
        await outer()

    p = profiler.Profiler()
    p.start()
    async_run(async_main())
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    actual = []
    for sample in pprof_utils.get_samples_with_label_key(profile, "task name"):
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "task name")
        if label is not None and profile.string_table[label.str] == "Task-2":
            actual.append(
                [
                    pprof_utils.get_location_from_id(profile, location_id).function_name
                    for location_id in sample.location_id
                ]
            )

    expected = ["inner2", "inner1", "outer", "async_main"]
    assert expected in actual, actual


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_MAX_FRAMES": "4",
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_asyncio_deep_sync_frame_budget",
    },
    err=None,
)
def test_deep_sync_stack_does_not_hide_task_context() -> None:
    import asyncio
    import os
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import async_run

    assert stack.is_available, stack.failure_msg

    def deep_sync(depth: int) -> None:
        if depth > 0:
            deep_sync(depth - 1)
            return

        deadline = time.time() + 1
        while time.time() < deadline:
            pass

    async def deep_task() -> None:
        deep_sync(20)

    async def async_main() -> None:
        await asyncio.create_task(deep_task())

    p = profiler.Profiler()
    p.start()
    async_run(async_main())
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    actual = [
        [pprof_utils.get_location_from_id(profile, location_id).function_name for location_id in sample.location_id]
        for sample in pprof_utils.get_samples_with_label_key(profile, "task name")
    ]

    assert any("deep_task" in names for names in actual), actual


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_MAX_FRAMES": "8",
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_asyncio_mixed_frame_budget",
    },
    err=None,
)
def test_task_stack_uses_remaining_budget_for_sync_context() -> None:
    import asyncio
    import os
    import re
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import async_run

    assert stack.is_available, stack.failure_msg

    def sync_code() -> None:
        deadline = time.time() + 1
        while time.time() < deadline:
            pass

    async def inner3() -> None:
        sync_code()

    async def inner2() -> None:
        await inner3()

    async def inner1() -> None:
        await asyncio.create_task(inner2())

    async def outer() -> None:
        await inner1()

    async def async_main() -> None:
        await outer()

    p = profiler.Profiler()
    p.start()
    async_run(async_main())
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    actual = []
    for sample in pprof_utils.get_samples_with_label_key(profile, "task name"):
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "task name")
        if label is not None and profile.string_table[label.str] == "Task-2":
            actual.append(
                [
                    pprof_utils.get_location_from_id(profile, location_id).function_name
                    for location_id in sample.location_id
                ]
            )

    assert any(
        len(names) <= 8 and "sync_code" in names and re.fullmatch(r"<[1-9][0-9]* frames? truncated>", names[-1])
        for names in actual
    ), actual


@pytest.mark.subprocess(
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_asyncio_collection_frame_limit"},
    err=None,
)
def test_asyncio_collection_honors_frame_limit() -> None:
    import asyncio
    import os
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    pprof_prefix = os.environ["DD_PROFILING_OUTPUT_PPROF"]
    ddup.config(env="test", service="test", version="0.0.0", max_nframes=64, output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def sync_leaf() -> None:
        time.sleep(0.5)

    async def inner() -> None:
        sync_leaf()

    async def outer() -> None:
        await inner()

    with stack.StackCollector(nframes=1):
        assert _stack._get_frame_limits() == (1, 1024)
        asyncio.run(outer())

    ddup.upload()
    profile = pprof_utils.parse_newest_profile(pprof_prefix + "." + str(os.getpid()))
    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            locations=[pprof_utils.StackLocation(function_name="outer", filename="", line_no=-1)],
        ),
        print_samples_on_failure=True,
    )


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
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_asyncio_uvloop_missing_boundary"},
    parametrize={"DD_PROFILING_MAX_FRAMES": ["1", "3"], "DD_PROFILING_STACK_NATIVE_FRAMES": ["true", "false"]},
    err=None,
)
def test_uvloop_keeps_physical_leaf_when_boundary_is_missing() -> None:
    import asyncio
    import os
    import time

    import uvloop

    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import ProfilerContextManager

    def sync_leaf() -> None:
        time.sleep(1)

    async def main() -> None:
        task = asyncio.current_task()
        assert task is not None
        task.set_name("uvloop_main")
        sync_leaf()

    with ProfilerContextManager():
        uvloop.run(main())

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)
    max_frames = int(os.environ["DD_PROFILING_MAX_FRAMES"])
    stacks = set()
    for sample in pprof_utils.get_samples_with_label_key(profile, "task name"):
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "task name")
        if label is not None and profile.string_table[label.str] == "uvloop_main":
            stacks.add(
                tuple(
                    pprof_utils.get_location_from_id(profile, location_id).function_name
                    for location_id in sample.location_id
                )
            )

    assert stacks
    assert all(0 < len(names) <= max_frames for names in stacks), stacks
    assert not any({"wrapper", "run.<locals>.wrapper"}.intersection(names) for names in stacks), stacks
    assert any("sync_leaf" in names for names in stacks), stacks


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_MAX_FRAMES": "1",
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_asyncio_native_call_frame_budget",
        "DD_PROFILING_STACK_NATIVE_FRAMES": "true",
    },
    err=None,
)
def test_one_frame_budget_keeps_coroutine_calling_native_code() -> None:
    import asyncio
    import os
    import time

    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import ProfilerContextManager

    async def native_call() -> None:
        time.sleep(1)

    async def main() -> None:
        await asyncio.create_task(native_call(), name="native_call")

    with ProfilerContextManager():
        asyncio.run(main())

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)
    stacks = set()
    for sample in pprof_utils.get_samples_with_label_key(profile, "task name"):
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "task name")
        if label is not None and profile.string_table[label.str] == "native_call":
            stacks.add(
                tuple(
                    pprof_utils.get_location_from_id(profile, location_id).function_name
                    for location_id in sample.location_id
                )
            )

    assert stacks == {("native_call",)}, stacks


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Native C frame tracking requires Python 3.12+")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_MAX_FRAMES": "2",
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_asyncio_gc_frame_budget",
        "DD_PROFILING_STACK_GC_ENABLED": "true",
        "DD_PROFILING_STACK_NATIVE_FRAMES": "true",
    },
    err=None,
)
def test_two_frame_budget_keeps_collecting_coroutine_and_gc_marker() -> None:
    import asyncio
    import gc
    import os

    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.gc_utils import slow_cyclic_collection_coroutine
    from tests.profiling.collector.test_utils import ProfilerContextManager

    gc.disable()

    with ProfilerContextManager():
        asyncio.run(slow_cyclic_collection_coroutine())

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)
    stacks = set()
    for sample in pprof_utils.get_samples_with_label_key(profile, "task name"):
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "task name")
        if label is not None and profile.string_table[label.str] == "Task-1":
            stacks.add(
                tuple(
                    pprof_utils.get_location_from_id(profile, location_id).function_name
                    for location_id in sample.location_id
                )
            )

    assert () not in stacks, stacks
    assert all(len(names) <= 2 for names in stacks), stacks
    gc_stacks = {names for names in stacks if "Garbage collection" in names}
    assert gc_stacks == {("Garbage collection", "slow_cyclic_collection_coroutine")}, stacks
