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
from contextlib import contextmanager
import sys
from types import CodeType
from typing import Any
from typing import Callable
from typing import Iterable
from typing import Iterator
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
_DISABLE: object = _sys_monitoring.DISABLE

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
# for debugger/coverage/profiler/optimizer. Use only custom slot 3 until handled-
# exception ownership is finalized in the follow-up migration.
_CANDIDATE_TOOL_IDS = (3,)

_tool_id: Optional[int] = None
_tool_lock = Lock()

_registry_lock = Lock()
_pending_registrations: int = 0


class _IdentityWeakKeyDictionary:
    """Weak mapping keyed by object identity (not equality).

    Unlike ``weakref.WeakKeyDictionary``, lookups use ``is`` rather than
    ``CodeType.__eq__``, so distinct code objects for the same source remain
    separate entries.
    """

    # NOTE: CodeType equality is structural. Keep identity semantics here and in
    # every code-object registry built on this class, or separately compiled/reloaded
    # copies of the same code will overwrite each other.

    __slots__ = ("_data", "_on_remove", "_refs")

    def __init__(self, on_remove: Optional[Callable[[], None]] = None) -> None:
        # A live key makes id reuse impossible, while the weakref callback removes
        # the value before a dead key's address can be reused. Keeping values and
        # weakrefs separate therefore gives hot paths one built-in dict lookup.
        self._data: dict[int, Any] = {}
        self._refs: dict[int, weakref.ref[Any]] = {}
        self._on_remove = on_remove

    def _make_remove(self, key_id: int) -> Any:
        def remove(ref: weakref.ref[Any]) -> None:
            # Do not let an obsolete callback remove a replacement entry.
            if self._refs.get(key_id) is ref:
                self._refs.pop(key_id, None)
                self._data.pop(key_id, None)
                if self._on_remove is not None:
                    self._on_remove()

        return remove

    def get(self, key: CodeType, default: Any = None) -> Any:
        return self._data.get(id(key), default)

    def __contains__(self, key: CodeType) -> bool:
        return id(key) in self._data

    def __iter__(self) -> Any:
        for ref in tuple(self._refs.values()):
            key = ref()
            if key is not None:
                yield key

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, key: CodeType) -> Any:
        return self._data[id(key)]

    def __setitem__(self, key: CodeType, value: Any) -> None:
        key_id = id(key)
        self._data[key_id] = value
        self._refs[key_id] = weakref.ref(key, self._make_remove(key_id))

    def __delitem__(self, key: CodeType) -> None:
        key_id = id(key)
        if key_id not in self._data:
            raise KeyError(key)
        del self._data[key_id]
        del self._refs[key_id]

    def pop(self, key: CodeType, *default: Any) -> Any:
        key_id = id(key)
        try:
            value = self._data.pop(key_id)
        except KeyError:
            if default:
                return default[0]
            raise
        del self._refs[key_id]
        return value

    def clear(self) -> None:
        self._data.clear()
        self._refs.clear()


def _on_code_registration_collected() -> None:
    """Prune collected registrations and release the final tool ownership."""
    with _registry_lock:
        _prune_subscribers()
        _release_tool_if_unused()


_registry: _IdentityWeakKeyDictionary = _IdentityWeakKeyDictionary(_on_code_registration_collected)
_registry_get = _registry._data.get


class _SubscriberToken:
    """Identity token invalidated whenever the distinct subscriber set changes."""

    __slots__ = ("valid",)

    def __init__(self) -> None:
        self.valid: bool = True


_subscriber_token = _SubscriberToken()


def _invalidate_subscriber_token() -> None:
    global _subscriber_token

    _subscriber_token.valid = False
    _subscriber_token = _SubscriberToken()


# NOTE: Event mutations use an odd epoch while they are in progress and an even
# epoch when stable. Aggregate callbacks may return DISABLE only when their
# original even epoch is still current, so stale results cannot cross a global
# restart, selective refresh, registration, or unregistration.
_event_mutation_epoch: int = 0


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


class _Subscriber(NamedTuple):
    handler_ref: weakref.ReferenceType[MonitoringEventHandler]
    codes: _IdentityWeakKeyDictionary


