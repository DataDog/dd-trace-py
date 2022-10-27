import sys

import pytest

from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


pytestmark = pytest.mark.skipif(sys.version_info >= (3, 11, 0), reason="FIXME[debugger-311]")


def test_wrap_unwrap():
    channel = []

    def wrapper(f, args, kwargs):
        channel[:] = []
        channel.append((args, kwargs))
        retval = f(*args, **kwargs)
        channel.append(retval)
        return channel

    def f(a, b, c=None):
        return (a, b, c)

    wrap(f, wrapper)

    assert f(1, 2, 3) == [((1, 2, 3), {}), (1, 2, 3)]
    assert f(1, b=2, c=3) == [((1, 2, 3), {}), (1, 2, 3)]

    channel[:] = []
    unwrap(f, wrapper)
    assert f(1, 2, 3) == (1, 2, 3)
    assert not channel


def test_mutiple_wrap():
    channel1, channel2 = [], []

    def wrapper_maker(channel):
        def wrapper(f, args, kwargs):
            channel[:] = []
            channel.append((args, kwargs))
            retval = f(*args, **kwargs)
            channel.append(retval)
            return retval

        return wrapper

    wrapper1, wrapper2 = (wrapper_maker(_) for _ in (channel1, channel2))

    def f(a, b, c=None):
        return (a, b, c)

    wrap(f, wrapper1)
    wrap(f, wrapper2)

    f(1, 2, 3)

    assert channel1 == channel2 == [((1, 2, 3), {}), (1, 2, 3)]

    channel1[:] = []
    channel2[:] = []

    unwrap(f, wrapper1)
    f(1, 2, 3)

    assert not channel1 and channel2 == [((1, 2, 3), {}), (1, 2, 3)]

    channel2[:] = []

    unwrap(f, wrapper2)
    f(1, 2, 3)

    assert not channel1 and not channel2


def test_wrap_generator():
    channel = []

    def wrapper(f, args, kwargs):
        channel[:] = []
        for _ in f(*args, **kwargs):
            channel.append(_)
            yield _

    def g():
        for _ in range(10):
            yield _
        return

    wrap(g, wrapper)

    assert list(g()) == list(range(10)) == channel


def test_wrap_generator_send():
    def wrapper(f, args, kwargs):
        return f(*args, **kwargs)

    def g():
        yield 0
        for _ in range(1, 10):
            n = yield _
            assert _ == n
        return

    wrap(g, wrapper)

    gen = g()
    n = next(gen)
    channel = [n]
    try:
        while True:
            n = gen.send(n)
            channel.append(n)
    except StopIteration:
        pass

    assert list(range(10)) == channel
