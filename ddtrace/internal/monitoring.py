"""Multiplexed sys.monitoring interface for ddtrace internal use.

A single sys.monitoring tool ID is shared across all ddtrace sub-systems.
Sub-systems implement :class:`MonitoringEventHandler` and register instances
via :func:`register`; the multiplexer dispatches each monitoring event to all
handlers registered for that code object.

Only the events corresponding to overridden handler methods are enabled,
so a handler that only overrides ``on_py_start`` pays no cost for the other
events.

The handler instance itself serves as the registration key: pass the same
object to :func:`unregister` to remove it.
"""

from abc import ABC
import sys
from types import CodeType
from typing import Any
from typing import Iterator
from typing import NamedTuple
from typing import Optional
import weakref

from ddtrace.internal.logger import get_logger
from ddtrace.internal.threads import Lock


if sys.version_info < (3, 15):
    raise ImportError("ddtrace.internal.monitoring requires Python 3.15+")

log = get_logger(__name__)

_E = sys.monitoring.events
DISABLE = sys.monitoring.DISABLE
_DISABLE = DISABLE

# sys.monitoring distinguishes "local" events, which can be enabled per code
# object via set_local_events, from events that can only be enabled globally
# via set_events. PY_START, PY_RETURN and LINE are local; PY_UNWIND is not and
# must be enabled globally, otherwise set_local_events raises
# "ValueError: invalid local event set".
_LOCAL_EVENTS = _E.PY_START | _E.PY_RETURN | _E.LINE
_GLOBAL_EVENTS = _E.PY_UNWIND

# CPython tool IDs (see ddtrace/profiling/collector/_exception.pyx):
#   0 DEBUGGER_ID, 1 COVERAGE_ID, 2 PROFILER_ID, 3 handled exceptions, 5 OPTIMIZER_ID.
# Never claim reserved/unmigrated slots; prefer 4 then 3.
_HANDLED_EXCEPTIONS_TOOL_ID = 3
_CANDIDATE_TOOL_IDS = (4, _HANDLED_EXCEPTIONS_TOOL_ID)

_tool_id: Optional[int] = None
_tool_lock = Lock()

# The set of global-only events currently enabled via sys.monitoring.set_events.
_active_global_events: int = 0

_registry_lock = Lock()


class _IdentityWeakKeyDictionary:
    """Weak mapping keyed by object identity (not equality).

    Unlike ``weakref.WeakKeyDictionary``, lookups use ``is`` rather than
    ``CodeType.__eq__``, so distinct code objects for the same source remain
    separate entries.
    """

    __slots__ = ("_data", "_on_remove")

    def __init__(self, on_remove: Optional[Any] = None) -> None:
        self._data: dict[int, tuple[weakref.ref[Any], Any]] = {}
        self._on_remove = on_remove

    def _make_remove(self, key_id: int) -> Any:
        def remove(_ref: weakref.ref[Any], selfref: weakref.ref[Any] = weakref.ref(self)) -> None:
            self = selfref()
            if self is None:
                return
            if self._data.pop(key_id, None) is not None and self._on_remove is not None:
                self._on_remove()

        return remove

    def get(self, key: CodeType, default: Any = None) -> Any:
        item = self._data.get(id(key))
        if item is None:
            return default
        ref, value = item
        if ref() is key:
            return value
        return default

    def __setitem__(self, key: CodeType, value: Any) -> None:
        key_id = id(key)
        self._data[key_id] = (weakref.ref(key, self._make_remove(key_id)), value)

    def __delitem__(self, key: CodeType) -> None:
        key_id = id(key)
        if key_id not in self._data:
            raise KeyError(key)
        del self._data[key_id]

    def values(self) -> Iterator[Any]:
        for ref, value in self._data.values():
            if ref() is not None:
                yield value


def _on_registry_entry_removed() -> None:
    with _registry_lock:
        _recompute_global_events()


_registry: _IdentityWeakKeyDictionary = _IdentityWeakKeyDictionary(on_remove=_on_registry_entry_removed)


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


class _Entry(NamedTuple):
    handler: MonitoringEventHandler
    events: int  # pre-computed from _events_for_handler


class _CodeHandlers:
    """Per-code handler table with a pre-built snapshot for hot-path dispatch."""

    __slots__ = ("_by_handler", "snapshot")

    def __init__(self) -> None:
        self._by_handler: dict[int, _Entry] = {}
        self.snapshot: tuple[_Entry, ...] = ()

    def __len__(self) -> int:
        return len(self._by_handler)

    def set_handler(self, handler_id: int, entry: _Entry) -> None:
        self._by_handler[handler_id] = entry
        self.snapshot = tuple(self._by_handler.values())

    def pop_handler(self, handler_id: int) -> None:
        self._by_handler.pop(handler_id, None)
        self.snapshot = tuple(self._by_handler.values())


def _events_for(handlers: _CodeHandlers) -> int:
    events: int = 0
    for e in handlers.snapshot:
        events |= e.events
    return events


def _setup() -> int:
    """Claim a free tool ID and install the global callbacks (idempotent)."""
    global _tool_id

    if _tool_id is not None:
        return _tool_id

    with _tool_lock:
        if _tool_id is not None:
            return _tool_id

        for tid in _CANDIDATE_TOOL_IDS:
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
# Hot-path callbacks — no lock; iterate a pre-built handler snapshot tuple
# ---------------------------------------------------------------------------


def _dispatch_start(code: CodeType, instruction_offset: int, entry: _Entry) -> None:
    entry.handler.on_py_start(code, instruction_offset)


