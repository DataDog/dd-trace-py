import pytest


@pytest.mark.subprocess(err=None)
def test_as_completed_preserves_lazy_scheduling() -> None:
    import asyncio
    import contextvars
    import sys

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.wrapping import is_wrapped
    from ddtrace.profiling import _asyncio

    assert stack.is_available, stack.failure_msg
    # Without the wrapper this test would pass without exercising the fix
    assert is_wrapped(asyncio.as_completed)
    assert _asyncio.ASYNCIO_IMPORTED

    var: contextvars.ContextVar[str] = contextvars.ContextVar("var", default="unset")

    async def read() -> str:
        return var.get()

    async def main() -> list[str]:
        iterator = asyncio.as_completed([read()])
        var.set("set")
        return [await future for future in iterator]

    # Before Python 3.13, the stdlib as_completed schedules its inputs on the first iteration.
    expected: str = "set" if sys.hexversion < 0x030D0000 else "unset"
    assert asyncio.run(main()) == [expected]
