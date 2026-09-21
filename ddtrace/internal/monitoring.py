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
from typing import NamedTuple
from typing import Optional
import weakref

from ddtrace.internal.compat import is_at_least_py
from ddtrace.internal.logger import get_logger
from ddtrace.internal.threads import Lock


if not is_at_least_py(3, 12):
    raise ImportError("ddtrace.internal.monitoring requires Python 3.12+")

log = get_logger(__name__)

_sys_monitoring: Any = sys.monitoring  # type: ignore[attr-defined]
_E: Any = _sys_monitoring.events
DISABLE: object = _sys_monitoring.DISABLE

# PY_UNWIND became a per-code "other" event only in Python 3.15 (its event bit even
# moved, 0x1000 -> 0x2000). On 3.12-3.14 it is a global-only event that
# set_local_events() rejects. The only PY_UNWIND consumer is itself 3.15+-gated.
if is_at_least_py(3, 15):
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
# slots for custom tools (see CPython docs). Prefer 4 and fall back to 3 if
# another tool claimed it. COVERAGE_ID (1) is deliberately never claimed, so
# ddtrace cannot lock coverage.py out of its conventional slot.
_CANDIDATE_TOOL_IDS = (4, 3)

_tool_id: Optional[int] = None
_tool_lock = Lock()

_registry_lock = Lock()


class IdentityWeakKeyDictionary:
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


_registry: IdentityWeakKeyDictionary = IdentityWeakKeyDictionary()

# id(handler) -> (weak reference to the handler, number of code objects it is registered for).
_subscribers: "dict[int, tuple[weakref.ReferenceType[MonitoringEventHandler], int]]" = {}
# Bumped only when the set of distinct subscribers changes, so it stays stable while one
# subscriber keeps registering more code objects. Callers hold on to a value to detect
# whether their view of the subscriber set is still accurate.
_subscriber_version: int = 0
_sole_subscriber_cache_version: int = -1
_sole_subscriber_cache_handler: "Optional[weakref.ReferenceType[MonitoringEventHandler]]" = None


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

    def set_handler(self, handler_id: int, entry: _Entry) -> Optional[_Entry]:
        previous = self._by_handler.get(handler_id)
        self._by_handler[handler_id] = entry
        self.snapshot = tuple(self._by_handler.values())
        return previous

    def pop_handler(self, handler_id: int) -> Optional[_Entry]:
        entry = self._by_handler.pop(handler_id, None)
        self.snapshot = tuple(self._by_handler.values())
        return entry


def _events_for(handlers: _CodeHandlers) -> int:
    events: int = 0
    for e in handlers.snapshot:
        events |= e.events
    return events


def _add_subscriber(handler: MonitoringEventHandler) -> None:
    """Record one more code object registered for handler. Caller holds _registry_lock."""
    global _subscriber_version

    handler_id = id(handler)
    existing = _subscribers.get(handler_id)
    # A dead entry means CPython reused the id of a collected handler, so this is a
    # different subscriber and must not inherit the old one's count.
    if existing is not None and existing[0]() is handler:
        ref, count = existing
        _subscribers[handler_id] = (ref, count + 1)
        return

    _subscribers[handler_id] = (weakref.ref(handler), 1)
    _subscriber_version += 1


def _remove_subscriber(handler: MonitoringEventHandler) -> None:
    """Drop one code object registered for handler. Caller holds _registry_lock."""
    global _subscriber_version

    handler_id = id(handler)
    existing = _subscribers.get(handler_id)
    if existing is None:
        return

    ref, count = existing
    if count > 1:
        _subscribers[handler_id] = (ref, count - 1)
        return

    del _subscribers[handler_id]
    _subscriber_version += 1


def _scan_sole_subscriber() -> Optional[MonitoringEventHandler]:
    """Walk the live registry and return its only subscriber, or None. Holds _registry_lock."""
    sole: Optional[MonitoringEventHandler] = None
    for code in _registry:
        handlers: Optional[_CodeHandlers] = _registry.get(code)
        if handlers is None:
            continue
        for entry in handlers.snapshot:
            if sole is None:
                sole = entry.handler
            elif entry.handler is not sole:
                return None
    return sole


