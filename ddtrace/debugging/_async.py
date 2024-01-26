import sys
from types import CoroutineType
from typing import Iterable

from ddtrace.debugging._signal.collector import SignalContext
from ddtrace.internal import compat


async def dd_coroutine_wrapper(coro: CoroutineType, contexts: Iterable[SignalContext]) -> CoroutineType:
    start_time = compat.monotonic_ns()
    try:
        retval = await coro
        end_time = compat.monotonic_ns()
        exc_info = (None, None, None)
    except Exception:
        end_time = compat.monotonic_ns()
        retval = None
        exc_info = sys.exc_info()  # type: ignore[assignment]

    for context in contexts:
        context.exit(retval, exc_info, end_time - start_time)

    _, exc, _ = exc_info
    if exc is not None:
        raise exc

    return retval
