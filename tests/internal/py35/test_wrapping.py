import sys
from types import CoroutineType

import pytest

from ddtrace.internal.wrapping import wrap
from tests.internal.py35.asyncstuff import async_func as asyncfoo


pytestmark = pytest.mark.skipif(sys.version_info >= (3, 11, 0), reason="FIXME[debugger-311]")


def test_wrap_generator_yield_from():
    channel = []

    def wrapper(f, args, kwargs):
        channel[:] = []
        for _ in f(*args, **kwargs):
            channel.append(_)
            yield _

    def g():
        yield from range(10)

    wrap(g, wrapper)

    assert list(g()) == list(range(10)) == channel


@pytest.mark.asyncio
async def test_wrap_coroutine():
    channel = []

    def wrapper(f, args, kwargs):
        async def _handle_coroutine(c):
            retval = await c
            channel.append(retval)
            return retval

        channel[:] = []
        retval = f(*args, **kwargs)
        if isinstance(retval, CoroutineType):
            return _handle_coroutine(retval)
        else:
            channel.append(retval)
            return retval

    async def c():
        return await asyncfoo()

    wrap(c, wrapper)

    assert await c() == 42

    assert channel == [42]


def test_wrap_args_kwarg():
    def f(*args, path=None):
        return (args, path)

    def wrapper(f, args, kwargs):
        return f(*args, **kwargs)

    wrap(f, wrapper)

    assert f(1, 2) == ((1, 2), None)


def test_wrap_arg_args_kwarg_kwargs():
    def f(posarg, *args, path=None, **kwargs):
        return (posarg, args, path, kwargs)

    def wrapper(f, args, kwargs):
        return f(*args, **kwargs)

    wrap(f, wrapper)

    assert f(1, 2) == (1, (2,), None, {})
    assert f(1, 2, 3, foo="bar") == (1, (2, 3), None, {"foo": "bar"})
    assert f(1, 2, 3, path="bar") == (1, (2, 3), "bar", {})
    assert f(1, 2, 3, 4, path="bar", foo="baz") == (1, (2, 3, 4), "bar", {"foo": "baz"})
    assert f(1, path="bar", foo="baz") == (1, (), "bar", {"foo": "baz"})
