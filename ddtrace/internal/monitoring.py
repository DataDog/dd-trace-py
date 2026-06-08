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

_E = sys.monitoring.events
DISABLE = sys.monitoring.DISABLE
_DISABLE = DISABLE

_tool_id: Optional[int] = None
_tool_lock = Lock()

_registry: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
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
    cls = type(handler)
    base = MonitoringEventHandler
    events = 0
    if cls.on_py_start is not base.on_py_start:
        events |= _E.PY_START
    if cls.on_py_return is not base.on_py_return:
        events |= _E.PY_RETURN
    if cls.on_py_unwind is not base.on_py_unwind:
        events |= _E.PY_UNWIND
    if cls.on_py_line is not base.on_py_line:
        events |= _E.LINE
    return events


def _events_for(entries: dict) -> int:
    events = 0
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
        fallback = events & ~_E.PY_UNWIND
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
    entries = _registry.get(code)
    if not entries:
        return _DISABLE
    for e in list(entries.values()):
        if e.events & _E.PY_START:
            e.handler.on_py_start(code, instruction_offset)
    return None


def _on_py_return(code: CodeType, instruction_offset: int, retval: object) -> Optional[object]:
    entries = _registry.get(code)
    if not entries:
        return _DISABLE
    for e in list(entries.values()):
        if e.events & _E.PY_RETURN:
            e.handler.on_py_return(code, instruction_offset, retval)
    return None


def _on_py_unwind(code: CodeType, instruction_offset: int, exception: BaseException) -> Optional[object]:
    entries = _registry.get(code)
    if not entries:
        return _DISABLE
    for e in list(entries.values()):
        if e.events & _E.PY_UNWIND:
            e.handler.on_py_unwind(code, instruction_offset, exception)
    return None


def _on_py_line(code: CodeType, line_number: int) -> Optional[object]:
    entries = _registry.get(code)
    if not entries:
        return _DISABLE
    disable = True
    for e in list(entries.values()):
        if e.events & _E.LINE:
            if e.handler.on_py_line(code, line_number) is not _DISABLE:
                disable = False
    return _DISABLE if disable else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def register(code: CodeType, handler: MonitoringEventHandler) -> None:
    """Register a monitoring event handler for *code*.

    The handler instance itself is the registration key; pass the same object
    to :func:`unregister` to remove it.
    """
    handler_events = _events_for_handler(handler)
    if not handler_events:
        raise ValueError("Handler overrides no MonitoringEventHandler methods")

    tool_id = _setup()
    entry = _Entry(handler, handler_events)

    with _registry_lock:
        entries = _registry.get(code)
        if entries is None:
            _registry[code] = entries = {}
        entries[id(handler)] = entry
        _set_local_events(tool_id, code, _events_for(entries))


def refresh(code: CodeType) -> None:
    """Re-apply local events for *code*, resetting any per-line DISABLE state.

    Call this after adding a new hook for a line that may have been previously
    disabled via a ``DISABLE`` return from :meth:`MonitoringEventHandler.on_py_line`.
    """
    with _registry_lock:
        entries = _registry.get(code)
        if entries and _tool_id is not None:
            _set_local_events(_tool_id, code, _events_for(entries))


def unregister(code: CodeType, handler: MonitoringEventHandler) -> None:
    """Remove *handler* from the handlers registered for *code*."""
    with _registry_lock:
        existing = _registry.get(code)
        if existing is None:
            return

        existing.pop(id(handler), None)

        if not existing:
            del _registry[code]
            if _tool_id is not None:
                sys.monitoring.set_local_events(_tool_id, code, 0)
        else:
            assert _tool_id is not None  # nosec
            _set_local_events(_tool_id, code, _events_for(existing))
