import enum

from ddtrace.internal.core.events import Event
from ddtrace.internal.native._native import EventResult
from ddtrace.internal.native._native import EventResultDict
from ddtrace.internal.native._native import dispatch
from ddtrace.internal.native._native import dispatch_with_results
from ddtrace.internal.native._native import has_listeners
from ddtrace.internal.native._native import on
from ddtrace.internal.native._native import reset


class ResultType(enum.Enum):
    RESULT_OK = 0
    RESULT_EXCEPTION = 1
    RESULT_UNDEFINED = -1


def dispatch_event(event: Event) -> None:
    """Call all hooks for the provided event."""
    dispatch(event.event_name, (event,))


def raising_dispatch(event_id: str, args: tuple = ()) -> None:
    """Deprecated: use ``dispatch`` with try/except instead."""
    results = dispatch_with_results(event_id, args)
    for event in results.values():
        # we explicitly set the exception as a value to prevent caught exceptions from leaking
        if isinstance(event.value, Exception):
            raise event.value


__all__ = [
    "EventResult",
    "EventResultDict",
    "ResultType",
    "dispatch",
    "dispatch_event",
    "dispatch_with_results",
    "has_listeners",
    "on",
    "raising_dispatch",
    "reset",
]
