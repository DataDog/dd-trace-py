from ddtrace.internal.core.events import Event
from ddtrace.internal.native._native import EventResult
from ddtrace.internal.native._native import EventResultDict
from ddtrace.internal.native._native import ResultType
from ddtrace.internal.native._native import dispatch
from ddtrace.internal.native._native import dispatch_with_results
from ddtrace.internal.native._native import has_listeners as _native_has_listeners
from ddtrace.internal.native._native import on as _native_on
from ddtrace.internal.native._native import reset as _native_reset


# Python-side mirror of "which event ids currently have >= 1 listener".
#
# The native `dispatch` already early-returns for events with no listeners, but the caller
# still pays to build the args tuple and cross the Python<->native FFI boundary. Profiling a
# tracer-only Flask request shows ~47 of ~60 dispatches/request have NO listeners (they exist
# only for AppSec/IAST/LLMObs, which aren't loaded). Hot call sites can consult this set
# (`event_id in _events_with_listeners`) to skip that work entirely for the no-listener case
# without an FFI `has_listeners()` call.
#
# Correctness: every listener registration/removal in the library goes through `core.on` /
# `core.reset_listeners` (== these wrappers), so this set stays an exact mirror of native
# listener presence. Keep it that way — do not register listeners via the native functions
# directly.
_events_with_listeners: "set[str]" = set()


def on(event_id, callback, name=None):
    _native_on(event_id, callback, name)
    _events_with_listeners.add(event_id)


def reset(event_id=None, callback=None):
    _native_reset(event_id, callback)
    if event_id is None:
        # Reset-all: rebuild is unnecessary — no listeners remain.
        _events_with_listeners.clear()
    elif not _native_has_listeners(event_id):
        # Removed the last listener for this event.
        _events_with_listeners.discard(event_id)


def has_listeners(event_id) -> bool:
    """Whether ``event_id`` has any registered listener (pure-Python, no FFI)."""
    return event_id in _events_with_listeners


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
    "_events_with_listeners",
]
