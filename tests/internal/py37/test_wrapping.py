from contextlib import asynccontextmanager

import pytest

from ddtrace.internal.wrapping import wrap


@pytest.mark.asyncio
async def test_wrap_async_context_manager_exception_on_exit():
    def wrapper(f, args, kwargs):
        return f(*args, **kwargs)

    @asynccontextmanager
    async def g():
        yield 0

    wrap(g.__wrapped__, wrapper)

    acm = g()
    assert 0 == await acm.__aenter__()
    await acm.__aexit__(ValueError, None, None)
