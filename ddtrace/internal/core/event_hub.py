from typing import Any
from typing import Optional

from ddtrace.internal import forksafe
from ddtrace.internal.core.events import Event
from ddtrace.internal.native._native import EventResult
from ddtrace.internal.native._native import EventResultDict
from ddtrace.internal.native._native import ResultType
from ddtrace.internal.native._native import dispatch
from ddtrace.internal.native._native import dispatch_with_results
from ddtrace.internal.native._native import has_listeners
from ddtrace.internal.native._native import on
from ddtrace.internal.native._native import reset as _reset


# Bundle activation and listener removal share a lock; dispatch stays entirely native.
_registration_lock = forksafe.RLock()
_listener_generation = 0


def reset(event_id: Optional[str] = None, callback: Optional[Any] = None) -> None:
    """Remove listeners and invalidate subscriber bundle activation."""
    global _listener_generation

    with _registration_lock:
        # Invalidate before removal, including when removal raises or reenters Python.
        _listener_generation += 1
        _reset(event_id, callback)


reset_listeners = reset


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
    "reset_listeners",
]
