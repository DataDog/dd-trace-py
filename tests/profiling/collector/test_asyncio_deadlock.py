import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_deadlock",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_deadlock() -> None:
    import asyncio
    from typing import Optional

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    assert stack.is_available, stack.failure_msg

    # We'll assign these after creation so each coroutine can await the other.
    t1: Optional[asyncio.Task] = None
    t2: Optional[asyncio.Task] = None

    async def task_a() -> None:
        assert t2 is not None
        try:
            await t2
        except asyncio.CancelledError:
            pass

    async def task_b() -> None:
        assert t1 is not None
        try:
            await t1
        except asyncio.CancelledError:
            pass

    async def main() -> None:
        # Create both tasks so they can reference each other.
        t1 = asyncio.create_task(task_a(), name="task_a")
        t2 = asyncio.create_task(task_b(), name="task_b")

        # Let the circular wait sit for 3 seconds.
        await asyncio.sleep(1.5)

        try:
            t1.cancel()
        except RecursionError:
            pass

        try:
            t2.cancel()
        except RecursionError:
            pass

    p = profiler.Profiler()
    p.start()

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    except RecursionError:
        pass

    p.stop()
