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
from typing import Callable
from typing import Iterable
from typing import NamedTuple
from typing import Optional
import weakref

from ddtrace.internal.logger import get_logger
from ddtrace.internal.threads import Lock


if sys.version_info < (3, 12):
    raise ImportError("ddtrace.internal.monitoring requires Python 3.12+")

log = get_logger(__name__)

_E = sys.monitoring.events
_DISABLE = sys.monitoring.DISABLE

# PY_UNWIND became a per-code "other" event only in Python 3.15 (its event bit even
# moved, 0x1000 -> 0x2000). On 3.12-3.14 it is a global-only event that
# set_local_events() rejects. The only PY_UNWIND consumer is itself 3.15+-gated.
if sys.version_info >= (3, 15):
    _LOCAL_EVENTS = _E.PY_START | _E.PY_RETURN | _E.LINE | _E.PY_UNWIND
    _SUPPORTS_LOCAL_PY_UNWIND = True
else:
    _LOCAL_EVENTS = _E.PY_START | _E.PY_RETURN | _E.LINE
    _SUPPORTS_LOCAL_PY_UNWIND = False


class MonitoringToolUnavailable(RuntimeError):
    """Raised when no free sys.monitoring tool ID is available for ddtrace."""


_MULTIPLEXER_TOOL_NAME = "ddtrace"
# sys.monitoring exposes six tool IDs (0–5). 0/1/2/5 are conventionally reserved
# for debugger/coverage/profiler/optimizer; 3 and 4 are the only undefined
# slots for custom tools (see CPython docs). Prefer 4 first, consistent with
# coverage's _DD_CANDIDATE_SLOTS, and fall back to 3 if another tool claimed it.
_CANDIDATE_TOOL_IDS = (4, 3)

_tool_id: Optional[int] = None
_tool_lock = Lock()

_registry_lock = Lock()


class _IdentityWeakKeyDictionary:
    """Weak mapping keyed by object identity (not equality).

    Unlike ``weakref.WeakKeyDictionary``, lookups use ``is`` rather than
    ``CodeType.__eq__``, so distinct code objects for the same source remain
    separate entries.
    """

    # NOTE: CodeType equality is structural. Keep identity semantics here and in
    # every code-object registry built on this class, or separately compiled/reloaded
    # copies of the same code will overwrite each other.

    __slots__ = ("_data",)

    def __init__(self) -> None:
        self._data: dict[int, tuple[weakref.ref[Any], Any]] = {}

    def _make_remove(self, key_id: int) -> Any:
        def remove(_ref: weakref.ref[Any]) -> None:
            self._data.pop(key_id, None)

        return remove

    def get(self, key: CodeType, default: Any = None) -> Any:
        item = self._data.get(id(key))
        if item is None:
            return default
        ref, value = item
        if ref() is key:
            return value
        return default

    def __contains__(self, key: CodeType) -> bool:
        item = self._data.get(id(key))
        return item is not None and item[0]() is key

    def __iter__(self) -> Any:
        for ref, _value in tuple(self._data.values()):
            key = ref()
            if key is not None:
                yield key

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, key: CodeType) -> Any:
        item = self._data.get(id(key))
        if item is None or item[0]() is not key:
            raise KeyError(key)
        return item[1]

    def __setitem__(self, key: CodeType, value: Any) -> None:
        key_id = id(key)
        self._data[key_id] = (weakref.ref(key, self._make_remove(key_id)), value)

    def __delitem__(self, key: CodeType) -> None:
        key_id = id(key)
        if key_id not in self._data:
            raise KeyError(key)
        del self._data[key_id]

    def pop(self, key: CodeType, *default: Any) -> Any:
        try:
            value = self[key]
        except KeyError:
            if default:
                return default[0]
            raise
        del self[key]
        return value

    def clear(self) -> None:
        self._data.clear()


