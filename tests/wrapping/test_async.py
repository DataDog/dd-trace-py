"""Coroutines and async generators: await delegation and async lifecycle.

Async generators that ``await`` around their ``yield`` exercise the SEND-based
await machinery that PR #18741 fixed for the internal bytecode trampoline; those
cases now pass for every mechanism. The remaining known failures here are
``wrapping_context``'s asend/athrow/aclose on 3.10 only (see the ``xfail_mechanism``
markers).
"""

import asyncio
import sys

import pytest

from tests.wrapping._harness import aiterate
from tests.wrapping._harness import run
from tests.wrapping.mechanisms import xfail_mechanism


# --- coroutines ------------------------------------------------------------
def test_coroutine_return(mech):
    async def c(x):
        await asyncio.sleep(0)
        return x * 2

    assert run(mech.wrap_function(c)(5)) == 10


def test_coroutine_raises(mech):
    async def c():
        await asyncio.sleep(0)
        raise KeyError("boom")

    with pytest.raises(KeyError, match="boom"):
        run(mech.wrap_function(c)())


def test_coroutine_awaits_multiple_times(mech):
    class _MultiSuspend:
        def __await__(self):
            yield
            yield
            return "done"

    async def c():
        return await _MultiSuspend()

    assert run(mech.wrap_function(c)()) == "done"


def test_coroutine_cancellation_runs_finally(mech):
    log = []

    async def c():
        try:
            await asyncio.sleep(10)
        finally:
            log.append("cleanup")

    async def driver():
        task = asyncio.ensure_future(mech.wrap_function(c)())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # snapshot inside the loop: finally must run on cancellation, not at teardown
        return list(log)

    assert run(driver()) == ["cleanup"]


# --- async generators ------------------------------------------------------
def test_async_generator_no_await(mech):
    # An async generator that never awaits is immune to PR #18741 on all versions.
    async def ag():
        yield 1
        yield 2

    assert run(aiterate(mech.wrap_function(ag)())) == [1, 2]


def test_async_generator_iterate(mech):
    async def ag():
        for i in range(3):
            await asyncio.sleep(0)
            yield i

    assert run(aiterate(mech.wrap_function(ag)())) == [0, 1, 2]


@xfail_mechanism(
    "wrapping_context",
    reason="WrappingContext async-gen asend/athrow/aclose broken on 3.10 (TypeError: NoneType not callable)",
    condition=sys.version_info[:2] == (3, 10),
)
def test_async_generator_asend(mech):
    async def ag():
        x = yield 0
        while True:
            await asyncio.sleep(0)
            x = yield x

    async def driver():
        gen = mech.wrap_function(ag)()
        out = [await gen.__anext__(), await gen.asend(10), await gen.asend(20)]
        await gen.aclose()
        return out

    assert run(driver()) == [0, 10, 20]


@xfail_mechanism(
    "wrapping_context",
    reason="WrappingContext async-gen asend/athrow/aclose broken on 3.10 (TypeError: NoneType not callable)",
    condition=sys.version_info[:2] == (3, 10),
)
def test_async_generator_athrow_recovered(mech):
    async def ag():
        while True:
            try:
                await asyncio.sleep(0)
                yield "value"
            except ValueError:
                yield "recovered"

    async def driver():
        gen = mech.wrap_function(ag)()
        first = await gen.__anext__()
        recovered = await gen.athrow(ValueError())
        await gen.aclose()
        return first, recovered

    assert run(driver()) == ("value", "recovered")


@xfail_mechanism(
    "wrapping_context",
    reason="WrappingContext async-gen asend/athrow/aclose broken on 3.10 (TypeError: NoneType not callable)",
    condition=sys.version_info[:2] == (3, 10),
)
def test_async_generator_aclose_runs_finally(mech):
    log = []

    async def ag():
        try:
            await asyncio.sleep(0)
            yield 1
            yield 2
        finally:
            log.append("cleanup")

    async def driver():
        gen = mech.wrap_function(ag)()
        await gen.__anext__()
        await gen.aclose()
        # snapshot inside the loop: finally must run during aclose, not at teardown
        return list(log)

    assert run(driver()) == ["cleanup"]


def test_async_generator_multi_suspend_before_yield(mech):
    # The exact PR #18741 shape: await more than once before the first yield.
    async def ag():
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        yield "resource"

    assert run(aiterate(mech.wrap_function(ag)())) == ["resource"]