def _sole_subscriber() -> Optional[MonitoringEventHandler]:
    """Return the only subscriber registered with the multiplexer, or None.

    _subscribers over-counts when a code object is garbage collected, because the weak
    registry entry disappears without a matching unregister(). Over-counting can hide a
    sole subscriber but can never invent one, so a single recorded handler is
    authoritative and anything else falls back to a walk of the live registry. That walk
    is O(registered code objects), so its result is cached against _subscriber_version --
    which, unlike the registration count, does not change as one subscriber instruments
    more modules.

    Caller holds _registry_lock.
    """
    global _sole_subscriber_cache_handler
    global _sole_subscriber_cache_version

    recorded: Optional[MonitoringEventHandler] = None
    several: bool = False
    for ref, _count in _subscribers.values():
        handler = ref()
        if handler is None:
            continue
        if recorded is None:
            recorded = handler
        elif handler is not recorded:
            several = True
            break
    if not several:
        return recorded

    if _sole_subscriber_cache_version != _subscriber_version:
        scanned = _scan_sole_subscriber()
        _sole_subscriber_cache_handler = weakref.ref(scanned) if scanned is not None else None
        _sole_subscriber_cache_version = _subscriber_version
    return _sole_subscriber_cache_handler() if _sole_subscriber_cache_handler is not None else None


def _setup() -> int:
    """Claim a free tool ID and install the global callbacks (idempotent)."""
    global _tool_id

    if _tool_id is not None:
        return _tool_id

    with _tool_lock:
        if _tool_id is not None:
            return _tool_id

        for tid in _CANDIDATE_TOOL_IDS:
            existing = _sys_monitoring.get_tool(tid)
            if existing is not None and existing != _MULTIPLEXER_TOOL_NAME:
                continue
            try:
                if existing is None:
                    _sys_monitoring.use_tool_id(tid, _MULTIPLEXER_TOOL_NAME)
                _tool_id = tid
                break
            except ValueError:
                continue
        else:
            raise MonitoringToolUnavailable("No free sys.monitoring tool ID available for ddtrace")

        _sys_monitoring.register_callback(_tool_id, _E.PY_START, _on_py_start)
        _sys_monitoring.register_callback(_tool_id, _E.PY_RETURN, _on_py_return)
        if _SUPPORTS_LOCAL_PY_UNWIND:
            _sys_monitoring.register_callback(_tool_id, _E.PY_UNWIND, _on_py_unwind)
        _sys_monitoring.register_callback(_tool_id, _E.LINE, _on_py_line)

    return _tool_id


# ---------------------------------------------------------------------------
# Hot-path callbacks — no lock; iterate a pre-built handler snapshot tuple
# ---------------------------------------------------------------------------


def _on_py_start(code: CodeType, instruction_offset: int) -> Optional[object]:
    handlers: Optional[_CodeHandlers] = _registry.get(code)
    if not handlers or not handlers.snapshot:
        return DISABLE
    # Deliberately uncaught: see the propagation warning on MonitoringEventHandler.
    # DISABLE is forwarded only when every PY_START handler for this code object
    # returns it, mirroring on_py_line. Existing handlers (the wrapping context)
    # return None, so behaviour is unchanged unless a handler opts into DISABLE.
    disable: bool = True
    for e in handlers.snapshot:
        if e.events & _E.PY_START:
            if e.handler.on_py_start(code, instruction_offset) is not DISABLE:
                disable = False
    if disable:
        handlers.disabled_events |= _E.PY_START
        return DISABLE
    return None


def _on_py_return(code: CodeType, instruction_offset: int, retval: object) -> Optional[object]:
    handlers: Optional[_CodeHandlers] = _registry.get(code)
    if not handlers or not handlers.snapshot:
        return DISABLE
    # Deliberately uncaught: see the propagation warning on MonitoringEventHandler.
    for e in handlers.snapshot:
        if e.events & _E.PY_RETURN:
            e.handler.on_py_return(code, instruction_offset, retval)
    return None


def _on_py_unwind(code: CodeType, instruction_offset: int, exception: BaseException) -> Optional[object]:
    handlers: Optional[_CodeHandlers] = _registry.get(code)
    if not handlers or not handlers.snapshot:
        return DISABLE
    # Deliberately uncaught: see the propagation warning on MonitoringEventHandler.
    for e in handlers.snapshot:
        if e.events & _E.PY_UNWIND:
            e.handler.on_py_unwind(code, instruction_offset, exception)
    return None


