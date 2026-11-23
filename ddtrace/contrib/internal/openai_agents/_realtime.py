"""Observe the Agents SDK WebSocket boundary through a product-owned session observer."""

from dataclasses import dataclass
from types import ModuleType
from typing import Any
from typing import Callable
import weakref

import agents
from wrapt import FunctionWrapper

from ddtrace.contrib._events.openai_agents_realtime import AgentsRealtimeSessionObserver
from ddtrace.contrib._events.openai_agents_realtime import OpenAIAgentsRealtimeSessionEvent
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)
_MODULE = "agents.realtime.openai_realtime"
_MISSING = object()


def _snapshot_containers(value: Any, depth: int = 0) -> Any:
    # Audio/text strings are immutable and shared. Copy only small JSON containers
    # so awaited SDK sends cannot mutate the evidence retained by the observer.
    if depth > 8:
        return None
    if isinstance(value, dict):
        return {k: _snapshot_containers(v, depth + 1) for k, v in list(value.items())[:256]}
    if isinstance(value, list):
        return [_snapshot_containers(v, depth + 1) for v in value[:256]]
    return value if isinstance(value, (str, int, float, bool, type(None))) else None


async def _connect(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    previous = getattr(instance, "_dd_realtime_state", None)
    # Preserve the SDK's already-connected/connection-in-progress rejection.
    if getattr(instance, "_websocket", None) is not None or getattr(instance, "_connection_attempt_active", False):
        return await func(*args, **kwargs)
    if previous:
        _finish_session(previous)
    finalizer = getattr(instance, "_dd_realtime_finalizer", None)
    if finalizer is not None:
        finalizer.detach()
    state = None
    try:
        if asbool(env.get("DD_OPENAI_REALTIME_ENABLED", "true")):
            options = args[0] if args else kwargs.get("options", {})
            settings = options.get("initial_model_settings") or {}
            call_id = options.get("call_id")
            url = options.get("url") or (
                "wss://api.openai.com/v1/realtime?call_id=" + str(call_id)
                if call_id
                else "wss://api.openai.com/v1/realtime"
            )
            event = OpenAIAgentsRealtimeSessionEvent(instance, url, settings.get("model_name") or instance.model)
            core.dispatch_event(event)
            state = event.observer
            if state is not None:
                instance._dd_realtime_state = state
                instance._dd_realtime_finalizer = weakref.finalize(instance, _finish_session, state)
    except Exception:
        log.debug("failed to start Agents realtime observation", exc_info=True)
    try:
        return await func(*args, **kwargs)
    except BaseException:
        if state:
            _finish_session(state)
        raise


async def _server(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    state = getattr(instance, "_dd_realtime_state", None)
    if state is None or state._closed:
        return await func(*args, **kwargs)
    event = args[0] if args else kwargs.get("event")
    suppress = False
    state.handling += 1
    try:
        suppress = state.context.copy().run(state.observe_server, event)
    except Exception:
        log.debug("failed to observe Agents realtime event", exc_info=True)
    if suppress:
        state.handling -= 1
        _after_handler(state)
        return None
    try:
        return await func(*args, **kwargs)
    finally:
        state.handling -= 1
        _after_handler(state)


def _finish_session(state: AgentsRealtimeSessionObserver) -> None:
    try:
        state.context.copy().run(state.finish_session)
    except Exception:
        log.debug("failed to finalize Agents realtime observation", exc_info=True)


def _after_handler(state: AgentsRealtimeSessionObserver) -> None:
    try:
        state.context.copy().run(state.after_handler)
    except Exception:
        log.debug("failed to finish Agents realtime observation", exc_info=True)


async def _send(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    state = getattr(instance, "_dd_realtime_state", None)
    event = args[0] if args else kwargs.get("event")
    observed = None
    if state and not state._closed:
        try:
            message = getattr(event, "message", None)
            observed = (
                _snapshot_containers({"type": message["type"], **message.get("other_data", {})})
                if message is not None
                else event.model_dump(exclude_unset=True)
                if event is not None
                else None
            )
        except Exception:
            log.debug("failed to snapshot Agents realtime send", exc_info=True)
    finish = None
    if state and not state._closed:
        try:
            finish = state.prepare_client_event(observed)
        except Exception:
            log.debug("failed to prepare Agents realtime observation", exc_info=True)
    successful = False
    try:
        result = await func(*args, **kwargs)
        successful = result is not False
        return result
    finally:
        if finish is not None:
            try:
                finish(successful)
            except Exception:
                log.debug("failed to observe Agents realtime send", exc_info=True)


async def _listen(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    state = getattr(instance, "_dd_realtime_state", None)
    try:
        return await func(*args, **kwargs)
    finally:
        if state:
            _finish_session(state)


async def _close(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    state = getattr(instance, "_dd_realtime_state", None)
    socket = getattr(instance, "_websocket", None)
    try:
        return await func(*args, **kwargs)
    finally:
        # Before-teardown cancellation keeps the same socket and listener alive.
        # Later teardown resets the socket even if its close raised.
        if state and (socket is None or getattr(instance, "_websocket", None) is not socket):
            _finish_session(state)


_WRAPS = {
    "connect": _connect,
    "_handle_ws_event": _server,
    "_send_raw_message": _send,
    "_send_raw_message_if": _send,
    "_listen_for_messages": _listen,
    "close": _close,
}


@dataclass
class _WrappedMethod:
    owner: Any
    name: str
    original: Any
    wrapped_original: Any
    installed: Any = None
    active: bool = True

    def enabled(self) -> bool:
        return self.active


_wrapped_methods: list[_WrappedMethod] = []
_hook_registered = False


def _patch_module(module: ModuleType) -> None:
    if not getattr(agents, "_datadog_patch", False):
        return
    cls = getattr(module, "OpenAIRealtimeWebSocketModel", None)
    if cls is None:
        return
    for name, wrapper in _WRAPS.items():
        if not hasattr(cls, name) or any(record.owner is cls and record.name == name for record in _wrapped_methods):
            continue
        descriptor = next(base.__dict__[name] for base in cls.__mro__ if name in base.__dict__)
        record = _WrappedMethod(cls, name, cls.__dict__.get(name, _MISSING), descriptor)
        record.installed = FunctionWrapper(descriptor, wrapper, enabled=record.enabled)
        setattr(cls, name, record.installed)
        _wrapped_methods.append(record)


def patch_realtime() -> None:
    global _hook_registered
    if _hook_registered or not asbool(env.get("DD_OPENAI_REALTIME_ENABLED", "true")):
        return
    ModuleWatchdog.register_module_hook(_MODULE, _patch_module)
    _hook_registered = True


def unpatch_realtime() -> None:
    global _hook_registered
    if _hook_registered:
        ModuleWatchdog.unregister_module_hook(_MODULE, _patch_module)
        _hook_registered = False
    while _wrapped_methods:
        record = _wrapped_methods.pop()
        # A later application decorator may retain our proxy. Disable it without
        # removing that decorator or reactivating it on a subsequent patch cycle.
        record.active = False
        if record.owner.__dict__.get(record.name) is record.installed:
            current = record.installed.__wrapped__
            if record.original is _MISSING and current is record.wrapped_original:
                delattr(record.owner, record.name)
            else:
                # Another wrapper may have been removed from beneath ours.
                setattr(record.owner, record.name, current)
