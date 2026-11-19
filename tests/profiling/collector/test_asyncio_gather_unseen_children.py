"""Verify that a gather link is removed when no Task snapshot ever saw its child.

A leftover link keeps its parent in the set of parent Tasks, so the parent is never sampled as a leaf and
disappears from the profile.
"""

import sys

import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_gather_future_child",
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    ),
    err=None,
)
def test_asyncio_gather_future_child() -> None:
    """A Future is never in a Task snapshot, so its gather link must be removed without being seen."""
    import asyncio
    import os

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import async_run

    assert stack.is_available, stack.failure_msg

    async def idle_after_gather() -> None:
        await asyncio.sleep(1)

    async def parent() -> None:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        loop.call_soon(future.set_result, None)
        await asyncio.gather(future)
        await idle_after_gather()

    async def main() -> None:
        await asyncio.create_task(parent(), name="Parent")

    p = profiler.Profiler()
    p.start()
    async_run(main())
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "task name")

    def loc(fn: str) -> pprof_utils.StackLocation:
        return pprof_utils.StackLocation(function_name=fn, filename="", line_no=-1)

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="Parent",
            locations=[loc("sleep"), loc("idle_after_gather"), loc("parent")],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="asyncio.eager_task_factory was added in Python 3.12")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_gather_eager_children",
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    ),
    err=None,
)
def test_asyncio_gather_eager_children() -> None:
    """Eager Tasks that finish when they are created are never in a Task snapshot."""
    import asyncio
    import os

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import async_run

    assert stack.is_available, stack.failure_msg

    async def child() -> None:
        pass

    async def idle_after_gather() -> None:
        await asyncio.sleep(1)

    async def parent() -> None:
        loop = asyncio.get_running_loop()
        loop.set_task_factory(asyncio.eager_task_factory)
        await asyncio.gather(*[asyncio.ensure_future(child()) for _ in range(10)])
        loop.set_task_factory(None)
        await idle_after_gather()

    async def main() -> None:
        await asyncio.create_task(parent(), name="Parent")

    p = profiler.Profiler()
    p.start()
    async_run(main())
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "task name")

    def loc(fn: str) -> pprof_utils.StackLocation:
        return pprof_utils.StackLocation(function_name=fn, filename="", line_no=-1)

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="Parent",
            locations=[loc("sleep"), loc("idle_after_gather"), loc("parent")],
        ),
        print_samples_on_failure=True,
    )
