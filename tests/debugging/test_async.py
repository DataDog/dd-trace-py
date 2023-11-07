import pytest

from ddtrace.debugging._async import dd_coroutine_wrapper


class MockSignalContext:
    def __init__(self):
        self.retval = None
        self.exc_info = None
        self.duration = None

    def exit(self, retval, exc_info, duration):
        self.retval = retval
        self.exc_info = exc_info
        self.duration = duration


@pytest.mark.asyncio
async def test_dd_coroutine_wrapper_return() -> None:
    contexts = [MockSignalContext() for _ in range(10)]

    async def coro():
        return 1

    retval = await dd_coroutine_wrapper(coro(), contexts)

    assert retval == 1

    assert all((context.retval, context.exc_info) == (1, (None, None, None)) for context in contexts)


@pytest.mark.asyncio
async def test_dd_coroutine_wrapper_exc() -> None:
    contexts = [MockSignalContext() for _ in range(10)]

    class MyException(Exception):
        pass

    async def coro():
        raise MyException("error")

    with pytest.raises(MyException):
        await dd_coroutine_wrapper(coro(), contexts)

    assert all((context.retval, context.exc_info[0]) == (None, MyException) for context in contexts)
