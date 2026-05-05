import enum

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


def dispatch_event(event) -> None:
    """Call all hooks for the provided event."""
    dispatch(event.event_name, (event,))


__all__ = [
    "EventResult",
    "EventResultDict",
    "ResultType",
    "dispatch",
    "dispatch_event",
    "dispatch_with_results",
    "has_listeners",
    "on",
    "reset",
]