_registry: _IdentityWeakKeyDictionary = _IdentityWeakKeyDictionary()
_registry_version: int = 0
_exclusive_cache_version: int = -1
_exclusive_handler_id: Optional[int] = None
# AIDEV-NOTE: Direct callbacks are an opt-in fast path for long-lived singleton
# handlers. Every direct/multiplexed transition must re-arm that event for all
# registered code objects because a prior direct callback may have returned DISABLE.
# Missing means no handler currently uses the event, None means multiplexed,
# and a handler value means its callback is installed directly.
_direct_event_handlers: dict[int, Optional["MonitoringEventHandler"]] = {}


class MonitoringEventHandler(ABC):
    """Base class for sys.monitoring event handlers.

    Override only the methods for events you need.  The multiplexer enables
    only those events, so un-overridden methods incur no monitoring overhead.

    .. warning::
        Do not call :func:`register` or :func:`unregister` from inside an
        event handler method.  Doing so mutates the handler list while it is
        being iterated, which produces undefined behavior.

    .. warning::
        Exceptions from ``on_py_start``/``on_py_return``/``on_py_unwind`` are
        not caught -- they propagate into the monitored frame and skip any
        later handler for the same event, exactly as sys.monitoring itself
        would deliver a callback failure. Catch your own exceptions if a
        handler must not affect the monitored function's behavior.
        ``on_py_line`` is caught and logged instead, since independent
        handlers commonly share one code object's LINE registration.
    """

    # Long-lived singleton handlers can opt into direct callback registration.
    # The multiplexer restores normal fan-out as soon as another handler needs
    # the same event.
    _direct_events: int = 0

    def on_py_start(self, code: CodeType, instruction_offset: int) -> Optional[object]:
        """Return ``sys.monitoring.DISABLE`` to request disabling future PY_START events.

        The multiplexer forwards ``DISABLE`` to CPython only when every registered
        PY_START handler for this code object returns it, mirroring ``on_py_line``.
        """
        pass

    def on_py_return(self, code: CodeType, instruction_offset: int, retval: object) -> None:
        pass

    def on_py_unwind(self, code: CodeType, instruction_offset: int, exception: BaseException) -> None:
        pass

    def on_py_line(self, code: CodeType, line_number: int) -> Optional[object]:
        """Return ``sys.monitoring.DISABLE`` to request disabling future LINE events.

        The multiplexer forwards ``DISABLE`` to CPython only when every registered
        LINE handler for this code object returns it. If any handler returns a
        different value, LINE events continue for that location.
        """
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

    __slots__ = ("_by_handler", "disabled_events", "snapshot")

    def __init__(self) -> None:
        self._by_handler: dict[int, _Entry] = {}
        # Event bits for which the aggregate callback has returned DISABLE at
        # least once. This is intentionally conservative: stale bits can cause
        # an unnecessary targeted re-arm, while missing a bit can lose events.
        self.disabled_events: int = 0
        self.snapshot: tuple[_Entry, ...] = ()

    def __len__(self) -> int:
        return len(self._by_handler)

    def set_handler(self, handler_id: int, entry: _Entry) -> None:
        self._by_handler[handler_id] = entry
        self.snapshot = tuple(self._by_handler.values())

    def pop_handler(self, handler_id: int) -> Optional[_Entry]:
        entry = self._by_handler.pop(handler_id, None)
        self.snapshot = tuple(self._by_handler.values())
        return entry


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
            existing = sys.monitoring.get_tool(tid)
            if existing is not None and existing != _MULTIPLEXER_TOOL_NAME:
                continue
            try:
                if existing is None:
                    sys.monitoring.use_tool_id(tid, _MULTIPLEXER_TOOL_NAME)
                _tool_id = tid
                break
            except ValueError:
                continue
        else:
            raise MonitoringToolUnavailable("No free sys.monitoring tool ID available for ddtrace")

        sys.monitoring.register_callback(_tool_id, _E.PY_START, _on_py_start)
        sys.monitoring.register_callback(_tool_id, _E.PY_RETURN, _on_py_return)
        if _SUPPORTS_LOCAL_PY_UNWIND:
            sys.monitoring.register_callback(_tool_id, _E.PY_UNWIND, _on_py_unwind)
        sys.monitoring.register_callback(_tool_id, _E.LINE, _on_py_line)

    return _tool_id


