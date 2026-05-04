import asyncio
from contextlib import asynccontextmanager
import copy
import inspect
import sys
import threading
from types import CoroutineType
from types import FunctionType
from typing import cast

import pytest

from ddtrace.internal.wrapping import is_wrapped
from ddtrace.internal.wrapping import is_wrapped_with
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap
from ddtrace.internal.wrapping.context import BaseWrappingContext
from ddtrace.internal.wrapping.context import LazyWrappingContext
from ddtrace.internal.wrapping.context import WrappingContext
from ddtrace.internal.wrapping.context import _UniversalWrappingContext


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


def test_is_wrapped():
    """Test that `is_wrapped` and `is_wrapped_with` work as expected."""

    def first_wrapper(f, args, kwargs):
        return f(*args, **kwargs)

    def second_wrapper(f, args, kwargs):
        return f(*args, **kwargs)

    def f(a, b, c=None):
        return (a, b, c)

    # Function works
    assert f(1, 2) == (1, 2, None)

    # Not wrapped yet
    assert not is_wrapped(f)
    assert not is_wrapped_with(f, first_wrapper)
    assert not is_wrapped_with(f, second_wrapper)

    # Wrap with first wrapper
    wrap(f, first_wrapper)

    # Function still works
    assert f(1, 2) == (1, 2, None)

    # Only wrapped with first_wrapper
    assert is_wrapped(f)
    assert is_wrapped_with(f, first_wrapper)
    assert not is_wrapped_with(f, second_wrapper)

    # Wrap with second wrapper
    wrap(f, second_wrapper)

    # Function still works
    assert f(1, 2) == (1, 2, None)

    # Wrapped with everything
    assert is_wrapped(f)
    assert is_wrapped_with(f, first_wrapper)
    assert is_wrapped_with(f, second_wrapper)

    # Unwrap first wrapper
    unwrap(f, first_wrapper)

    # Function still works
    assert f(1, 2) == (1, 2, None)

    # Still wrapped with second_wrapper
    assert is_wrapped(f)
    assert not is_wrapped_with(f, first_wrapper)
    assert is_wrapped_with(f, second_wrapper)

    # Unwrap second wrapper
    unwrap(f, second_wrapper)

    # Function still works
    assert f(1, 2) == (1, 2, None)

    # Not wrapped anymore
    assert not is_wrapped(f)
    assert not is_wrapped_with(f, first_wrapper)
    assert not is_wrapped_with(f, second_wrapper)


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
    assert inspect.isgeneratorfunction(g)

    gen = g()
    assert inspect.isgenerator(gen)

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


NOTSET = object()


class DummyWrappingContext(WrappingContext):
    def __init__(self, f):
        super().__init__(f)

        self.entered = False
        self.exited = False
        self.return_value = NOTSET
        self.exc_info = None
        self.frame = None

    def __enter__(self):
        self.entered = True
        self.frame = self.__frame__
        return super().__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        self.exited = True
        if exc_value is not None:
            self.exc_info = (exc_type, exc_value, traceback)
        super().__exit__(exc_type, exc_value, traceback)

    def __return__(self, value):
        self.return_value = value
        return super().__return__(value)


def test_wrapping_context_happy():
    def foo():
        return 42

    wc = DummyWrappingContext(foo)
    wc.wrap()

    assert foo() == 42

    assert wc.entered
    assert wc.return_value == 42
    assert not wc.exited
    assert wc.exc_info is None

    assert wc.frame.f_code.co_name == "foo"
    assert wc.frame.f_code.co_filename == __file__


def test_wrapping_context_unwrapping():
    def foo():
        return 42

    wc = DummyWrappingContext(foo)
    wc.wrap()
    assert _UniversalWrappingContext.is_wrapped(foo)

    wc.unwrap()
    assert not _UniversalWrappingContext.is_wrapped(foo)

    assert foo() == 42

    assert not wc.entered
    assert wc.return_value is NOTSET
    assert not wc.exited
    assert wc.exc_info is None


