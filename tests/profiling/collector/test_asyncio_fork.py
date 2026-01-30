import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_fork",
    ),
    err=None,
)
def test_asyncio_fork() -> None:
    """Test that asyncio event loop behaves as we assume on fork.

    Specifically, we expect that trying to use the existing event loop after fork in the child
    raises a RuntimeError (as the "surviving" event loop should not be running anymore).

    This assumption allows us to make simplifying assumptions in the Profiler (specifically, that
    we do not need to track the surviving event loop in the child process as it cannot be reused anyway).
    """
    import asyncio
    import os

    async def async_main() -> None:
        await asyncio.sleep(0.2)
        pid = os.fork()

        if pid == 0:
            try:
                await asyncio.sleep(1)
            except RuntimeError as e:
                if str(e) == "no running event loop":
                    return
                raise
        else:
            assert asyncio.get_event_loop() is not None

    asyncio.set_event_loop(asyncio.new_event_loop())
    asyncio.run(async_main())
