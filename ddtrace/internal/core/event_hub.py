from ddtrace.internal.core.events import Event
from ddtrace.internal.native._native import EventResult
from ddtrace.internal.native._native import EventResultDict
from ddtrace.internal.native._native import ResultType
from ddtrace.internal.native._native import dispatch as _native_dispatch
from ddtrace.internal.native._native import dispatch_with_results
from ddtrace.internal.native._native import has_listeners
from ddtrace.internal.native._native import on
from ddtrace.internal.native._native import reset


def dispatch(event_id: str, args: tuple = (), allow_raise: bool = False) -> None:
    """Call all hooks for the provided event_id with the provided args.

    When ``allow_raise=True``, listener ``Exception``s propagate to the caller.
    ``BaseException``-derived exceptions always propagate regardless of ``allow_raise``.
    """
    _native_dispatch(event_id, args, allow_raise)


def dispatch_event(event: Event, allow_raise: bool = False) -> None:
    """Call all hooks for the provided event.

    When ``allow_raise=True``, listener ``Exception``s propagate to the caller.
    ``BaseException``-derived exceptions always propagate regardless of ``allow_raise``.
    """
    _native_dispatch(event.event_name, (event,), allow_raise)


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
