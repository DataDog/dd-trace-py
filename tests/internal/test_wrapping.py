from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


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
    unwrap(f)
    assert f(1, 2, 3) == (1, 2, 3)
    assert not channel


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
