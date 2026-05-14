from ddtrace.internal.core.events import Event
from ddtrace.internal.native._native import EventResult
from ddtrace.internal.native._native import EventResultDict
from ddtrace.internal.native._native import ResultType
from ddtrace.internal.native._native import dispatch
from ddtrace.internal.native._native import dispatch_with_results
from ddtrace.internal.native._native import has_listeners
from ddtrace.internal.native._native import on
from ddtrace.internal.native._native import reset


def dispatch_event(event: Event, allow_raise: bool = False) -> None:
    """Call all hooks for the provided event.

    When ``allow_raise=True``, listener ``Exception``s propagate to the caller.
    ``BaseException``-derived exceptions always propagate regardless of ``allow_raise``.
    """
    dispatch(event.event_name, (event,), allow_raise)


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
