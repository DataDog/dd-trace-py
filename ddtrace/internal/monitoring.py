"""Multiplexed sys.monitoring interface for ddtrace internal use.

A single sys.monitoring tool ID is shared across all ddtrace sub-systems.
Sub-systems implement :class:`MonitoringEventHandler` and register instances
via :func:`register`; the multiplexer dispatches each monitoring event to all
handlers registered for that code object.

Only the events corresponding to overridden handler methods are enabled,
so a handler that only overrides ``on_py_start`` pays no cost for the other
two events.

The handler instance itself serves as the registration key: pass the same
object to :func:`unregister` to remove it.
"""

from abc import ABC
import sys
from types import CodeType
from typing import NamedTuple
from typing import Optional
import weakref

from ddtrace.internal.threads import Lock


if sys.version_info < (3, 15):
    raise ImportError("ddtrace.internal.monitoring requires Python 3.15+")

_E = sys.monitoring.events  # type: ignore[unreachable]
DISABLE = sys.monitoring.DISABLE
_DISABLE = DISABLE

# sys.monitoring distinguishes "local" events, which can be enabled per code
# object via set_local_events, from events that can only be enabled globally
# via set_events. PY_START, PY_RETURN and LINE are local; PY_UNWIND is not and
# must be enabled globally, otherwise set_local_events raises
# "ValueError: invalid local event set".
_LOCAL_EVENTS = _E.PY_START | _E.PY_RETURN | _E.LINE
_GLOBAL_EVENTS = _E.PY_UNWIND

_tool_id: Optional[int] = None
_tool_lock = Lock()

# The set of global-only events currently enabled via sys.monitoring.set_events.
_active_global_events: int = 0

_registry: "weakref.WeakKeyDictionary[CodeType, dict[int, _Entry]]" = weakref.WeakKeyDictionary()
_registry_lock = Lock()


class MonitoringEventHandler(ABC):
    """Base class for sys.monitoring event handlers.

    Override only the methods for events you need.  The multiplexer enables
    only those events, so un-overridden methods incur no monitoring overhead.

    .. warning::
        Do not call :func:`register` or :func:`unregister` from inside an
        event handler method.  Doing so mutates the handler list while it is
        being iterated, which produces undefined behavior.
    """

    def on_py_start(self, code: CodeType, instruction_offset: int) -> None:
        pass

    def on_py_return(self, code: CodeType, instruction_offset: int, retval: object) -> None:
        pass

    def on_py_unwind(self, code: CodeType, instruction_offset: int, exception: BaseException) -> None:
        pass

    def on_py_line(self, code: CodeType, line_number: int) -> Optional[object]:
        """Return ``sys.monitoring.DISABLE`` to stop future events on this line."""
        return None


def _events_for_handler(handler: MonitoringEventHandler) -> int:
    """Return the OR of events corresponding to overridden handler methods."""
    cls: type[MonitoringEventHandler] = type(handler)
    base: type[MonitoringEventHandler] = MonitoringEventHandler
    events: int = 0
    if cls.on_py_start is not base.on_py_start:
        events |= _E.PY_START
    if cls.on_py_return is not base.on_py_return:
        events |= _E.PY_RETURN
    if cls.on_py_unwind is not base.on_py_unwind:
        events |= _E.PY_UNWIND
    if cls.on_py_line is not base.on_py_line:
        events |= _E.LINE
    return events


def _events_for(entries: "dict[int, _Entry]") -> int:
    events: int = 0
    for e in list(entries.values()):
        events |= e.events
    return events


def _set_local_events(tool_id: int, code: CodeType, events: int) -> None:
    # TODO(py-315): Pre-release Python 3.15 builds may reject PY_UNWIND
    # as a local event.  Fall back without it when the full set is invalid;
    # PY_UNWIND is still registered as a global callback via _setup() so
    # exception handling degrades gracefully rather than crashing.
    try:
        sys.monitoring.set_local_events(tool_id, code, events)
    except ValueError:
        fallback: int = events & ~_E.PY_UNWIND
        if fallback != events:
            sys.monitoring.set_local_events(tool_id, code, fallback)
        else:
            raise


class _Entry(NamedTuple):
    handler: MonitoringEventHandler
    events: int  # pre-computed from _events_for_handler


def _setup() -> int:
    """Claim a free tool ID and install the global callbacks (idempotent)."""
    global _tool_id

    if _tool_id is not None:
        return _tool_id

    with _tool_lock:
        if _tool_id is not None:
            return _tool_id

        for tid in range(5, -1, -1):
            try:
                sys.monitoring.use_tool_id(tid, "ddtrace")
                _tool_id = tid
                break
            except ValueError:
                continue
        else:
            raise RuntimeError("No free sys.monitoring tool ID available for ddtrace")

        sys.monitoring.register_callback(_tool_id, _E.PY_START, _on_py_start)
        sys.monitoring.register_callback(_tool_id, _E.PY_RETURN, _on_py_return)
        sys.monitoring.register_callback(_tool_id, _E.PY_UNWIND, _on_py_unwind)
        sys.monitoring.register_callback(_tool_id, _E.LINE, _on_py_line)

    return _tool_id