def _dispatch_return(code: CodeType, instruction_offset: int, retval: object, entry: _Entry) -> None:
    entry.handler.on_py_return(code, instruction_offset, retval)


def _dispatch_unwind(code: CodeType, instruction_offset: int, exception: BaseException, entry: _Entry) -> None:
    entry.handler.on_py_unwind(code, instruction_offset, exception)


def _dispatch_line(code: CodeType, line_number: int, entry: _Entry) -> Optional[object]:
    return entry.handler.on_py_line(code, line_number)


def _on_py_start(code: CodeType, instruction_offset: int) -> Optional[object]:
    handlers: Optional[_CodeHandlers] = _registry.get(code)
    if not handlers or not handlers.snapshot:
        return _DISABLE
    for e in handlers.snapshot:
        if e.events & _E.PY_START:
            try:
                _dispatch_start(code, instruction_offset, e)
            except Exception:
                log.warning("monitoring PY_START handler failed", exc_info=True)
    return None


def _on_py_return(code: CodeType, instruction_offset: int, retval: object) -> Optional[object]:
    handlers: Optional[_CodeHandlers] = _registry.get(code)
    if not handlers or not handlers.snapshot:
        return _DISABLE
    for e in handlers.snapshot:
        if e.events & _E.PY_RETURN:
            try:
                _dispatch_return(code, instruction_offset, retval, e)
            except Exception:
                log.warning("monitoring PY_RETURN handler failed", exc_info=True)
    return None


def _on_py_unwind(code: CodeType, instruction_offset: int, exception: BaseException) -> Optional[object]:
    handlers: Optional[_CodeHandlers] = _registry.get(code)
    # PY_UNWIND is a global event, so this callback fires for every unwinding
    # frame regardless of registration. We must not return DISABLE for
    # unregistered code: doing so would permanently disable the event for that
    # code location, and a later register() would not re-arm it (we never call
    # restart_events). Unwinding only happens on exceptions, so the extra lookup
    # cost on this already-slow path is negligible.
    if not handlers or not handlers.snapshot:
        return None
    for e in handlers.snapshot:
        if e.events & _E.PY_UNWIND:
            try:
                _dispatch_unwind(code, instruction_offset, exception, e)
            except Exception:
                log.warning("monitoring PY_UNWIND handler failed", exc_info=True)
    return None


def _on_py_line(code: CodeType, line_number: int) -> Optional[object]:
    handlers: Optional[_CodeHandlers] = _registry.get(code)
    if not handlers or not handlers.snapshot:
        return _DISABLE
    disable: bool = True
    for e in handlers.snapshot:
        if e.events & _E.LINE:
            try:
                if _dispatch_line(code, line_number, e) is not _DISABLE:
                    disable = False
            except Exception:
                log.warning("monitoring LINE handler failed", exc_info=True)
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
    for handlers in _registry.values():
        needed |= _events_for(handlers)
    needed &= _GLOBAL_EVENTS
    if needed != _active_global_events:
        _active_global_events = needed
        sys.monitoring.set_events(_tool_id, needed)


def _set_local_events(tool_id: int, code: CodeType, events: int) -> None:
    sys.monitoring.set_local_events(tool_id, code, events)


def _rearm_local_events(tool_id: int, code: CodeType, events: int) -> None:
    # A DISABLE returned from a per-line callback is sticky until the monitored
    # event set changes or restart_events() is called. Re-applying the same
    # local events does not clear it; toggling local events off and back on
    # re-arms only this tool's DISABLE marks for *code* without the global
    # restart_events() call that would reset other tools' disabled-event
    # bookkeeping (coverage.py).
    _set_local_events(tool_id, code, 0)
    _set_local_events(tool_id, code, events)


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
        handlers: Optional[_CodeHandlers] = _registry.get(code)
        if handlers is None:
            _registry[code] = handlers = _CodeHandlers()

        had_line: bool = any(e.events & _E.LINE for e in handlers.snapshot)
        handlers.set_handler(id(handler), entry)
        local_events: int = _events_for(handlers) & _LOCAL_EVENTS

        if (handler_events & _E.LINE) and had_line:
            _rearm_local_events(tool_id, code, local_events)
        else:
            _set_local_events(tool_id, code, local_events)

        _enable_global_events(_events_for(handlers) & _GLOBAL_EVENTS)


def refresh(code: CodeType) -> None:
    """Re-apply local events for *code*, resetting any per-line DISABLE state.

    Call this after adding a new hook for a line that may have been previously
    disabled via a ``DISABLE`` return from :meth:`MonitoringEventHandler.on_py_line`.
    """
    with _registry_lock:
        handlers: Optional[_CodeHandlers] = _registry.get(code)
        if handlers and _tool_id is not None:
            events: int = _events_for(handlers) & _LOCAL_EVENTS
            _rearm_local_events(_tool_id, code, events)


def unregister(code: CodeType, handler: MonitoringEventHandler) -> None:
    """Remove *handler* from the handlers registered for *code*."""
    with _registry_lock:
        handlers: Optional[_CodeHandlers] = _registry.get(code)
        if handlers is None:
            return

        handlers.pop_handler(id(handler))

        if not handlers:
            del _registry[code]
            if _tool_id is not None:
                _set_local_events(_tool_id, code, 0)
        else:
            assert _tool_id is not None  # nosec
            _set_local_events(_tool_id, code, _events_for(handlers) & _LOCAL_EVENTS)

        _recompute_global_events()
