"""Context managers built from wrapped (async) generators.

This is the real-world shape behind PR #18718 and PR #18741: a
``@contextlib.asynccontextmanager`` over a traced async generator. The mechanism
wraps the underlying generator function; ``contextlib`` then decorates it.
"""

import contextlib

import pytest

from tests.wrapping._harness import AGEN_ITER_XFAIL
from tests.wrapping._harness import AGEN_SEND_XFAIL
from tests.wrapping._harness import mechanisms
from tests.wrapping._harness import mechanisms_param
from tests.wrapping._harness import run


@mechanisms
def test_sync_contextmanager_enter_exit(mech):
    log = []

    def cm_gen(value):
        try:
            yield value
        finally:
            log.append("cleanup")

    cm = contextlib.contextmanager(mech.wrap_function(cm_gen))
    with cm("resource") as v:
        assert v == "resource"
    assert log == ["cleanup"]


@mechanisms
def test_sync_contextmanager_exit_with_exception(mech):
    log = []

    def cm_gen():
        try:
            yield "resource"
        finally:
            log.append("cleanup")

    cm = contextlib.contextmanager(mech.wrap_function(cm_gen))
    with pytest.raises(RuntimeError, match="boom"):
        with cm():
            raise RuntimeError("boom")
    assert log == ["cleanup"]


@mechanisms_param(xfail=AGEN_ITER_XFAIL)
def test_async_contextmanager_enter_exit(mech):
    import asyncio

    log = []

    async def acm_gen(value):
        try:
            await asyncio.sleep(0)
            yield value
        finally:
            log.append("cleanup")

    cm = contextlib.asynccontextmanager(mech.wrap_function(acm_gen))

    async def driver():
        async with cm("resource") as v:
            assert v == "resource"
        # snapshot inside the loop: finally must run during __aexit__, not at teardown
        return list(log)

    assert run(driver()) == ["cleanup"]


@mechanisms_param(xfail=AGEN_SEND_XFAIL)
def test_async_contextmanager_exit_with_exception(mech):
    # PR #18718: the body raises, so __aexit__ throws into the generator at its
    # yield; the finally must still run and the exception must propagate.
    import asyncio

    log = []

    async def acm_gen():
        try:
            await asyncio.sleep(0)
            yield "resource"
        finally:
            log.append("cleanup")

    cm = contextlib.asynccontextmanager(mech.wrap_function(acm_gen))

    async def driver():
        with pytest.raises(RuntimeError, match="boom"):
            async with cm():
                raise RuntimeError("boom")
        # snapshot inside the loop: finally must run during __aexit__, not at teardown
        return list(log)

    assert run(driver()) == ["cleanup"]
