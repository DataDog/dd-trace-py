from typing import Any
from typing import Callable
from typing import Optional

from greenlet import gettrace
from greenlet import settrace

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


GreenletTraceCallback = Callable[[str, Any], None]


class _Trace:
    """Compose callbacks through the current thread's greenlet trace hook."""

    def __init__(self, original: Optional[GreenletTraceCallback]) -> None:
        self._original = original
        self._subscribers: list[GreenletTraceCallback] = []

    def __call__(self, event: str, args: Any) -> None:
        for subscriber in tuple(self._subscribers):
            try:
                subscriber(event, args)
            except Exception:
                log.debug("greenlet trace subscriber %r raised", subscriber, exc_info=True)
        if self._original is not None:
            self._original(event, args)


def register_trace(subscriber: GreenletTraceCallback) -> None:
    current_trace = gettrace()
    if isinstance(current_trace, _Trace):
        trace = current_trace
    else:
        trace = _Trace(current_trace)
        settrace(trace)

    if subscriber in trace._subscribers:
        return
    trace._subscribers.append(subscriber)


def unregister_trace(subscriber: GreenletTraceCallback) -> None:
    trace = gettrace()
    if not isinstance(trace, _Trace):
        return

    try:
        trace._subscribers.remove(subscriber)
    except ValueError:
        return

    if trace._subscribers:
        return

    settrace(trace._original)