# ---------------------------------------------------------------------------
# Hot-path callbacks — no lock; iterate a pre-built handler snapshot tuple
# ---------------------------------------------------------------------------


def _on_py_start(code: CodeType, instruction_offset: int) -> Optional[object]:
    handlers: Optional[_CodeHandlers] = _registry.get(code)
    if not handlers or not handlers.snapshot:
        return _DISABLE
    # Deliberately uncaught: see the propagation warning on MonitoringEventHandler.
    # DISABLE is forwarded only when every PY_START handler for this code object
    # returns it, mirroring on_py_line. Existing handlers (the wrapping context)
    # return None, so behaviour is unchanged unless a handler opts into DISABLE.
    disable: bool = True
    for e in handlers.snapshot:
        if e.events & _E.PY_START:
            if e.handler.on_py_start(code, instruction_offset) is not _DISABLE:
                disable = False
    if disable:
        handlers.disabled_events |= _E.PY_START
        return _DISABLE
    return None


def _on_py_return(code: CodeType, instruction_offset: int, retval: object) -> Optional[object]:
    handlers: Optional[_CodeHandlers] = _registry.get(code)
    if not handlers or not handlers.snapshot:
        return _DISABLE
    # Deliberately uncaught: see the propagation warning on MonitoringEventHandler.
    for e in handlers.snapshot:
        if e.events & _E.PY_RETURN:
            e.handler.on_py_return(code, instruction_offset, retval)
    return None


def _on_py_unwind(code: CodeType, instruction_offset: int, exception: BaseException) -> Optional[object]:
    handlers: Optional[_CodeHandlers] = _registry.get(code)
    if not handlers or not handlers.snapshot:
        return _DISABLE
    # Deliberately uncaught: see the propagation warning on MonitoringEventHandler.
    for e in handlers.snapshot:
        if e.events & _E.PY_UNWIND:
            e.handler.on_py_unwind(code, instruction_offset, exception)
    return None


def _on_py_line(code: CodeType, line_number: int) -> Optional[object]:
    handlers: Optional[_CodeHandlers] = _registry.get(code)
    if not handlers or not handlers.snapshot:
        return _DISABLE
    disable: bool = True
    for e in handlers.snapshot:
        if e.events & _E.LINE:
            try:
                if e.handler.on_py_line(code, line_number) is not _DISABLE:
                    disable = False
            except Exception:
                log.warning("monitoring LINE handler failed", exc_info=True)
                disable = False
    if disable:
        handlers.disabled_events |= _E.LINE
        return _DISABLE
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _set_local_events(tool_id: int, code: CodeType, events: int) -> None:
    sys.monitoring.set_local_events(tool_id, code, events)


def _rearm_local_events(tool_id: int, code: CodeType, events: int, rearm_events: int) -> None:
    # A DISABLE return is sticky until the monitored event set changes or
    # restart_events() is called. Re-applying the same local events does not
    # clear it. Toggle only the requested event bits so unrelated lifecycle
    # events remain enabled throughout the re-arm operation.
    _set_local_events(tool_id, code, events & ~rearm_events)
    _set_local_events(tool_id, code, events)


def _iter_events(events: int) -> Iterable[int]:
    for event in (_E.PY_START, _E.PY_RETURN, _E.PY_UNWIND, _E.LINE):
        if events & event:
            yield event


def _multiplexer_callback(event: int) -> Callable[..., Optional[object]]:
    if event == _E.PY_START:
        return _on_py_start
    if event == _E.PY_RETURN:
        return _on_py_return
    if event == _E.PY_UNWIND:
        return _on_py_unwind
    if event == _E.LINE:
        return _on_py_line
    raise ValueError(f"Unsupported local monitoring event: {event}")


