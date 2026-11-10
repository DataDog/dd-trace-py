import pytest


@pytest.mark.subprocess(err=None)
def test_as_completed_and_shield_outside_running_loop() -> None:
    import asyncio
    import sys

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.wrapping import is_wrapped
    from ddtrace.profiling import _asyncio

    assert stack.is_available, stack.failure_msg
    # Without the wrappers this test would pass without exercising the fix
    assert is_wrapped(asyncio.as_completed)
    assert is_wrapped(asyncio.shield)
    assert _asyncio.ASYNCIO_IMPORTED

    async def work(value: int) -> int:
        return value

    loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # Python 3.14+ stdlib as_completed calls current_task itself.
        if sys.hexversion < 0x030E0000:
            results: list[int] = []
            for fut in asyncio.as_completed([work(i) for i in range(3)]):
                results.append(loop.run_until_complete(fut))
            assert sorted(results) == [0, 1, 2]

        done: asyncio.Future[int] = loop.create_future()
        done.set_result(7)
        assert loop.run_until_complete(asyncio.shield(done)) == 7
        assert loop.run_until_complete(asyncio.shield(work(8))) == 8
    finally:
        loop.close()
        asyncio.set_event_loop(None)