def test_wrapping_context_deepcopy():
    """Deepcopy of a route holding a wrapping context (e.g. Cadwyn/Airflow 3) must not raise.
    This is a regression for: https://github.com/DataDog/dd-trace-py/issues/16443.
    """

    def endpoint():
        return 1

    wc = DummyLazyWrappingContext(endpoint)
    wc.wrap()

    class Route:
        """Minimal route-like container (e.g. Starlette APIRoute)."""

        def __init__(self, endpoint, ctx):
            self.endpoint = endpoint
            self.ctx = ctx

    route = Route(endpoint, wc)
    route_copy = copy.deepcopy(route)

    assert route_copy.ctx is not wc
    assert hasattr(route_copy.ctx, "_storage")
    assert hasattr(route_copy.ctx, "_trampoline_lock")
    # Use base __enter__/__exit__ so we don't trigger __frame__ (which expects
    # to run inside a wrapped call). This verifies the copied context's
    # _storage is a new, working ContextVar.
    BaseWrappingContext.__enter__(route_copy.ctx)
    try:
        route_copy.ctx.set("k", 99)
        assert route_copy.ctx.get("k") == 99
    finally:
        BaseWrappingContext.__exit__(route_copy.ctx, None, None, None)


def test_wrapping_context_exc():
    def foo():
        raise ValueError("foo")

    wc = DummyWrappingContext(foo)
    wc.wrap()

    with pytest.raises(ValueError):
        foo()

    assert wc.entered
    assert wc.return_value is NOTSET
    assert wc.exited

    _type, exc, _ = wc.exc_info
    assert _type == ValueError
    assert exc.args == ("foo",)


def test_wrapping_context_exc_on_exit():
    class BrokenExitWrappingContext(DummyWrappingContext):
        def __exit__(self, exc_type, exc_value, traceback):
            super().__exit__(exc_type, exc_value, traceback)
            raise RuntimeError("broken")

    def foo():
        raise ValueError("foo")

    wc = BrokenExitWrappingContext(foo)
    wc.wrap()

    with pytest.raises(RuntimeError):
        foo()

    assert wc.entered
    assert wc.return_value is NOTSET
    assert wc.exited

    _type, exc, _ = wc.exc_info
    assert _type == ValueError
    assert exc.args == ("foo",)


def test_wrapping_context_priority():
    class HighPriorityWrappingContext(DummyWrappingContext):
        def __enter__(self):
            nonlocal mutated

            mutated = True

            return super().__enter__()

        def __return__(self, value):
            nonlocal mutated

            assert not mutated

            return super().__return__(value)

    class LowPriorityWrappingContext(DummyWrappingContext):
        __priority__ = 99

        def __enter__(self):
            nonlocal mutated

            assert mutated

            return super().__enter__()

        def __return__(self, value):
            nonlocal mutated

            mutated = False

            return super().__return__(value)

    mutated = False

    def foo():
        return 42

    hwc = HighPriorityWrappingContext(foo)
    lwc = LowPriorityWrappingContext(foo)

    # Wrap low first. We want to make sure that hwc is entered first
    lwc.wrap()
    hwc.wrap()

    foo()

    assert lwc.entered
    assert hwc.return_value == 42