# ---------------------------------------------------------------------------
# Hot-path callbacks — no lock, no allocation
# ---------------------------------------------------------------------------


def _on_py_start(code: CodeType, instruction_offset: int) -> Optional[object]:
    entries: Optional[dict[int, _Entry]] = _registry.get(code)
    if not entries:
        return _DISABLE
    for e in list(entries.values()):
        if e.events & _E.PY_START:
            e.handler.on_py_start(code, instruction_offset)
    return None


def _on_py_return(code: CodeType, instruction_offset: int, retval: object) -> Optional[object]:
    entries: Optional[dict[int, _Entry]] = _registry.get(code)
    if not entries:
        return _DISABLE
    for e in list(entries.values()):
        if e.events & _E.PY_RETURN:
            e.handler.on_py_return(code, instruction_offset, retval)
    return None


def _on_py_unwind(code: CodeType, instruction_offset: int, exception: BaseException) -> Optional[object]:
    entries: Optional[dict[int, _Entry]] = _registry.get(code)
    # PY_UNWIND is a global event, so this callback fires for every unwinding
    # frame regardless of registration. We must not return DISABLE for
    # unregistered code: doing so would permanently disable the event for that
    # code location, and a later register() would not re-arm it (we never call
    # restart_events). Unwinding only happens on exceptions, so the extra lookup
    # cost on this already-slow path is negligible.
    if not entries:
        return None
    for e in list(entries.values()):
        if e.events & _E.PY_UNWIND:
            e.handler.on_py_unwind(code, instruction_offset, exception)
    return None


def _on_py_line(code: CodeType, line_number: int) -> Optional[object]:
    entries: Optional[dict[int, _Entry]] = _registry.get(code)
    if not entries:
        return _DISABLE
    disable: bool = True
    for e in list(entries.values()):
        if e.events & _E.LINE:
            if e.handler.on_py_line(code, line_number) is not _DISABLE:
                disable = False
    return _DISABLE if disable else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _enable_global_events(events: int) -> None:
    """Ensure the given global-only *events* are enabled (additive)."""
    global _active_global_events
    if events & ~_active_global_events:
        _active_global_events |= events
        assert _tool_id is not None  # nosec
        sys.monitoring.set_events(_tool_id, _active_global_events)


def _recompute_global_events() -> None:
    """Re-derive the set of global-only events from the current registry."""
    global _active_global_events
    if _tool_id is None:
        return
    needed: int = 0
    for entries in _registry.values():
        needed |= _events_for(entries)
    needed &= _GLOBAL_EVENTS
    if needed != _active_global_events:
        _active_global_events = needed
        sys.monitoring.set_events(_tool_id, needed)


def register(code: CodeType, handler: MonitoringEventHandler) -> None:
    """Register a monitoring event handler for *code*.

    The handler instance itself is the registration key; pass the same object
    to :func:`unregister` to remove it.
    """
    handler_events: int = _events_for_handler(handler)
    if not handler_events:
        raise ValueError("Handler overrides no MonitoringEventHandler methods")

    tool_id: int = _setup()
    entry: _Entry = _Entry(handler, handler_events)

    with _registry_lock:
        entries: Optional[dict[int, _Entry]] = _registry.get(code)
        if entries is None:
            _registry[code] = entries = {}
        entries[id(handler)] = entry
        all_events: int = _events_for(entries)
        sys.monitoring.set_local_events(tool_id, code, all_events & _LOCAL_EVENTS)
        _enable_global_events(all_events & _GLOBAL_EVENTS)


def refresh(code: CodeType) -> None:
    """Re-apply local events for *code*, resetting any per-line DISABLE state.

    Call this after adding a new hook for a line that may have been previously
    disabled via a ``DISABLE`` return from :meth:`MonitoringEventHandler.on_py_line`.
    """
    with _registry_lock:
        entries: Optional[dict[int, _Entry]] = _registry.get(code)
        if entries and _tool_id is not None:
            events: int = _events_for(entries) & _LOCAL_EVENTS
            # A DISABLE returned from a per-line callback is sticky until the
            # monitored event set changes or restart_events() is called.
            # Re-applying the same local events does not clear it; toggling
            # local events off and back on re-arms only this tool's DISABLE
            # marks for *code* without the global restart_events() call that
            # would reset other tools' disabled-event bookkeeping (coverage.py).
            sys.monitoring.set_local_events(_tool_id, code, 0)
            sys.monitoring.set_local_events(_tool_id, code, events)


def unregister(code: CodeType, handler: MonitoringEventHandler) -> None:
    """Remove *handler* from the handlers registered for *code*."""
    with _registry_lock:
        existing: Optional[dict[int, _Entry]] = _registry.get(code)
        if existing is None:
            return

        existing.pop(id(handler), None)

        if not existing:
            del _registry[code]
            if _tool_id is not None:
                sys.monitoring.set_local_events(_tool_id, code, 0)
        else:
            assert _tool_id is not None  # nosec
            sys.monitoring.set_local_events(_tool_id, code, _events_for(existing) & _LOCAL_EVENTS)

        _recompute_global_events()
