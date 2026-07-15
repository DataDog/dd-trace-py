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

    async def async_main() -> None:
        deep_sync(20)

    p = profiler.Profiler()
    p.start()
    async_run(async_main())
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    actual = []
    for sample in pprof_utils.get_samples_with_label_key(profile, "task name"):
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "task name")
        if label is not None and profile.string_table[label.str] == "Task-1":
            actual.append(
                [
                    pprof_utils.get_location_from_id(profile, location_id).function_name
                    for location_id in sample.location_id
                ]
            )

    assert ["async_main"] in actual, actual


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
        len(names) <= 8 and "sync_code" in names and names[-1].startswith("<") and "synchronous frame" in names[-1]
        for names in actual
    ), actual
