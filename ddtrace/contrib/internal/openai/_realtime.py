"""Instrumentation for the OpenAI Realtime API (bidirectional WebSocket event stream).

The Realtime API is not request/response, so it can't reuse the streaming path. Instead we wrap
the connection's send/parse_event/close methods (all typed sub-resource sends funnel through
RealtimeConnection.send, and recv/iteration/recv_bytes() all funnel through parse_event) and hand
each observed event to the session observer that a product (LLM Observability) attaches when the
connection opens. Nothing is observed when no product attaches one.
"""

import importlib
from types import ModuleType
from typing import Any
from typing import Callable
import weakref

from ddtrace.contrib._events.openai_realtime import OpenAIRealtimeSessionEvent
from ddtrace.contrib._events.openai_realtime import RealtimeSessionObserver
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import deep_getattr


log = get_logger(__name__)

# Realtime SDK classes have lived in two locations across versions; wrap whichever exist.
_REALTIME_MODULE_PATHS = (
    "openai.resources.realtime.realtime",
    "openai.resources.beta.realtime.realtime",
)


# -- wrappers ---------------------------------------------------------------


def patched_connect(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    manager = func(*args, **kwargs)
    if not core.has_listeners(OpenAIRealtimeSessionEvent.event_name):
        return manager
    try:
        manager._dd_client = getattr(instance, "_client", None)
        model = kwargs.get("model")
        manager._dd_model = model if isinstance(model, str) else None
    except Exception:
        log.debug("error annotating realtime connection manager", exc_info=True)
    return manager


def _finish_session_on_gc(state: RealtimeSessionObserver) -> None:
    """Last-resort finalizer: submit whatever the session still holds when the connection is dropped.

    Runs from a `weakref.finalize` callback (garbage collection, or interpreter exit), so it must
    never raise - by then there is no caller left to handle it.
    """
    try:
        state.finish_session()
    except Exception:
        log.debug("error finalizing realtime session on connection drop", exc_info=True)


def _attach_session(instance: Any, connection: Any) -> None:
    if not core.has_listeners(OpenAIRealtimeSessionEvent.event_name):
        return
    try:
        event = OpenAIRealtimeSessionEvent(
            client=getattr(instance, "_dd_client", None), model=getattr(instance, "_dd_model", None)
        )
        core.dispatch_event(event)
        state = event.observer
        if state is None:
            return
        connection._dd_realtime_state = state
        # An observer may hold events until a later one, or until `finish_session`, so a caller that
        # drops the connection without `close()` - and without `recv()` raising ConnectionClosed - would
        # lose them entirely. `with` blocks are already covered (the SDK's `__exit__` closes), so this
        # catches the un-managed case and process exit. `finish_session` must be idempotent, so it is
        # safe alongside the close paths. The observer must not reference the connection, so this
        # creates no cycle that would keep the connection alive.
        weakref.finalize(connection, _finish_session_on_gc, state)
    except Exception:
        log.debug("error starting realtime session observer", exc_info=True)


def patched_enter(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    connection = func(*args, **kwargs)
    _attach_session(instance, connection)
    return connection


async def patched_async_enter(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    connection = await func(*args, **kwargs)
    _attach_session(instance, connection)
    return connection


def patched_parse_event(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    # `parse_event` is the single sync observation point for server events: `recv()`,
    # connection iteration, and the manual `recv_bytes()` + `parse_event()` path all funnel
    # through it (it is synchronous on both the sync and async connection classes).
    event = func(*args, **kwargs)
    state = getattr(instance, "_dd_realtime_state", None)
    if state is not None:
        state.on_server_event(event)
    return event


def patched_send(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    # Record the client event only after the send succeeds, so a failed send doesn't attribute
    # unsent audio/text to the next turn.
    result = func(*args, **kwargs)
    state = getattr(instance, "_dd_realtime_state", None)
    if state is not None:
        state.on_client_event(args[0] if args else kwargs.get("event"))
    return result


async def patched_async_send(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    result = await func(*args, **kwargs)
    state = getattr(instance, "_dd_realtime_state", None)
    if state is not None:
        state.on_client_event(args[0] if args else kwargs.get("event"))
    return result


def patched_close(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    try:
        return func(*args, **kwargs)
    finally:
        state = getattr(instance, "_dd_realtime_state", None)
        if state is not None:
            state.finish_session()


async def patched_async_close(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    try:
        return await func(*args, **kwargs)
    finally:
        state = getattr(instance, "_dd_realtime_state", None)
        if state is not None:
            state.finish_session()


def _is_connection_closed(exc: BaseException) -> bool:
    # Match by class name to avoid importing/handling the optional `websockets` dependency here.
    return "ConnectionClosed" in type(exc).__name__


def patched_recv(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    # Server events are observed via parse_event; here we only need to catch the connection closing
    # (which raises out of recv) so the session is finalized even when the caller iterates/recvs
    # without using `with`/`close()`. finish_session is idempotent, so this is safe alongside close.
    try:
        return func(*args, **kwargs)
    except BaseException as exc:
        if _is_connection_closed(exc):
            state = getattr(instance, "_dd_realtime_state", None)
            if state is not None:
                state.finish_session()
        raise


async def patched_async_recv(
    func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    try:
        return await func(*args, **kwargs)
    except BaseException as exc:
        if _is_connection_closed(exc):
            state = getattr(instance, "_dd_realtime_state", None)
            if state is not None:
                state.finish_session()
        raise


# (class_name, method_name, wrapper)
_REALTIME_WRAPS = (
    ("Realtime", "connect", patched_connect),
    ("AsyncRealtime", "connect", patched_connect),
    ("RealtimeConnectionManager", "__enter__", patched_enter),
    ("RealtimeConnectionManager", "enter", patched_enter),
    ("AsyncRealtimeConnectionManager", "__aenter__", patched_async_enter),
    ("AsyncRealtimeConnectionManager", "enter", patched_async_enter),
    ("RealtimeConnection", "parse_event", patched_parse_event),
    ("RealtimeConnection", "send", patched_send),
    ("RealtimeConnection", "recv", patched_recv),
    ("RealtimeConnection", "close", patched_close),
    ("AsyncRealtimeConnection", "parse_event", patched_parse_event),
    ("AsyncRealtimeConnection", "send", patched_async_send),
    ("AsyncRealtimeConnection", "recv", patched_async_recv),
    ("AsyncRealtimeConnection", "close", patched_async_close),
)


def _realtime_modules() -> list[ModuleType]:
    modules: list[ModuleType] = []
    for path in _REALTIME_MODULE_PATHS:
        try:
            modules.append(importlib.import_module(path))
        except ImportError:
            continue
    return modules


def patch_realtime() -> None:
    # Realtime is a large, event-driven wrapping surface; allow disabling just it (while keeping the
    # rest of the OpenAI integration) via DD_OPENAI_REALTIME_ENABLED=false.
    if not asbool(env.get("DD_OPENAI_REALTIME_ENABLED", "true")):
        return
    for module in _realtime_modules():
        for class_name, method_name, wrapper in _REALTIME_WRAPS:
            cls = getattr(module, class_name, None)
            if cls is None or not hasattr(cls, method_name):
                continue
            try:
                wrap(module, f"{class_name}.{method_name}", wrapper)
            except Exception:
                log.debug("failed to wrap realtime %s.%s", class_name, method_name, exc_info=True)


def unpatch_realtime() -> None:
    for module in _realtime_modules():
        for class_name, method_name, _ in _REALTIME_WRAPS:
            cls = getattr(module, class_name, None)
            if cls is None:
                continue
            method = deep_getattr(cls, method_name)
            if method is not None and hasattr(method, "__wrapped__"):
                try:
                    unwrap(cls, method_name)
                except Exception:
                    log.debug("failed to unwrap realtime %s.%s", class_name, method_name, exc_info=True)