def _direct_callback(handler: MonitoringEventHandler, event: int) -> Callable[..., Optional[object]]:
    if event == _E.PY_START:
        return handler.on_py_start
    if event == _E.PY_RETURN:
        return handler.on_py_return
    if event == _E.PY_UNWIND:
        return handler.on_py_unwind
    if event == _E.LINE:

        def on_line(code: CodeType, line_number: int) -> Optional[object]:
            try:
                return handler.on_py_line(code, line_number)
            except Exception:
                log.warning("monitoring LINE handler failed", exc_info=True)
                return None

        return on_line
    raise ValueError(f"Unsupported local monitoring event: {event}")


def _rearm_event_for_all_codes(tool_id: int, event: int) -> None:
    for code in _registry:
        handlers: Optional[_CodeHandlers] = _registry.get(code)
        if handlers is None:
            continue
        local_events = _events_for(handlers) & _LOCAL_EVENTS
        if local_events & event:
            _rearm_local_events(tool_id, code, local_events, event)
            handlers.disabled_events &= ~event


def _configure_event_callbacks_for_registration(
    tool_id: int, handler: MonitoringEventHandler, handler_events: int
) -> None:
    for event in _iter_events(handler_events):
        if event not in _direct_event_handlers:
            if handler._direct_events & event:
                sys.monitoring.register_callback(tool_id, event, _direct_callback(handler, event))
                _direct_event_handlers[event] = handler
            else:
                _direct_event_handlers[event] = None
            continue

        owner = _direct_event_handlers[event]
        if owner is handler or owner is None:
            continue

        sys.monitoring.register_callback(tool_id, event, _multiplexer_callback(event))
        _direct_event_handlers[event] = None
        _rearm_event_for_all_codes(tool_id, event)


def _recompute_event_callback(tool_id: int, event: int) -> None:
    unique_handlers: dict[int, MonitoringEventHandler] = {}
    for code in _registry:
        handlers: Optional[_CodeHandlers] = _registry.get(code)
        if handlers is None:
            continue
        for entry in handlers.snapshot:
            if entry.events & event:
                unique_handlers[id(entry.handler)] = entry.handler
                if len(unique_handlers) > 1:
                    break
        if len(unique_handlers) > 1:
            break

    current = _direct_event_handlers.get(event)
    if not unique_handlers:
        if current is not None:
            sys.monitoring.register_callback(tool_id, event, _multiplexer_callback(event))
        _direct_event_handlers.pop(event, None)
        return

    handler = next(iter(unique_handlers.values()))
    desired = handler if len(unique_handlers) == 1 and handler._direct_events & event else None
    if current is desired:
        return

    callback = _direct_callback(handler, event) if desired is not None else _multiplexer_callback(event)
    sys.monitoring.register_callback(tool_id, event, callback)
    _direct_event_handlers[event] = desired
    _rearm_event_for_all_codes(tool_id, event)


def ensure_tool() -> int:
    """Claim the shared tool ID or raise MonitoringToolUnavailable."""
    return _setup()


def register(code: CodeType, handler: MonitoringEventHandler) -> None:
    """Register a monitoring event handler for *code*.

    The handler instance itself is the registration key; pass the same object
    to :func:`unregister` to remove it.
    """
    global _registry_version

    declared_events: int = _events_for_handler(handler)
    if (declared_events & _E.PY_UNWIND) and not _SUPPORTS_LOCAL_PY_UNWIND:
        raise RuntimeError("on_py_unwind handlers require Python 3.15+ (PY_UNWIND is a global-only event on 3.12-3.14)")
    handler_events = declared_events & _LOCAL_EVENTS
    if not handler_events:
        raise ValueError("Handler overrides no local MonitoringEventHandler methods")

    tool_id: int = _setup()
    entry: _Entry = _Entry(handler, handler_events)

    with _registry_lock:
        handlers: Optional[_CodeHandlers] = _registry.get(code)
        if handlers is None:
            _registry[code] = handlers = _CodeHandlers()

        # Events already provided by an existing handler may have been DISABLE'd by
        # that handler's callback return (LINE, or PY_START when a handler opts in).
        # If the new handler shares any of those events, re-arm via the tool-scoped
        # toggle so the new handler actually receives them. This generalises the
        # previous LINE-only re-arm to every local event.
        existing_events: int = _events_for(handlers)
        handlers.set_handler(id(handler), entry)
        _registry_version += 1
        _configure_event_callbacks_for_registration(tool_id, handler, handler_events)
        local_events: int = _events_for(handlers) & _LOCAL_EVENTS
        handlers.disabled_events &= local_events

        rearm_events = handler_events & existing_events & handlers.disabled_events
        if rearm_events:
            _rearm_local_events(tool_id, code, local_events, rearm_events)
        else:
            _set_local_events(tool_id, code, local_events)