def _on_py_line(code: CodeType, line_number: int) -> Optional[object]:
    handlers: Optional[_CodeHandlers] = _registry.get(code)
    if not handlers or not handlers.snapshot:
        return DISABLE
    disable: bool = True
    for e in handlers.snapshot:
        if e.events & _E.LINE:
            try:
                if e.handler.on_py_line(code, line_number) is not DISABLE:
                    disable = False
            except Exception:
                log.warning("monitoring LINE handler failed", exc_info=True)
                disable = False
    if disable:
        handlers.disabled_events |= _E.LINE
        return DISABLE
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _set_local_events(tool_id: int, code: CodeType, events: int) -> None:
    _sys_monitoring.set_local_events(tool_id, code, events)


def _rearm_local_events(tool_id: int, code: CodeType, events: int, rearm_events: int) -> None:
    # A DISABLE return is sticky until the monitored event set changes or
    # restart_events() is called. Re-applying the same local events does not
    # clear it. Toggle only the requested event bits so unrelated lifecycle
    # events remain enabled throughout the re-arm operation.
    _set_local_events(tool_id, code, events & ~rearm_events)
    _set_local_events(tool_id, code, events)


def ensure_tool() -> int:
    """Claim the shared tool ID or raise MonitoringToolUnavailable."""
    return _setup()


def register(code: CodeType, handler: MonitoringEventHandler) -> None:
    """Register a monitoring event handler for *code*.

    The handler instance itself is the registration key; pass the same object
    to :func:`unregister` to remove it.
    """
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
        previous = handlers.set_handler(id(handler), entry)
        if previous is None:
            _add_subscriber(handler)
        local_events: int = _events_for(handlers) & _LOCAL_EVENTS
        handlers.disabled_events &= local_events

        rearm_events = handler_events & existing_events & handlers.disabled_events
        if rearm_events:
            _rearm_local_events(tool_id, code, local_events, rearm_events)
        else:
            _set_local_events(tool_id, code, local_events)


def refresh(code: CodeType, events: int) -> None:
    """Re-arm disabled local *events* for *code* without changing unrelated events."""
    with _registry_lock:
        handlers: Optional[_CodeHandlers] = _registry.get(code)
        if handlers and _tool_id is not None:
            local_events: int = _events_for(handlers) & _LOCAL_EVENTS
            rearm_events = events & local_events & handlers.disabled_events
            if rearm_events:
                _rearm_local_events(_tool_id, code, local_events, rearm_events)


def restart_events(handler: MonitoringEventHandler, *, force: bool = False) -> Optional[int]:
    """Restart events and return the subscriber version on success.

    By default, handler must be the sole ddtrace subscriber and no external tool
    may be visible. force bypasses those safeguards. The handler argument is an
    ownership check only; the underlying sys.monitoring restart remains global.
    """
    with _registry_lock:
        if not force:
            if _sole_subscriber() is not handler or _tool_id is None:
                return None
            for tool_id in range(6):
                if tool_id != _tool_id and _sys_monitoring.get_tool(tool_id) is not None:
                    return None

        # AIDEV-NOTE: The external-tool scan and restart are not atomic because
        # sys.monitoring exposes no shared lock or tool-scoped restart. This narrow
        # registration race is intentional parity with the previous coverage
        # implementation; visible external tools always use selective re-arming.
        _sys_monitoring.restart_events()
        return _subscriber_version


def subscriber_version_is_current(version: int) -> bool:
    """Return whether the set of registered subscribers still matches version."""
    return version == _subscriber_version


def unregister(code: CodeType, handler: MonitoringEventHandler) -> None:
    """Remove *handler* from the handlers registered for *code*."""
    with _registry_lock:
        handlers: Optional[_CodeHandlers] = _registry.get(code)
        if handlers is None:
            return

        entry = handlers.pop_handler(id(handler))
        if entry is None:
            return
        _remove_subscriber(handler)

        if not handlers:
            del _registry[code]
            if _tool_id is not None:
                _set_local_events(_tool_id, code, 0)
        else:
            assert _tool_id is not None  # nosec
            local_events = _events_for(handlers) & _LOCAL_EVENTS
            handlers.disabled_events &= local_events
            _set_local_events(_tool_id, code, local_events)
