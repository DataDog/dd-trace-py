from contextlib import asynccontextmanager
import inspect
import sys
from types import CoroutineType

import pytest

from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


def assert_stack(expected):
    stack = []
    frame = sys._getframe(1)
    for _ in range(len(expected)):
        stack.append(frame)
        frame = frame.f_back

    assert [f.f_code.co_name for f in stack] == expected


async def async_func():
    return 42


def test_wrap_unwrap():
    channel = []

    def wrapper(f, args, kwargs):
        channel.append((args, kwargs))
        retval = f(*args, **kwargs)
        channel.append(retval)
        return retval

    def f(a, b, c=None):
        return (a, b, c)

    wrap(f, wrapper)

    assert f(1, 2, 3) == (1, 2, 3)
    assert channel == [((1, 2, 3), {}), (1, 2, 3)]

    assert f(1, b=2, c=3) == (1, 2, 3)
    assert channel == [((1, 2, 3), {}), (1, 2, 3), ((1, 2, 3), {}), (1, 2, 3)]

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
        assert_stack(["g", "wrapper", "g"])

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


def test_wrap_generator_throw_close():
    def wrapper_maker(channel):
        def wrapper(f, args, kwargs):
            channel.append(True)

            __ddgen = f(*args, **kwargs)
            __ddgensend = __ddgen.send
            try:
                value = next(__ddgen)
                channel.append(value)
            except StopIteration:
                return
            while True:
                try:
                    tosend = yield value
                except GeneratorExit:
                    channel.append("GeneratorExit")
                    __ddgen.close()
                    raise GeneratorExit()
                except:  # noqa
                    channel.append(sys.exc_info()[0])
                    value = __ddgen.throw(*sys.exc_info())
                    channel.append(value)
                else:
                    try:
                        value = __ddgensend(tosend)
                        channel.append(value)
                    except StopIteration:
                        return

        return wrapper

    channel = []

    def g():
        while True:
            try:
                yield 0
            except ValueError:
                yield 1

    wrap(g, wrapper_maker(channel))
    inspect.isgeneratorfunction(g)

    gen = g()
    inspect.isgenerator(gen)

    for _ in range(10):
        assert next(gen) == 0
        assert gen.throw(ValueError) == 1

    gen.close()

    assert channel == [True] + [0, ValueError, 1] * 10 + ["GeneratorExit"]


def test_wrap_stack():
    def wrapper(f, args, kwargs):
        return f(*args, **kwargs)

    def f():
        stack = []
        frame = sys._getframe()
        while frame:
            stack.append(frame)
            frame = frame.f_back
        return stack

    wrap(f, wrapper)

    assert [frame.f_code.co_name for frame in f()[:4]] == ["f", "wrapper", "f", "test_wrap_stack"]


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
        return await async_func()

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


@pytest.mark.asyncio
async def test_async_generator():
    async def stream():
        assert_stack(["stream", "agwrapper", "stream", "body", "wrapper", "body"])
        yield b"hello"
        yield b""
        return

    async def body():
        chunks = []
        async for chunk in stream():
            chunks.append(chunk)
        _body = b"".join(chunks)
        return _body

    wrapper_called = awrapper_called = False

    async def wrapper(f, args, kwargs):
        nonlocal wrapper_called
        wrapper_called = True
        return await f(*args, **kwargs)

    async def agwrapper(f, args, kwargs):
        nonlocal awrapper_called
        awrapper_called = True
        async for _ in f(*args, **kwargs):
            yield _

    wrap(stream, agwrapper)
    wrap(body, wrapper)

    assert await body() == b"hello"
    assert wrapper_called
    assert awrapper_called


@pytest.mark.asyncio
async def test_wrap_async_generator_send():
    def wrapper(f, args, kwargs):
        return f(*args, **kwargs)

    async def g():
        yield 0
        for _ in range(1, 10):
            n = yield _
            assert _ == n
        return

    wrap(g, wrapper)

    channel = []

    async def consume():
        agen = g()
        n = await agen.__anext__()
        channel.append(n)
        try:
            while True:
                n = await agen.asend(n)
                channel.append(n)
        except StopAsyncIteration:
            pass

        assert list(range(10)) == channel

    await consume()


@pytest.mark.asyncio
async def test_double_async_for_with_exception():
    channel = None

    class StreamConsumed(Exception):
        pass

    class AsyncIteratorByteStream(object):
        def __init__(self, stream):
            self._stream = stream
            self._is_stream_consumed = False

        async def __aiter__(self):
            if self._is_stream_consumed:
                raise StreamConsumed()

            self._is_stream_consumed = True
            async for part in self._stream:
                yield part

    async def wrapper(f, args, kwargs):
        nonlocal channel

        channel = [_ async for _ in f(*args, **kwargs)]
        for _ in channel:
            yield _
        return

    async def stream():
        yield b"hello"
        yield b""
        return

    wrap(stream, wrapper)
    wrap(AsyncIteratorByteStream.__aiter__, wrapper)

    s = AsyncIteratorByteStream(stream())

    assert b"".join([_ async for _ in s]) == b"hello"
    assert channel == [b"hello", b""]
    with pytest.raises(StreamConsumed):
        b"".join([_ async for _ in s])


@pytest.mark.asyncio
async def test_wrap_async_generator_throw_close():
    channel = []

    async def wrapper(f, args, kwargs):
        nonlocal channel

        channel.append(True)

        __ddgen = f(*args, **kwargs)
        __ddgensend = __ddgen.asend
        try:
            value = await __ddgen.__anext__()
            channel.append(value)
        except StopAsyncIteration:
            return
        while True:
            try:
                tosend = yield value
            except GeneratorExit:
                channel.append("GeneratorExit")
                await __ddgen.aclose()
                raise
            except:  # noqa
                channel.append(sys.exc_info()[0])
                value = await __ddgen.athrow(*sys.exc_info())
                channel.append(value)
            else:
                try:
                    value = await __ddgensend(tosend)
                    channel.append(value)
                except StopAsyncIteration:
                    return

    async def g():
        while True:
            try:
                yield 0
            except ValueError:
                yield 1

    wrap(g, wrapper)
    assert inspect.isasyncgenfunction(g)

    gen = g()
    assert inspect.isasyncgen(gen)

    for _ in range(10):
        assert await gen.__anext__() == 0
        assert await gen.athrow(ValueError) == 1

    await gen.aclose()

    assert channel == [True] + [0, ValueError, 1] * 10 + ["GeneratorExit"]


def test_wrap_closure():
    channel = []

    def wrapper(f, args, kwargs):
        channel.append((args, kwargs))
        retval = f(*args, **kwargs)
        channel.append(retval)
        return retval

    def outer(answer=42):
        def f(a, b, c=None):
            return (a, b, c, answer)

        return f

    wrap(outer, wrapper)

    closure = outer()
    wrap(closure, wrapper)

    assert closure(1, 2, 3) == (1, 2, 3, 42)
    assert channel == [((42,), {}), closure, ((1, 2, 3), {}), (1, 2, 3, 42)]