def test_wrapping_context_recursive():
    values = []

    class RecursiveWrappingContext(DummyWrappingContext):
        def __enter__(self):
            nonlocal values
            super().__enter__()

            n = self.__frame__.f_locals["n"]
            self.set("n", n)
            values.append(n)

            return self

        def __return__(self, value):
            nonlocal values
            n = self.__frame__.f_locals["n"]
            assert self.get("n") == n
            values.append(n)

            return super().__return__(value)

    def factorial(n):
        if n == 0:
            return 1
        return n * factorial(n - 1)

    wc = RecursiveWrappingContext(factorial)
    wc.wrap()

    assert factorial(5) == 120
    assert values == [5, 4, 3, 2, 1, 0, 0, 1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_wrapping_context_async_storage_isolation():
    """Storage set in one coroutine must not bleed into a concurrent sibling."""
    barrier = asyncio.Event()

    class StorageIsolationContext(WrappingContext):
        pass

    async def coro(value, *, first: bool):
        ctx = StorageIsolationContext.extract(cast(FunctionType, coro))
        ctx.set("value", value)
        if first:
            # Yield control so the second task runs and sets its own value.
            await barrier.wait()
        return ctx.get("value")

    wc = StorageIsolationContext(coro)
    wc.wrap()

    task1 = asyncio.create_task(coro(1, first=True))
    # Let task1 reach the barrier before starting task2.
    await asyncio.sleep(0)
    task2 = asyncio.create_task(coro(2, first=False))
    await task2
    barrier.set()
    result1 = await task1

    # task1 set "value" to 1; even though task2 ran in between and set its own
    # "value" to 2, task1 should still read back 1.
    assert result1 == 1


def test_wrapping_context_generator():
    def foo():
        yield from range(10)
        return 42

    wc = DummyWrappingContext(foo)
    wc.wrap()

    assert list(foo()) == list(range(10))

    assert wc.entered
    assert wc.return_value == 42
    assert not wc.exited
    assert wc.exc_info is None


@pytest.mark.asyncio
async def test_wrapping_context_async_generator():
    async def arange(count):
        for i in range(count):
            yield (i)
            await asyncio.sleep(0.0)

    wc = DummyWrappingContext(arange)
    wc.wrap()

    a = []
    async for _ in arange(10):
        a.append(_)

    assert a == list(range(10))

    assert wc.entered
    assert wc.return_value is None
    assert not wc.exited
    assert wc.exc_info is None


@pytest.mark.asyncio
async def test_wrapping_context_async_happy() -> None:
    async def coro():
        return 1

    wc = DummyWrappingContext(coro)
    wc.wrap()

    assert await coro() == 1

    assert wc.entered
    assert wc.return_value == 1
    assert not wc.exited
    assert wc.exc_info is None


@pytest.mark.asyncio
async def test_wrapping_context_async_exc() -> None:
    async def coro():
        raise ValueError("foo")

    wc = DummyWrappingContext(coro)
    wc.wrap()

    with pytest.raises(ValueError):
        await coro()

    assert wc.entered
    assert wc.return_value is NOTSET
    assert wc.exited

    _type, exc, _ = wc.exc_info
    assert _type is ValueError
    assert exc.args == ("foo",)


@pytest.mark.asyncio
async def test_wrapping_context_async_concurrent() -> None:
    values = []

    class ConcurrentWrappingContext(DummyWrappingContext):
        def __enter__(self):
            super().__enter__()

            self.set("n", self.__frame__.f_locals["n"])

            return self

        def __return__(self, value):
            nonlocal values

            values.append((self.get("n"), self.__frame__.f_locals["n"]))

            return super().__return__(value)

    async def fibonacci(n):
        if n <= 1:
            return 1
        return sum(await asyncio.gather(fibonacci(n - 1), fibonacci(n - 2)))

    wc = ConcurrentWrappingContext(fibonacci)
    wc.wrap()

    N = 20

    await asyncio.gather(*[fibonacci(n) for n in range(1, N)])

    assert set(values) == {(n, n) for n in range(0, N)}


# DEV: Since this test relies on `gc`, run this test as a subprocess and avoid over-importing
# too many modules to avoid outside impact on `gc` causing flakiness
@pytest.mark.subprocess()
def test_wrapping_context_method_leaks():
    import gc

    from ddtrace.internal.wrapping.context import WrappingContext
    from ddtrace.internal.wrapping.context import _UniversalWrappingContext

    NOTSET = object()

    # DEV: Redefine this module level class to avoid importing `tests.internal.test_wrapping`
    # to help reduce flakiness caused by outside impact on `gc`
    class DummyWrappingContext(WrappingContext):
        def __init__(self, f):
            super().__init__(f)

            self.entered = False
            self.exited = False
            self.return_value = NOTSET
            self.exc_info = None
            self.frame = None

        def __enter__(self):
            self.entered = True
            self.frame = self.__frame__
            return super().__enter__()

        def __exit__(self, exc_type, exc_value, traceback):
            self.exited = True
            if exc_value is not None:
                self.exc_info = (exc_type, exc_value, traceback)
            super().__exit__(exc_type, exc_value, traceback)

        def __return__(self, value):
            self.return_value = value
            return super().__return__(value)

    def foo():
        return 42

    wc = DummyWrappingContext(foo)
    wc.wrap()

    # Disable auto-GC to prevent non-deterministic collection between measurements,
    # and narrow to bound methods of _UniversalWrappingContext (the __enter__,
    # __return__, and _exit bound methods injected into the wrapped bytecode) to
    # avoid counting unrelated method objects from background threads or imports.
    def is_wrapping_method(obj):
        return type(obj).__name__ == "method" and isinstance(getattr(obj, "__self__", None), _UniversalWrappingContext)

    gc.disable()
    gc.collect()
    # Store only IDs before the run — no object references held, so nothing is kept alive artificially.
    before_ids = {id(obj) for obj in gc.get_objects() if is_wrapping_method(obj)}

    for _ in range(10000):
        foo()

    gc.collect()

    # After measurement is complete it is safe to hold references for inspection.
    after_objects = [obj for obj in gc.get_objects() if is_wrapping_method(obj)]
    gc.enable()

    new_objects = [obj for obj in after_objects if id(obj) not in before_ids]
    assert len(after_objects) <= len(before_ids) + 1, (
        f"Expected at most {len(before_ids) + 1} wrapping methods, got {len(after_objects)}.\n"
        + "\n".join(
            f"  NEW {obj!r} __func__={getattr(obj, '__func__', None)!r} referrers={gc.get_referrers(obj)!r}"
            for obj in new_objects
        )
    )


class DummyLazyWrappingContext(LazyWrappingContext):
    def __init__(self, f):
        super().__init__(f)

        self.count = 0

    def __enter__(self):
        self.count += 1
        return super().__enter__()


def test_wrapping_context_lazy():
    free = 42

    def foo():
        return free

    (wc := DummyLazyWrappingContext(foo)).wrap()

    assert DummyLazyWrappingContext.is_wrapped(foo)
    assert not _UniversalWrappingContext.is_wrapped(foo)

    for _ in range(n := 10):
        assert foo() == free

        assert not DummyLazyWrappingContext.is_wrapped(foo)
        assert _UniversalWrappingContext.is_wrapped(foo)

    assert wc.count == n

    wc.count = 0

    wc.unwrap()

    for _ in range(10):
        assert not DummyLazyWrappingContext.is_wrapped(foo)
        assert not _UniversalWrappingContext.is_wrapped(foo)

        assert foo() == free

    assert wc.count == 0


def test_wrapping_context_lazy_multiple_wrappers():
    free = 42

    def foo():
        return free

    class Context1(LazyWrappingContext):
        def __init__(self, f):
            super().__init__(f)

            self.count = 0

        def __enter__(self):
            self.count += 1
            return super().__enter__()

    class Context2(LazyWrappingContext):
        def __init__(self, f):
            super().__init__(f)

            self.count = 0

        def __enter__(self):
            self.count += 1
            return super().__enter__()

    (c1 := Context1(foo)).wrap()
    (c2 := Context2(foo)).wrap()

    for _ in range(n := 10):
        assert foo() == free

    assert c1.count == c2.count == n

    c1.count = c2.count = 0

    c1.unwrap()
    c2.unwrap()

    for _ in range(10):
        assert foo() == free

    assert c1.count == c2.count == 0


def test_wrapping_context_lazy_unwrap_before_call():
    free = 42

    def foo():
        return free

    (wc := DummyLazyWrappingContext(foo)).wrap()

    assert DummyLazyWrappingContext.is_wrapped(foo)
    assert not _UniversalWrappingContext.is_wrapped(foo)

    wc.unwrap()

    assert not DummyLazyWrappingContext.is_wrapped(foo)
    assert not _UniversalWrappingContext.is_wrapped(foo)

    for _ in range(10):
        assert foo() == free

        assert not DummyLazyWrappingContext.is_wrapped(foo)
        assert not _UniversalWrappingContext.is_wrapped(foo)

    assert wc.count == 0


@pytest.mark.asyncio
async def test_async_wrapper_frames_have_valid_linenos():
    """Regression test: async wrapping must not inject instructions with lineno=None.

    When wrap() is applied to an async function, the injected COROUTINE_ASSEMBLY
    instructions (GET_AWAITABLE, SEND, YIELD_VALUE, RESUME, ...) were previously
    emitted without line-number information, leaving f_lineno=None on the
    suspended trampoline frame.  inspect.stack() / inspect.getframeinfo() does
    ``lineno - 1 - context//2`` and raises TypeError when lineno is None.  Any
    code that inspects the call stack from inside or above a wrapped coroutine
    (e.g. IAST's report_stack) would therefore crash silently.
    """
    stack_from_inside = []

    def wrapper(f, args, kwargs):
        return f(*args, **kwargs)

    async def coro(x):
        # Capture the full call stack from inside the coroutine body.
        stack_from_inside.extend(inspect.stack())
        return x + 1

    wrap(coro, wrapper)

    result = await coro(41)
    assert result == 42

    # Every frame captured while the wrapped coroutine was running must have a
    # valid (non-None) line number so that inspect.getframeinfo() cannot crash.
    for frame_info in stack_from_inside:
        lineno = frame_info.lineno
        assert lineno is not None, (
            f"Frame {frame_info.filename}:{frame_info.function} has lineno=None — "
            "async wrapping injected instructions without line-number information"
        )


@pytest.mark.asyncio
async def test_lazy_async_wrapper_frames_have_valid_linenos():
    """Same lineno regression check for LazyWrappingContext on async functions.

    LazyWrappingContext applies a trampoline on first call, then promotes to
    full UniversalWrappingContext bytecode wrapping.  Both phases must produce
    frames with valid linenos.
    """
    for call_index in range(2):
        stack_from_inside = []

        async def coro(x):
            stack_from_inside.extend(inspect.stack())
            return x + 1

        DummyLazyWrappingContext(coro).wrap()

        result = await coro(41)
        assert result == 42

        for frame_info in stack_from_inside:
            lineno = frame_info.lineno
            assert lineno is not None, (
                f"[call {call_index}] Frame {frame_info.filename}:{frame_info.function} "
                f"has lineno=None — async wrapping injected instructions without "
                f"line-number information"
            )


@pytest.mark.asyncio
async def test_async_wrapper_throw_forwarding():
    """Regression test: throw() on a wrapped coroutine must be forwarded to the inner.

    Python 3.12+ uses CLEANUP_THROW bytecode in compiled ``return await sub_coro``
    to forward exceptions thrown into the outer coroutine to the inner one.  For
    Python's own ``_PyGen_yf`` mechanism to work correctly, the wrapping bytecode
    (COROUTINE_ASSEMBLY) must keep the inner coroutine as the top-of-stack iterator
    so that a ``throw()`` call on the wrapper is forwarded to the wrapped coroutine
    rather than absorbed by the wrapper frame.
    """
    received_cancel = []

    def wrapper(f, args, kwargs):
        return f(*args, **kwargs)

    async def inner():
        try:
            # Suspension point so throw() can be tested
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            received_cancel.append(True)
            raise

    wrap(inner, wrapper)

    # Advance wrapped coroutine to its first suspension point, then throw
    c = inner()
    try:
        c.send(None)
    except StopIteration:
        pass  # completed without suspending (shouldn't happen here)

    try:
        c.throw(asyncio.CancelledError())
    except (asyncio.CancelledError, StopIteration):
        pass

    assert received_cancel, (
        "CancelledError was not forwarded to the inner coroutine by the wrapper; throw() interception is broken"
    )


@pytest.mark.asyncio
async def test_lazy_async_wrapper_throw_forwarding():
    """Same throw-forwarding regression check for LazyWrappingContext."""
    for call_index in range(2):
        received_cancel = []

        async def inner():
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                received_cancel.append(True)
                raise

        DummyLazyWrappingContext(inner).wrap()

        c = inner()
        try:
            c.send(None)
        except StopIteration:
            pass

        try:
            c.throw(asyncio.CancelledError())
        except (asyncio.CancelledError, StopIteration):
            pass

        assert received_cancel, (
            f"[call {call_index}] CancelledError was not forwarded to the inner "
            "coroutine by the lazy wrapper; throw() interception is broken"
        )


# ---------------------------------------------------------------------------
# Resilience: a broken sub-context must not affect other contexts or the
# function's observable behaviour.  These tests exercise the per-context
# try/except inside _UniversalWrappingContext.__enter__/__return__/__exit__.
# ---------------------------------------------------------------------------


def test_wrapping_context_broken_enter_does_not_break_function():
    """A context whose __enter__ raises must not prevent the function from running."""

    class BrokenEnterContext(WrappingContext):
        def __enter__(self):
            raise RuntimeError("broken enter")

    class GoodContext(WrappingContext):
        entered = False
        return_value = NOTSET

        def __enter__(self):
            GoodContext.entered = True
            return super().__enter__()

        def __return__(self, value):
            GoodContext.return_value = value
            return super().__return__(value)

    def foo():
        return 42

    BrokenEnterContext(foo).wrap()
    GoodContext(foo).wrap()

    assert foo() == 42

    # The good context still ran despite the broken one.
    assert GoodContext.entered
    assert GoodContext.return_value == 42


def test_wrapping_context_broken_return_does_not_affect_return_value():
    """A context whose __return__ raises must not change the actual return value."""

    class BrokenReturnContext(WrappingContext):
        def __return__(self, value):
            raise RuntimeError("broken return")

    class GoodContext(WrappingContext):
        entered = False
        return_value = NOTSET

        def __enter__(self):
            GoodContext.entered = True
            return super().__enter__()

        def __return__(self, value):
            GoodContext.return_value = value
            return super().__return__(value)

    def foo():
        return 42

    BrokenReturnContext(foo).wrap()
    GoodContext(foo).wrap()

    assert foo() == 42

    assert GoodContext.entered
    assert GoodContext.return_value == 42


def test_wrapping_context_broken_exit_does_not_mask_original_exception():
    """A context whose __exit__ raises must not replace the original exception."""

    class BrokenExitContext(WrappingContext):
        def __exit__(self, exc_type, exc_value, traceback):
            super().__exit__(exc_type, exc_value, traceback)
            raise RuntimeError("broken exit")

    class GoodContext(WrappingContext):
        exited = False
        exc_info = None

        def __exit__(self, exc_type, exc_value, traceback):
            GoodContext.exited = True
            if exc_value is not None:
                GoodContext.exc_info = (exc_type, exc_value, traceback)
            super().__exit__(exc_type, exc_value, traceback)

    def foo():
        raise ValueError("original")

    BrokenExitContext(foo).wrap()
    GoodContext(foo).wrap()

    with pytest.raises(ValueError, match="original"):
        foo()

    assert GoodContext.exited
    _type, exc, _ = GoodContext.exc_info
    assert _type is ValueError


# ---------------------------------------------------------------------------
# Thread-based concurrency: ContextVar storage must be isolated per thread.
# ---------------------------------------------------------------------------


def test_wrapping_context_thread_concurrent():
    """Storage set by one thread must not bleed into a concurrent thread."""
    results = {}
    errors = []

    class ThreadIsolationContext(DummyWrappingContext):
        def __enter__(self):
            super().__enter__()
            self.set("tid", threading.get_ident())
            return self

        def __return__(self, value):
            stored = self.get("tid")
            current = threading.get_ident()
            if stored != current:
                errors.append(f"tid mismatch: stored={stored} current={current}")
            results[current] = stored
            return super().__return__(value)

    def foo():
        return threading.get_ident()

    wc = ThreadIsolationContext(foo)
    wc.wrap()

    barrier = threading.Barrier(10)

    def run():
        barrier.wait()
        foo()

    threads = [threading.Thread(target=run) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    # Each thread saw its own tid in storage.
    for tid, stored in results.items():
        assert tid == stored


# ---------------------------------------------------------------------------
# Async recursion: ContextVar stack must be correct for recursive coroutines.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrapping_context_async_recursive():
    """Each recursive coroutine call must have its own isolated storage slot."""
    values = []

    class AsyncRecursiveContext(DummyWrappingContext):
        def __enter__(self):
            super().__enter__()
            n = self.__frame__.f_locals["n"]
            self.set("n", n)
            values.append(("enter", n))
            return self

        def __return__(self, value):
            n = self.__frame__.f_locals["n"]
            assert self.get("n") == n, f"storage mismatch: expected {n}, got {self.get('n')}"
            values.append(("return", n))
            return super().__return__(value)

    async def afactorial(n):
        if n == 0:
            return 1
        return n * await afactorial(n - 1)

    wc = AsyncRecursiveContext(afactorial)
    wc.wrap()

    result = await afactorial(5)
    assert result == 120

    entered = [n for ev, n in values if ev == "enter"]
    returned = [n for ev, n in values if ev == "return"]
    assert entered == [5, 4, 3, 2, 1, 0]
    assert returned == [0, 1, 2, 3, 4, 5]