# NOTE: Subscriber tokens track distinct subscriber identities, not per-code
# registrations. Coverage adds many code objects for one handler between restarts;
# invalidating for each object would make ownership checks quadratic. The code sets
# are weak for the same reason as _registry.
_subscriber_codes: dict[int, _Subscriber] = {}


class _CodeHandlers:
    """Per-code handler table with bound callbacks partitioned by event."""

    __slots__ = (
        "_by_handler",
        "line_possibly_disabled",
        "py_start_possibly_disabled",
        "start_callbacks",
        "return_callbacks",
        "unwind_callbacks",
        "line_callbacks",
    )

    def __init__(self) -> None:
        self._by_handler: dict[int, _Entry] = {}
        # Event bits for which the aggregate callback has returned DISABLE at
        # least once. This is intentionally conservative: stale bits can cause
        # an unnecessary targeted re-arm, while missing a bit can lose events.
        # Separate flags avoid lost read-modify-write updates when different
        # event callbacks run concurrently on free-threaded Python.
        self.py_start_possibly_disabled: bool = False
        self.line_possibly_disabled: bool = False
        self.start_callbacks: tuple[Callable[[CodeType, int], Optional[object]], ...] = ()
        self.return_callbacks: tuple[Callable[[CodeType, int, object], None], ...] = ()
        self.unwind_callbacks: tuple[Callable[[CodeType, int, BaseException], None], ...] = ()
        self.line_callbacks: tuple[Callable[[CodeType, int], Optional[object]], ...] = ()

    def __len__(self) -> int:
        return len(self._by_handler)

    def set_handler(self, handler_id: int, entry: _Entry) -> Optional[_Entry]:
        previous = self._by_handler.get(handler_id)
        self._by_handler[handler_id] = entry
        self._update_callbacks()
        return previous

    def pop_handler(self, handler_id: int) -> Optional[_Entry]:
        entry = self._by_handler.pop(handler_id, None)
        self._update_callbacks()
        return entry

    def _update_callbacks(self) -> None:
        # Bind and filter at registration time, not on every delivered event.
        # Each immutable tuple also preserves the in-flight dispatch snapshot.
        start_callbacks: list[Callable[[CodeType, int], Optional[object]]] = []
        return_callbacks: list[Callable[[CodeType, int, object], None]] = []
        unwind_callbacks: list[Callable[[CodeType, int, BaseException], None]] = []
        line_callbacks: list[Callable[[CodeType, int], Optional[object]]] = []
        for entry in self._by_handler.values():
            if entry.events & _E.PY_START:
                start_callbacks.append(entry.handler.on_py_start)
            if entry.events & _E.PY_RETURN:
                return_callbacks.append(entry.handler.on_py_return)
            if entry.events & _E.PY_UNWIND:
                unwind_callbacks.append(entry.handler.on_py_unwind)
            if entry.events & _E.LINE:
                line_callbacks.append(entry.handler.on_py_line)
        self.start_callbacks = tuple(start_callbacks)
        self.return_callbacks = tuple(return_callbacks)
        self.unwind_callbacks = tuple(unwind_callbacks)
        self.line_callbacks = tuple(line_callbacks)


def _events_for(handlers: _CodeHandlers) -> int:
    events: int = 0
    for e in handlers._by_handler.values():
        events |= e.events
    return events


def _possibly_disabled_events(handlers: _CodeHandlers) -> int:
    events = 0
    if handlers.py_start_possibly_disabled:
        events |= _E.PY_START
    if handlers.line_possibly_disabled:
        events |= _E.LINE
    return events


def _retain_possibly_disabled_events(handlers: _CodeHandlers, events: int) -> None:
    if not events & _E.PY_START:
        handlers.py_start_possibly_disabled = False
    if not events & _E.LINE:
        handlers.line_possibly_disabled = False


def _prune_subscribers() -> None:
    """Remove subscriber identities that no longer have live code registrations."""
    stale = [
        handler_id
        for handler_id, subscriber in _subscriber_codes.items()
        if subscriber.handler_ref() is None or not len(subscriber.codes)
    ]
    if stale:
        for handler_id in stale:
            del _subscriber_codes[handler_id]
        _invalidate_subscriber_token()


