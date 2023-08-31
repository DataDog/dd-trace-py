import inspect
import sys

import pytest

from ddtrace.internal.wrapping import wrap


@pytest.mark.asyncio
async def test_async_generator():
    async def stream():
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