def _refresh(tool_id: int, code: CodeType, events: int) -> None:
    handlers: Optional[_CodeHandlers] = _registry.get(code)
    if handlers is None:
        return

    local_events: int = _events_for(handlers) & _LOCAL_EVENTS
    direct_events = sum(event for event, owner in _direct_event_handlers.items() if owner is not None)
    rearm_events = events & local_events & (handlers.disabled_events | direct_events)
    if rearm_events:
        _rearm_local_events(tool_id, code, local_events, rearm_events)


def refresh(code: CodeType, events: int) -> None:
    """Re-arm disabled local *events* for *code* without changing unrelated events."""
    with _registry_lock:
        if _tool_id is not None:
            _refresh(_tool_id, code, events)


def refresh_many(codes: Iterable[CodeType], events: int) -> None:
    """Re-arm disabled local *events* for multiple code objects under one lock."""
    with _registry_lock:
        if _tool_id is None:
            return
        for code in codes:
            _refresh(_tool_id, code, events)


def restart_events_if_exclusive(handler: MonitoringEventHandler) -> Optional[int]:
    """Globally re-arm events when *handler* is the only monitoring consumer.

    Returns the local registry version when the restart is safe, otherwise
    returns ``None`` without changing monitoring state.
    """
    global _exclusive_cache_version
    global _exclusive_handler_id

    with _registry_lock:
        if _tool_id is None or sys.monitoring.get_tool(_tool_id) != _MULTIPLEXER_TOOL_NAME:
            return None

        for tool_id in range(6):
            if tool_id != _tool_id and sys.monitoring.get_tool(tool_id) is not None:
                return None

        if _exclusive_cache_version != _registry_version:
            exclusive_handler_id: Optional[int] = None
            for code in _registry:
                handlers: Optional[_CodeHandlers] = _registry.get(code)
                if handlers is None:
                    continue
                for entry in handlers.snapshot:
                    entry_handler_id = id(entry.handler)
                    if exclusive_handler_id is None:
                        exclusive_handler_id = entry_handler_id
                    elif exclusive_handler_id != entry_handler_id:
                        exclusive_handler_id = -1
                        break
                if exclusive_handler_id == -1:
                    break
            _exclusive_handler_id = exclusive_handler_id
            _exclusive_cache_version = _registry_version

        if _exclusive_handler_id != id(handler):
            return None

        sys.monitoring.restart_events()
        return _registry_version


def registry_version_is_current(version: int) -> bool:
    """Return whether no local handler registration changed since *version*."""
    return version == _registry_version


def unregister(code: CodeType, handler: MonitoringEventHandler) -> None:
    """Remove *handler* from the handlers registered for *code*."""
    global _registry_version

    with _registry_lock:
        handlers: Optional[_CodeHandlers] = _registry.get(code)
        if handlers is None:
            return

        entry = handlers.pop_handler(id(handler))
        if entry is None:
            return
        _registry_version += 1

        if not handlers:
            del _registry[code]
            if _tool_id is not None:
                _set_local_events(_tool_id, code, 0)
        else:
            assert _tool_id is not None  # nosec
            local_events = _events_for(handlers) & _LOCAL_EVENTS
            handlers.disabled_events &= local_events
            _set_local_events(_tool_id, code, local_events)

        if _tool_id is not None:
            for event in _iter_events(entry.events):
                _recompute_event_callback(_tool_id, event)
