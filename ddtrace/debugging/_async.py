import sys
from types import CoroutineType
from typing import List

from ddtrace.debugging._snapshot.collector import SnapshotContext


async def dd_coroutine_wrapper(coro, contexts):
    # type: (CoroutineType, List[SnapshotContext]) -> CoroutineType
    try:
        retval = await coro
        exc_info = (None, None, None)
    except Exception:
        retval = None
        exc_info = sys.exc_info()  # type: ignore[assignment]

    for context in contexts:
        context.exit(retval, exc_info)

    _, exc, _ = exc_info
    if exc is not None:
        raise exc

    return retval
