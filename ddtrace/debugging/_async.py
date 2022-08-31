import sys
from types import CoroutineType
from typing import List

from ddtrace.debugging._snapshot.collector import SnapshotContext
from ddtrace.internal import compat


async def dd_coroutine_wrapper(coro, contexts):
    # type: (CoroutineType, List[SnapshotContext]) -> CoroutineType
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