def _add_subscriber_code(code: CodeType, handler: MonitoringEventHandler) -> None:
    """Record a new code registration, invalidating only for a new subscriber."""
    handler_id = id(handler)
    subscriber = _subscriber_codes.get(handler_id)
    if subscriber is None or subscriber.handler_ref() is not handler:
        codes = _IdentityWeakKeyDictionary(_on_code_registration_collected)
        _subscriber_codes[handler_id] = _Subscriber(weakref.ref(handler), codes)
        _invalidate_subscriber_token()
    else:
        codes = subscriber.codes
    codes[code] = None


def _remove_subscriber_code(code: CodeType, handler: MonitoringEventHandler) -> None:
    """Remove a code registration, invalidating when its subscriber disappears."""
    subscriber = _subscriber_codes.get(id(handler))
    if subscriber is None or subscriber.handler_ref() is not handler:
        return
    codes = subscriber.codes
    codes.pop(code, None)
    if not len(codes):
        del _subscriber_codes[id(handler)]
        _invalidate_subscriber_token()


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


def _release_tool_if_unused() -> None:
    """Release the tool after the final registration; caller holds _registry_lock."""
    global _tool_id

    if _tool_id is None or _pending_registrations or len(_registry):
        return

    _prune_subscribers()
    with _tool_lock:
        if _tool_id is None or _pending_registrations or len(_registry):
            return
        tool_id = _tool_id
        _sys_monitoring.set_events(tool_id, 0)
        _sys_monitoring.register_callback(tool_id, _E.PY_START, None)
        _sys_monitoring.register_callback(tool_id, _E.PY_RETURN, None)
        if _SUPPORTS_LOCAL_PY_UNWIND:
            _sys_monitoring.register_callback(tool_id, _E.PY_UNWIND, None)
        _sys_monitoring.register_callback(tool_id, _E.LINE, None)
        _sys_monitoring.free_tool_id(tool_id)
        _tool_id = None


@contextmanager
def _reserve_tool_id() -> Iterator[int]:
    """Keep the shared tool claimed while a caller prepares its registration."""
    global _pending_registrations

    with _registry_lock:
        tool_id = _setup()
        _pending_registrations += 1
    try:
        yield tool_id
    finally:
        with _registry_lock:
            _pending_registrations -= 1
            _release_tool_if_unused()


def get_tool_id() -> int:
    """Return the shared tool ID, claiming it if necessary."""
    with _registry_lock:
        return _setup()


# ---------------------------------------------------------------------------
# Hot-path callbacks — no lock; iterate pre-built per-event callback tuples
# ---------------------------------------------------------------------------


def _on_py_start(code: CodeType, instruction_offset: int) -> Optional[object]:
    handlers: Optional[_CodeHandlers] = _registry_get(id(code))
    if handlers is None:
        return _DISABLE
    epoch = _event_mutation_epoch
    # Deliberately uncaught: see the propagation warning on MonitoringEventHandler.
    # DISABLE is forwarded only when every PY_START handler for this code object
    # returns it, mirroring on_py_line. Existing handlers (the wrapping context)
    # return None, so behaviour is unchanged unless a handler opts into DISABLE.
    disable: bool = True
    for callback in handlers.start_callbacks:
        if callback(code, instruction_offset) is not _DISABLE:
            disable = False
    # Publish the possible DISABLE state before validating the vote. A concurrent
    # refresh that starts after publication can then observe and re-arm it. If the
    # epoch changed earlier, retain the conservative bit but reject the stale vote.
    if disable:
        handlers.py_start_possibly_disabled = True
        if epoch == _event_mutation_epoch and not (epoch & 1):
            return _DISABLE
    return None


def _on_py_return(code: CodeType, instruction_offset: int, retval: object) -> Optional[object]:
    handlers: Optional[_CodeHandlers] = _registry_get(id(code))
    if handlers is None:
        return _DISABLE
    # Deliberately uncaught: see the propagation warning on MonitoringEventHandler.
    for callback in handlers.return_callbacks:
        callback(code, instruction_offset, retval)
    return None


