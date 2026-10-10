from typing import Any
from typing import Callable
from typing import Optional

import elevenlabs
import elevenlabs.conversational_ai.conversation as conversation
from websockets.exceptions import ConnectionClosedOK
import wrapt

from ddtrace import config
from ddtrace.contrib.internal.elevenlabs._state import _CURRENT_STATE
from ddtrace.contrib.internal.elevenlabs._state import ConversationState
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.elevenlabs import ElevenLabsIntegration
from ddtrace.trace import tracer


log = get_logger(__name__)
config._add("elevenlabs", {})  # type: ignore[no-untyped-call]
_original_websockets = conversation.websockets


def get_version() -> str:
    return str(getattr(elevenlabs, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"elevenlabs": ">=2.70.0"}


def _observe(state: ConversationState, direction: str, raw: Any) -> None:
    try:
        if direction == "send":
            state.on_send(raw)
        else:
            state.on_receive(raw)
    except Exception:
        log.debug("Could not capture ElevenLabs conversation event", exc_info=True)


class _Socket(wrapt.ObjectProxy):  # type: ignore[misc]
    def __init__(self, socket: Any, state: ConversationState) -> None:
        super().__init__(socket)
        self._self_state = state

    def send(self, raw: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            result = self.__wrapped__.send(raw, *args, **kwargs)
        except Exception as exc:
            if not self._self_state.closing and not isinstance(exc, ConnectionClosedOK):
                self._self_state.error = True
            raise
        _observe(self._self_state, "send", raw)
        return result

    def recv(self, *args: Any, **kwargs: Any) -> Any:
        try:
            raw = self.__wrapped__.recv(*args, **kwargs)
        except TimeoutError:
            raise
        except Exception as exc:
            if not self._self_state.closing and not isinstance(exc, ConnectionClosedOK):
                self._self_state.error = True
            raise
        _observe(self._self_state, "receive", raw)
        return raw


class _AsyncSocket(wrapt.ObjectProxy):  # type: ignore[misc]
    def __init__(self, socket: Any, state: ConversationState) -> None:
        super().__init__(socket)
        self._self_state = state

    async def send(self, raw: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            result = await self.__wrapped__.send(raw, *args, **kwargs)
        except Exception as exc:
            if not self._self_state.closing and not isinstance(exc, ConnectionClosedOK):
                self._self_state.error = True
            raise
        _observe(self._self_state, "send", raw)
        return result

    async def recv(self, *args: Any, **kwargs: Any) -> Any:
        try:
            raw = await self.__wrapped__.recv(*args, **kwargs)
        except TimeoutError:
            raise
        except Exception as exc:
            if not self._self_state.closing and not isinstance(exc, ConnectionClosedOK):
                self._self_state.error = True
            raise
        _observe(self._self_state, "receive", raw)
        return raw


class _Connect(wrapt.ObjectProxy):  # type: ignore[misc]
    def __init__(self, connection: Any, state: ConversationState) -> None:
        super().__init__(connection)
        self._self_state = state

    def __enter__(self) -> _Socket:
        return _Socket(self.__wrapped__.__enter__(), self._self_state)

    def __exit__(self, *args: Any) -> Any:
        return self.__wrapped__.__exit__(*args)


class _AsyncConnect(wrapt.ObjectProxy):  # type: ignore[misc]
    def __init__(self, connection: Any, state: ConversationState) -> None:
        super().__init__(connection)
        self._self_state = state

    async def __aenter__(self) -> _AsyncSocket:
        return _AsyncSocket(await self.__wrapped__.__aenter__(), self._self_state)

    async def __aexit__(self, *args: Any) -> Any:
        return await self.__wrapped__.__aexit__(*args)


class _Websockets(wrapt.ObjectProxy):  # type: ignore[misc]
    def connect(self, *args: Any, **kwargs: Any) -> Any:
        connection = self.__wrapped__.connect(*args, **kwargs)
        state = _CURRENT_STATE.get()
        return _AsyncConnect(connection, state) if state is not None else connection


def traced_connect(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    connection = func(*args, **kwargs)
    state = _CURRENT_STATE.get()
    return _Connect(connection, state) if state is not None else connection


def _new_state(instance: Any) -> Optional[ConversationState]:
    try:
        integration = elevenlabs._datadog_integration
        if not integration.llmobs_enabled:
            return None
        existing = _state(instance)
        if existing is not None and not existing.closed:
            return existing
        state = ConversationState(integration, instance, parent=tracer.context_provider.active())
        instance._dd_elevenlabs_state = state
        return state
    except Exception:
        log.debug("Could not initialize ElevenLabs conversation tracing", exc_info=True)
        return None


def traced_start(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    _new_state(instance)
    return func(*args, **kwargs)


async def traced_async_start(
    func: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    _new_state(instance)
    return await func(*args, **kwargs)


def _state(instance: Any) -> Optional[ConversationState]:
    return getattr(instance, "_dd_elevenlabs_state", None)


def _finish(state: Optional[ConversationState]) -> None:
    if state is not None:
        try:
            state.close()
        except Exception:
            log.debug("Could not finalize ElevenLabs conversation", exc_info=True)


def traced_run(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    state = _state(instance)
    if state is None:
        return func(*args, **kwargs)
    token = _CURRENT_STATE.set(state)
    try:
        return func(*args, **kwargs)
    except Exception:
        state.error = not state.closing
        raise
    finally:
        _CURRENT_STATE.reset(token)
        _finish(state)


async def traced_async_run(
    func: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    state = _state(instance)
    if state is None:
        return await func(*args, **kwargs)
    token = _CURRENT_STATE.set(state)
    try:
        return await func(*args, **kwargs)
    except Exception:
        state.error = not state.closing
        raise
    finally:
        _CURRENT_STATE.reset(token)
        _finish(state)


def traced_end(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    state = _state(instance)
    if state is not None:
        state.closing = True
    return func(*args, **kwargs)


async def traced_async_end(
    func: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    state = _state(instance)
    if state is not None:
        state.closing = True
    return await func(*args, **kwargs)


async def traced_tool(
    func: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    state = _CURRENT_STATE.get()
    parameters = kwargs.get("parameters", args[1] if len(args) > 1 else {})
    name = kwargs.get("tool_name", args[0] if args else "")
    call_id = parameters.get("tool_call_id") if isinstance(parameters, dict) else None
    if state is None or not isinstance(call_id, str):
        return await func(*args, **kwargs)
    try:
        span = state.begin_tool(call_id, str(name), parameters)
    except Exception:
        log.debug("Could not start ElevenLabs client tool span", exc_info=True)
        span = None
    try:
        result = await func(*args, **kwargs)
    except Exception:
        if span is not None:
            try:
                state.end_tool(call_id, error=True)
            except Exception:
                log.debug("Could not finalize ElevenLabs client tool span", exc_info=True)
        raise
    else:
        if span is not None:
            try:
                state.end_tool(call_id, result)
            except Exception:
                log.debug("Could not finalize ElevenLabs client tool span", exc_info=True)
        return result


def patch() -> None:
    if getattr(elevenlabs, "_datadog_patch", False):
        return
    elevenlabs._datadog_patch = True
    elevenlabs._datadog_integration = ElevenLabsIntegration(config.elevenlabs)
    module = "elevenlabs.conversational_ai.conversation"
    for cls, async_mode in (("Conversation", False), ("AsyncConversation", True)):
        wrap(module, cls + ".start_session", traced_async_start if async_mode else traced_start)
        wrap(module, cls + "._run", traced_async_run if async_mode else traced_run)
        wrap(module, cls + ".end_session", traced_async_end if async_mode else traced_end)
    wrap(module, "ClientTools.handle", traced_tool)
    wrap(module, "connect", traced_connect)
    # Replace only the SDK's binding; the global websockets module is unchanged.
    conversation.websockets = _Websockets(_original_websockets)


def unpatch() -> None:
    if not getattr(elevenlabs, "_datadog_patch", False):
        return
    elevenlabs._datadog_patch = False
    for cls in (conversation.Conversation, conversation.AsyncConversation):
        for name in ("start_session", "_run", "end_session"):
            unwrap(cls, name)
    unwrap(conversation.ClientTools, "handle")
    unwrap(conversation, "connect")
    conversation.websockets = _original_websockets
