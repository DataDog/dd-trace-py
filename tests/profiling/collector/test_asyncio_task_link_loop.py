import sys

import pytest


def assert_latest_child_has_parent(profile_prefix: str, thread_name: str) -> None:
    import os

    from tests.profiling.collector import pprof_utils

    profile = pprof_utils.parse_newest_profile(f"{profile_prefix}.{os.getpid()}")
    child_samples = [
        sample
        for sample in pprof_utils.get_samples_with_label_key(profile, "task name")
        if pprof_utils.get_str_label(profile, sample, "thread name") == thread_name
        and pprof_utils.get_str_label(profile, sample, "task name") == "linked-child"
    ]
    assert child_samples, f"No child samples from {thread_name}"
    latest = max(
        child_samples,
        key=lambda sample: pprof_utils.get_label_with_key(profile.string_table, sample, "end_timestamp_ns").num,
    )
    pprof_utils.assert_profile_has_sample(
        profile,
        [latest],
        expected_sample=pprof_utils.StackEvent(
            thread_name=thread_name,
            task_name="linked-child",
            locations=[
                pprof_utils.StackLocation(function_name="sleep", filename="", line_no=-1),
                pprof_utils.StackLocation(function_name="child", filename="", line_no=-1),
                pprof_utils.StackLocation(function_name="parent", filename="", line_no=-1),
            ],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info >= (3, 14), reason="Native tasks are discovered on the worker thread in 3.14+")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_task_link_from_untracked_runner",
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    ),
    err=None,
)
def test_asyncio_task_link_from_untracked_runner() -> None:
    import asyncio
    import os
    import threading

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector.test_asyncio_task_link_loop import assert_latest_child_has_parent

    assert stack.is_available, stack.failure_msg

    async def child() -> None:
        await asyncio.sleep(1)

    async def parent() -> None:
        task = asyncio.create_task(child(), name="linked-child")
        await asyncio.gather(task)

    p = profiler.Profiler()
    p.start()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # Only MainThread tracks the loop, but the worker creates the links.
    worker = threading.Thread(target=loop.run_forever, daemon=True)
    worker.start()
    try:
        asyncio.run_coroutine_threadsafe(parent(), loop).result(timeout=10)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        worker.join(timeout=10)
        p.stop()
        assert not worker.is_alive()
        asyncio.set_event_loop(None)
        loop.close()

    assert_latest_child_has_parent(os.environ["DD_PROFILING_OUTPUT_PPROF"], "MainThread")


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_task_link_while_untracked",
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    ),
    parametrize={"LINK_KIND": ["strong", "weak"]},
    err=None,
)
def test_asyncio_task_link_while_untracked() -> None:
    import asyncio
    import os

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector.test_asyncio_task_link_loop import assert_latest_child_has_parent

    assert stack.is_available, stack.failure_msg

    async def child() -> None:
        await asyncio.sleep(1)

    async def parent() -> None:
        loop = asyncio.get_running_loop()
        # Changing the default does not change the running loop or the child's loop.
        asyncio.set_event_loop(None)
        try:
            task = asyncio.create_task(child(), name="linked-child")
            group = asyncio.gather(task) if os.environ["LINK_KIND"] == "strong" else None
        finally:
            asyncio.set_event_loop(loop)
        if group is not None:
            await group
        else:
            # Leave the child running without awaiting it, so only the weak link can attach the parent.
            await asyncio.sleep(1.5)
            await task

    p = profiler.Profiler()
    p.start()
    try:
        asyncio.run(parent())
    finally:
        p.stop()

    assert_latest_child_has_parent(os.environ["DD_PROFILING_OUTPUT_PPROF"], "MainThread")


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_task_link_shared_loop",
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    ),
    err=None,
)
def test_asyncio_task_link_survives_shared_loop_untracking() -> None:
    import asyncio
    import os
    import threading

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector.test_asyncio_task_link_loop import assert_latest_child_has_parent

    assert stack.is_available, stack.failure_msg
    ready = threading.Event()

    async def child() -> None:
        ready.set()
        await main_untracked.wait()
        await asyncio.sleep(1)

    async def parent() -> None:
        task = asyncio.create_task(child(), name="linked-child")
        await asyncio.gather(task)

    def run_loop() -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    p = profiler.Profiler()
    p.start()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main_untracked = asyncio.Event()
    worker = threading.Thread(target=run_loop, name="loop-worker", daemon=True)
    worker.start()
    try:
        result = asyncio.run_coroutine_threadsafe(parent(), loop)
        assert ready.wait(timeout=10)
        asyncio.set_event_loop(None)
        # The worker keeps running the same loop and must retain the links.
        loop.call_soon_threadsafe(main_untracked.set)
        result.result(timeout=10)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        worker.join(timeout=10)
        p.stop()
        assert not worker.is_alive()
        loop.close()

    assert_latest_child_has_parent(os.environ["DD_PROFILING_OUTPUT_PPROF"], worker.name)


@pytest.mark.subprocess(env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_asyncio_task_link_future_subclass"}, err=None)
def test_asyncio_task_link_uses_get_loop_for_future_subclass() -> None:
    import asyncio

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    assert stack.is_available, stack.failure_msg
    calls = []

    class CustomFuture(asyncio.Future):
        def get_loop(self):
            calls.append(self)
            return super().get_loop()

    async def main() -> None:
        parent = asyncio.current_task()
        child = CustomFuture()
        stack.link_tasks(parent, child)
        stack.weak_link_tasks(parent, child)
        assert calls == [child, child]

    p = profiler.Profiler()
    p.start()
    try:
        asyncio.run(main())
    finally:
        p.stop()
