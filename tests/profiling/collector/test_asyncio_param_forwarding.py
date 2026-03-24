import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_param_forwarding",
    ),
    err=None,
)
def test_asyncio_param_forwarding() -> None:
    """Verify that asyncio wrappers correctly forward parameters regardless of
    whether they are passed as positional args or keyword args.

    The wrappers for as_completed and shield replace the first argument with
    pre-ensured futures.  They must update either args or kwargs depending on
    how the caller passed the parameter, otherwise the original function
    receives the argument twice (TypeError: got multiple values for argument).
    """
    import asyncio
    import sys

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    assert stack.is_available, stack.failure_msg

    async def noop() -> int:
        await asyncio.sleep(0)
        return 42

    # -- as_completed --------------------------------------------------------

    async def test_as_completed_positional() -> None:
        coros = [noop() for _ in range(3)]
        results: list[int] = []
        for fut in asyncio.as_completed(coros):
            results.append(await fut)
        assert sorted(results) == [42, 42, 42]

    async def test_as_completed_keyword() -> None:
        coros = [noop() for _ in range(3)]
        results: list[int] = []
        for fut in asyncio.as_completed(fs=coros):
            results.append(await fut)
        assert sorted(results) == [42, 42, 42]

    async def test_as_completed_positional_with_timeout() -> None:
        coros = [noop() for _ in range(3)]
        results: list[int] = []
        for fut in asyncio.as_completed(coros, timeout=10):
            results.append(await fut)
        assert sorted(results) == [42, 42, 42]

    async def test_as_completed_keyword_with_timeout() -> None:
        coros = [noop() for _ in range(3)]
        results: list[int] = []
        for fut in asyncio.as_completed(fs=coros, timeout=10):
            results.append(await fut)
        assert sorted(results) == [42, 42, 42]

    # -- as_completed with loop (Python < 3.10) ------------------------------

    async def test_as_completed_positional_with_loop() -> None:
        loop = asyncio.get_running_loop()
        coros = [noop() for _ in range(3)]
        results: list[int] = []
        for fut in asyncio.as_completed(coros, loop=loop):  # type: ignore[call-arg]
            results.append(await fut)
        assert sorted(results) == [42, 42, 42]

    async def test_as_completed_keyword_with_loop() -> None:
        loop = asyncio.get_running_loop()
        coros = [noop() for _ in range(3)]
        results: list[int] = []
        for fut in asyncio.as_completed(fs=coros, loop=loop):  # type: ignore[call-arg]
            results.append(await fut)
        assert sorted(results) == [42, 42, 42]

    # -- shield --------------------------------------------------------------

    async def test_shield_positional() -> None:
        task = asyncio.create_task(noop())
        result: int = await asyncio.shield(task)
        assert result == 42

    async def test_shield_keyword() -> None:
        task = asyncio.create_task(noop())
        result: int = await asyncio.shield(arg=task)
        assert result == 42

    # -- shield with loop (Python < 3.10) ------------------------------------

    async def test_shield_positional_with_loop() -> None:
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(noop())
        result: int = await asyncio.shield(task, loop=loop)  # type: ignore[call-arg]
        assert result == 42

    async def test_shield_keyword_with_loop() -> None:
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(noop())
        result: int = await asyncio.shield(arg=task, loop=loop)  # type: ignore[call-arg]
        assert result == 42

    # -- wait ----------------------------------------------------------------

    async def test_wait_positional() -> None:
        tasks = {asyncio.create_task(noop()) for _ in range(3)}
        done, pending = await asyncio.wait(tasks)
        assert len(done) == 3
        assert len(pending) == 0
        assert all(t.result() == 42 for t in done)

    async def test_wait_keyword() -> None:
        tasks = {asyncio.create_task(noop()) for _ in range(3)}
        done, pending = await asyncio.wait(fs=tasks)
        assert len(done) == 3
        assert len(pending) == 0
        assert all(t.result() == 42 for t in done)

    # -- create_task ---------------------------------------------------------

    async def test_create_task_positional() -> None:
        task = asyncio.create_task(noop())
        assert await task == 42

    async def test_create_task_keyword() -> None:
        task = asyncio.create_task(noop(), name="test-task")
        assert await task == 42
        assert task.get_name() == "test-task"

    # -- Run all tests -------------------------------------------------------

    p = profiler.Profiler()
    p.start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    tests = [
        test_as_completed_positional,
        test_as_completed_keyword,
        test_as_completed_positional_with_timeout,
        test_as_completed_keyword_with_timeout,
        test_shield_positional,
        test_shield_keyword,
        test_wait_positional,
        test_wait_keyword,
        test_create_task_positional,
        test_create_task_keyword,
    ]

    # loop parameter was removed from as_completed and shield in Python 3.10
    if sys.hexversion < 0x030A0000:
        tests += [
            test_as_completed_positional_with_loop,
            test_as_completed_keyword_with_loop,
            test_shield_positional_with_loop,
            test_shield_keyword_with_loop,
        ]

    for test in tests:
        try:
            loop.run_until_complete(test())
        except Exception as e:
            raise AssertionError(f"{test.__name__} failed: {e}") from e

    p.stop()
