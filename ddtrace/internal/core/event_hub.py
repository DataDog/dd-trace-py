from ddtrace.internal.core.events import Event
from ddtrace.internal.native._native import EventResult
from ddtrace.internal.native._native import EventResultDict
from ddtrace.internal.native._native import ResultType
from ddtrace.internal.native._native import dispatch
from ddtrace.internal.native._native import dispatch_with_results
from ddtrace.internal.native._native import has_listeners
from ddtrace.internal.native._native import on
from ddtrace.internal.native._native import reset


def dispatch_event(event: Event) -> None:
    """Call all hooks for the provided event."""
    dispatch(event.event_name, (event,))


def raising_dispatch(event_id: str, args: tuple = ()) -> None:
    """Deprecated: use ``dispatch`` with try/except instead."""
    results = dispatch_with_results(event_id, args)
    for er in results.values():
        # we explicitly set the exception as a value to prevent caught exceptions from leaking
        if isinstance(er.value, Exception):
            raise er.value


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