def _on_py_unwind(code: CodeType, instruction_offset: int, exception: BaseException) -> Optional[object]:
    handlers: Optional[_CodeHandlers] = _registry_get(id(code))
    if handlers is None:
        return _DISABLE
    # Deliberately uncaught: see the propagation warning on MonitoringEventHandler.
    for callback in handlers.unwind_callbacks:
        callback(code, instruction_offset, exception)
    return None


def _on_py_line(code: CodeType, line_number: int) -> Optional[object]:
    handlers: Optional[_CodeHandlers] = _registry_get(id(code))
    if handlers is None:
        return _DISABLE
    epoch = _event_mutation_epoch
    disable: bool = True
    for callback in handlers.line_callbacks:
        try:
            if callback(code, line_number) is not _DISABLE:
                disable = False
        except Exception:
            log.warning("monitoring LINE handler failed", exc_info=True)
            disable = False
    # Publish before validating so a concurrent refresh cannot miss a DISABLE
    # vote that is about to be returned. Stale conservative bits are harmless.
    if disable:
        handlers.line_possibly_disabled = True
        if epoch == _event_mutation_epoch and not (epoch & 1):
            return _DISABLE
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _set_local_events(tool_id: int, code: CodeType, events: int) -> None:
    _sys_monitoring.set_local_events(tool_id, code, events)


def _begin_event_mutation() -> None:
    """Mark event configuration unstable; callers hold _registry_lock and do not nest mutations."""
    global _event_mutation_epoch

    _event_mutation_epoch += 1


def _end_event_mutation() -> None:
    """Publish stable event configuration after the matching mutation completes."""
    global _event_mutation_epoch

    _event_mutation_epoch += 1


def _rearm_local_events(tool_id: int, code: CodeType, events: int, rearm_events: int) -> None:
    # A DISABLE return is sticky until the monitored event set changes or
    # restart_events() is called. Re-applying the same local events does not
    # clear it. Toggle only the requested event bits so unrelated lifecycle
    # events remain enabled throughout the re-arm operation. The caller keeps
    # the event mutation epoch unstable across inspection and this physical toggle.
    _set_local_events(tool_id, code, events & ~rearm_events)
    _set_local_events(tool_id, code, events)


def _handler_event_mask(handler: MonitoringEventHandler, events: Optional[int]) -> int:
    declared_events: int = _events_for_handler(handler) if events is None else events
    if (declared_events & _E.PY_UNWIND) and not _SUPPORTS_LOCAL_PY_UNWIND:
        raise RuntimeError("on_py_unwind handlers require Python 3.15+ (PY_UNWIND is a global-only event on 3.12-3.14)")
    handler_events: int = declared_events & _LOCAL_EVENTS
    if not handler_events:
        raise ValueError("Handler overrides no local MonitoringEventHandler methods")
    return handler_events


def _register_one(code: CodeType, handler: MonitoringEventHandler, entry: _Entry, tool_id: int) -> None:
    """Register one code while the registry lock and mutation barrier are held."""
    handler_events = entry.events
    handlers: Optional[_CodeHandlers] = _registry.get(code)
    if handlers is None:
        handlers = _CodeHandlers()
        handlers.set_handler(id(handler), entry)
        _registry[code] = handlers
        _add_subscriber_code(code, handler)
        _set_local_events(tool_id, code, handler_events)
        return

    # Events already provided by an existing handler may have been DISABLE'd by
    # that handler's callback return (LINE, or PY_START when a handler opts in).
    # If the new handler shares any of those events, re-arm via the tool-scoped
    # toggle so the new handler actually receives them.
    existing_events: int = _events_for(handlers)
    previous = handlers.set_handler(id(handler), entry)
    if previous is None:
        _add_subscriber_code(code, handler)
    local_events: int = _events_for(handlers) & _LOCAL_EVENTS
    _retain_possibly_disabled_events(handlers, local_events)

    rearm_events = handler_events & existing_events & _possibly_disabled_events(handlers)
    if rearm_events:
        _rearm_local_events(tool_id, code, local_events, rearm_events)
    else:
        _set_local_events(tool_id, code, local_events)


