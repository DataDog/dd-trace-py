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

    async def wrapper(f, args, kwargs):
        return await f(*args, **kwargs)

    async def agwrapper(f, args, kwargs):
        async for _ in f(*args, **kwargs):
            yield _

    wrap(stream, agwrapper)
    wrap(body, wrapper)

    assert await body() == b"hello"


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
    wrapper_run = False

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

    def wrapper(f, args, kwargs):
        nonlocal wrapper_run

        wrapper_run = True
        return f(*args, **kwargs)

    async def stream():
        yield b"hello"
        yield b""
        return

    wrap(stream, wrapper)
    wrap(AsyncIteratorByteStream.__aiter__, wrapper)

    s = AsyncIteratorByteStream(stream())

    assert b"".join([_ async for _ in s]) == b"hello"
    assert wrapper_run
    with pytest.raises(StreamConsumed):
        b"".join([_ async for _ in s])