def register(code: CodeType, handler: MonitoringEventHandler, *, events: Optional[int] = None) -> None:
    """Register a monitoring event handler for code.

    The handler instance itself is the registration key; pass the same object
    to unregister to remove it. Internal callers that already know their event
    mask can pass events to avoid repeating handler introspection per code object.
    """
    handler_events = _handler_event_mask(handler, events)
    entry = _Entry(handler, handler_events)

    with _registry_lock:
        tool_id: int = _setup()
        _begin_event_mutation()
        try:
            _register_one(code, handler, entry, tool_id)
        finally:
            _end_event_mutation()


def _register_many(codes: Iterable[CodeType], handler: MonitoringEventHandler, *, events: int) -> None:
    """Register one handler for multiple codes under one mutation barrier."""
    # Materialize before taking the non-reentrant lock. Advancing a one-shot
    # iterable can release its previous code object and synchronously run the
    # registry's weakref cleanup callback, which acquires the same lock.
    code_batch = tuple(codes)
    if not code_batch:
        return
    handler_events = _handler_event_mask(handler, events)
    entry = _Entry(handler, handler_events)

    with _registry_lock:
        tool_id: int = _setup()
        _begin_event_mutation()
        try:
            for code in code_batch:
                _register_one(code, handler, entry, tool_id)
        finally:
            _end_event_mutation()


def refresh(code: CodeType, events: int) -> None:
    """Re-arm disabled local *events* for *code* without changing unrelated events."""
    with _registry_lock:
        handlers: Optional[_CodeHandlers] = _registry.get(code)
        if handlers is None or _tool_id is None:
            return
        local_events: int = _events_for(handlers) & _LOCAL_EVENTS
        requested_events = events & local_events
        if not requested_events:
            return

        _begin_event_mutation()
        try:
            rearm_events = requested_events & _possibly_disabled_events(handlers)
            if rearm_events:
                _rearm_local_events(_tool_id, code, local_events, rearm_events)
        finally:
            _end_event_mutation()


def restart_events(handler: MonitoringEventHandler) -> Optional[_SubscriberToken]:
    """Restart events when handler has exclusive ownership, returning its validity token."""
    with _registry_lock:
        _prune_subscribers()
        if len(_subscriber_codes) != 1 or _tool_id is None:
            return None
        subscriber = next(iter(_subscriber_codes.values()))
        if subscriber.handler_ref() is not handler:
            return None
        for tool_id in range(6):
            if tool_id != _tool_id and _sys_monitoring.get_tool(tool_id) is not None:
                return None

        # NOTE: The external-tool scan and restart are not atomic because
        # sys.monitoring exposes no shared lock or tool-scoped restart. This narrow
        # registration race is intentional parity with the previous coverage
        # implementation; visible external tools always use selective re-arming.
        _begin_event_mutation()
        try:
            _sys_monitoring.restart_events()
        finally:
            _end_event_mutation()
        return _subscriber_token


def subscriber_version_is_current(token: _SubscriberToken) -> bool:
    """Return whether token still represents the current local subscriber set.

    Weak-reference cleanup prunes dead registrations outside this hot-path
    check, while a new subscriber invalidates the token immediately.
    """
    return token.valid


def unregister(code: CodeType, handler: MonitoringEventHandler) -> None:
    """Remove *handler* from the handlers registered for *code*."""
    with _registry_lock:
        handlers: Optional[_CodeHandlers] = _registry.get(code)
        if handlers is None:
            return

        _begin_event_mutation()
        try:
            entry = handlers.pop_handler(id(handler))
            if entry is None:
                return
            _remove_subscriber_code(code, handler)

            if not handlers:
                del _registry[code]
                if _tool_id is not None:
                    _set_local_events(_tool_id, code, 0)
            else:
                assert _tool_id is not None  # nosec
                local_events = _events_for(handlers) & _LOCAL_EVENTS
                _retain_possibly_disabled_events(handlers, local_events)
                _set_local_events(_tool_id, code, local_events)
        finally:
            _end_event_mutation()
        _release_tool_if_unused()
