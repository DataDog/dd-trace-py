"""Instrumentation for the OpenAI Realtime API (bidirectional WebSocket event stream).

The Realtime API is not request/response, so it can't reuse the streaming path. Instead we wrap
the connection's ``send``/``recv``/``close`` methods (all typed sub-resource sends funnel through
``RealtimeConnection.send``, and iteration funnels through ``recv``) and feed each observed event
into a ``_RealtimeState`` machine:

- a long-lived **session span** (workflow) spanning connect -> close, tagged with session config.
- a per-turn **llm child span** started on ``response.created`` and finished on ``response.done``,
  carrying the user/assistant transcripts, audio, and token usage for that turn.

Realtime audio is raw PCM by default, which the UI can't render, so we keep the transcript as the
message content and only emit a playable ``audio_part`` for renderable formats (handled by the
shared audio helpers).
"""

import importlib
from types import SimpleNamespace
from typing import Any
from typing import Optional

import openai

from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import deep_getattr
from ddtrace.llmobs._constants import AUDIO_FALLBACK_MARKER
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.utils import concat_base64_audio
from ddtrace.llmobs._integrations.utils import format_audio_part_with_guard
from ddtrace.llmobs._integrations.utils import realtime_audio_format_to_mime
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs.types import Message


log = get_logger(__name__)

# Realtime SDK classes have lived in two locations across versions; wrap whichever exist.
_REALTIME_MODULE_PATHS = (
    "openai.resources.realtime.realtime",
    "openai.resources.beta.realtime.realtime",
)


def _event_type(event: Any) -> str:
    return str(_get_attr(event, "type", "") or "")


def _normalize_response_event_type(event_type: str) -> str:
    """Collapse SDK naming drift so ``response.output_audio.*``/``response.output_text.*`` match
    their older ``response.audio.*``/``response.text.*`` equivalents.
    """
    return (
        event_type.replace(".output_audio_transcript", ".audio_transcript")
        .replace(".output_audio", ".audio")
        .replace(".output_text", ".text")
    )


class _InputTurn:
    """Accumulated user input (audio chunks + transcript/text) for a single turn."""

    def __init__(self) -> None:
        self.audio_chunks: list[str] = []
        self.text: str = ""
        self.transcript: str = ""
        self.item_id: Optional[str] = None


class _ResponseTurn:
    """Accumulated assistant output for a single ``response.*`` lifecycle."""

    def __init__(self, input_turn: _InputTurn) -> None:
        self.input = input_turn
        self.audio_chunks: list[str] = []
        self.transcript: str = ""
        self.text: str = ""
        self.usage: Any = None
        self.model: Optional[str] = None
        self.status: Optional[str] = None
        self.span = None


class _RealtimeState:
    """Drives session + per-response LLMObs spans off the realtime event stream."""

    def __init__(self, integration: Any, session_span: Any, client: Any = None, model: Optional[str] = None) -> None:
        self._integration = integration
        self._session_span = session_span
        self._client = client
        self._model = model
        self._session_config: dict[str, Any] = {}
        self._input_audio_mime: str = ""
        self._output_audio_mime: str = ""
        self._pending_input = _InputTurn()
        self._responses: dict[str, Any] = {}
        self._input_transcripts: dict[str, str] = {}
        self._closed = False

    # -- event entry points -------------------------------------------------

    def on_client_event(self, event: Any) -> None:
        try:
            event_type = _event_type(event)
            if event_type == "session.update":
                self._update_session_config(_get_attr(event, "session", None))
            elif event_type == "input_audio_buffer.append":
                audio = _get_attr(event, "audio", None)
                if audio:
                    self._pending_input.audio_chunks.append(audio)
            elif event_type == "input_audio_buffer.clear":
                # Discarded input audio must not be attributed to the next response.
                self._pending_input.audio_chunks = []
            elif event_type == "conversation.item.create":
                self._absorb_input_item(_get_attr(event, "item", None))
        except Exception:
            log.debug("error handling realtime client event", exc_info=True)

    def on_server_event(self, event: Any) -> None:
        try:
            event_type = _event_type(event)
            if event_type in ("session.created", "session.updated"):
                self._update_session_config(_get_attr(event, "session", None))
                return
            if event_type == "input_audio_buffer.committed":
                self._pending_input.item_id = _get_attr(event, "item_id", None)
                return
            if event_type == "input_audio_buffer.cleared":
                self._pending_input.audio_chunks = []
                return
            if event_type == "conversation.item.input_audio_transcription.completed":
                item_id = _get_attr(event, "item_id", None)
                transcript = str(_get_attr(event, "transcript", "") or "")
                if item_id is not None:
                    self._input_transcripts[item_id] = transcript
                if self._pending_input.item_id == item_id and not self._pending_input.transcript:
                    self._pending_input.transcript = transcript
                return
            if event_type == "response.created":
                response = _get_attr(event, "response", None)
                self._start_response(_get_attr(response, "id", None) or _get_attr(event, "response_id", None))
                return
            if event_type == "response.done":
                response = _get_attr(event, "response", None)
                self._finish_response(
                    _get_attr(response, "id", None) or _get_attr(event, "response_id", None), response
                )
                return
            self._handle_response_delta(event, event_type)
        except Exception:
            log.debug("error handling realtime server event", exc_info=True)

    def _handle_response_delta(self, event: Any, event_type: str) -> None:
        normalized = _normalize_response_event_type(event_type)
        turn = self._responses.get(_get_attr(event, "response_id", None))
        if turn is None:
            return
        if normalized == "response.audio.delta":
            delta = _get_attr(event, "delta", None)
            if delta:
                turn.audio_chunks.append(delta)
        elif normalized == "response.audio_transcript.delta":
            turn.transcript += str(_get_attr(event, "delta", "") or "")
        elif normalized == "response.audio_transcript.done":
            turn.transcript = str(_get_attr(event, "transcript", turn.transcript) or "")
        elif normalized == "response.text.delta":
            turn.text += str(_get_attr(event, "delta", "") or "")
        elif normalized == "response.text.done":
            turn.text = str(_get_attr(event, "text", turn.text) or "")

    # -- span lifecycle -----------------------------------------------------

    def _start_response(self, response_id: Optional[str]) -> None:
        if response_id is None:
            return
        turn = _ResponseTurn(self._pending_input)
        self._pending_input = _InputTurn()
        turn.model = self._model
        try:
            turn.span = self._integration.trace(
                "createRealtimeResponse",
                instance=SimpleNamespace(_client=self._client),
                parent_context=self._session_span,
                activate=False,
            )
        except Exception:
            log.debug("error starting realtime response span", exc_info=True)
        self._responses[response_id] = turn

    def _finish_response(self, response_id: Optional[str], response: Any) -> None:
        if response_id is None:
            return
        turn = self._responses.pop(response_id, None)
        if turn is None:
            return
        turn.usage = _get_attr(response, "usage", None)
        turn.model = _get_attr(response, "model", None) or turn.model or self._model
        turn.status = _get_attr(response, "status", None)
        if not turn.input.transcript and turn.input.item_id is not None:
            turn.input.transcript = self._input_transcripts.pop(turn.input.item_id, "")
        if turn.span is not None:
            try:
                if turn.status == "failed":
                    turn.span.error = 1
                self._tag_response(turn)
            except Exception:
                log.debug("error tagging realtime response span", exc_info=True)
            finally:
                turn.span.finish()

    def finish_session(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Tag any in-flight turns (closed before ``response.done``) with whatever partial data we
        # have so they aren't submitted as empty llm spans.
        for turn in list(self._responses.values()):
            if turn.span is None:
                continue
            try:
                if not turn.input.transcript and turn.input.item_id is not None:
                    turn.input.transcript = self._input_transcripts.pop(turn.input.item_id, "")
                self._tag_response(turn)
            except Exception:
                log.debug("error tagging in-flight realtime response span", exc_info=True)
            finally:
                turn.span.finish()
        self._responses.clear()
        self._input_transcripts.clear()
        try:
            self._integration._llmobs_set_tags_from_realtime_session(
                self._session_span, self._model, self._session_metadata()
            )
        except Exception:
            log.debug("error tagging realtime session span", exc_info=True)
        self._session_span.finish()

    # -- tagging helpers ----------------------------------------------------

    def tag_session(self) -> None:
        try:
            self._integration._llmobs_set_tags_from_realtime_session(
                self._session_span, self._model, self._session_metadata()
            )
        except Exception:
            log.debug("error tagging realtime session span", exc_info=True)

    def _tag_response(self, turn: _ResponseTurn) -> None:
        input_message = self._build_message(
            "user", turn.input.transcript or turn.input.text, turn.input.audio_chunks, self._input_audio_mime
        )
        output_message = self._build_message(
            "assistant", turn.transcript or turn.text, turn.audio_chunks, self._output_audio_mime
        )
        self._integration._llmobs_set_tags_from_realtime_response(
            turn.span,
            turn.model,
            [input_message] if input_message else [],
            [output_message] if output_message else [],
            metadata={},
            metrics=_usage_metrics(turn.usage),
        )

    def _build_message(self, role: str, content: str, audio_chunks: list[str], mime_type: str) -> Optional[Message]:
        audio_part = None
        if audio_chunks:
            audio_part = format_audio_part_with_guard(concat_base64_audio(audio_chunks), mime_type)
        if not content and not audio_part and audio_chunks:
            # Audio was captured but isn't renderable (e.g. raw PCM) and there's no transcript;
            # surface a marker so the turn isn't silently empty.
            content = AUDIO_FALLBACK_MARKER
        if not content and not audio_part:
            return None
        message = Message(role=role, content=content or "")
        if audio_part:
            message["audio_parts"] = [audio_part]
        return message

    def _session_metadata(self) -> dict[str, Any]:
        return dict(self._session_config)

    # -- config extraction --------------------------------------------------

    def _update_session_config(self, session: Any) -> None:
        if session is None:
            return
        model = _get_attr(session, "model", None)
        if isinstance(model, str) and model:
            self._model = model
        instructions = _get_attr(session, "instructions", None)
        if instructions is not None:
            self._session_config["instructions"] = str(instructions)
        modalities = _get_attr(session, "output_modalities", None) or _get_attr(session, "modalities", None)
        if modalities:
            self._session_config["output_modalities"] = list(modalities)

        input_format = output_format = voice = None
        audio = _get_attr(session, "audio", None)
        if audio is not None:
            audio_input = _get_attr(audio, "input", None)
            audio_output = _get_attr(audio, "output", None)
            input_format = _get_attr(audio_input, "format", None)
            output_format = _get_attr(audio_output, "format", None)
            voice = _get_attr(audio_output, "voice", None)
        # Legacy flat fields (older SDKs).
        input_format = input_format if input_format is not None else _get_attr(session, "input_audio_format", None)
        output_format = output_format if output_format is not None else _get_attr(session, "output_audio_format", None)
        voice = voice if voice is not None else _get_attr(session, "voice", None)

        if input_format is not None:
            self._input_audio_mime = realtime_audio_format_to_mime(input_format)
            self._session_config["input_audio_format"] = self._input_audio_mime
        if output_format is not None:
            self._output_audio_mime = realtime_audio_format_to_mime(output_format)
            self._session_config["output_audio_format"] = self._output_audio_mime
        if voice is not None:
            self._session_config["voice"] = str(voice)

        self.tag_session()

    def _absorb_input_item(self, item: Any) -> None:
        if item is None:
            return
        # Only user items contribute to the input turn; skip assistant/system/tool/function items.
        role = _get_attr(item, "role", None)
        if role is not None and role != "user":
            return
        content = _get_attr(item, "content", None) or []
        for part in content:
            part_type = _get_attr(part, "type", "")
            if part_type in ("input_text", "text"):
                self._pending_input.text += str(_get_attr(part, "text", "") or "")
            elif part_type in ("input_audio", "audio"):
                audio = _get_attr(part, "audio", None)
                if audio:
                    self._pending_input.audio_chunks.append(audio)
                transcript = _get_attr(part, "transcript", None)
                if transcript:
                    self._pending_input.transcript += str(transcript)


def _usage_metrics(usage: Any) -> Optional[dict[str, Any]]:
    if not usage:
        return None
    metrics = {}
    input_tokens = _get_attr(usage, "input_tokens", None)
    output_tokens = _get_attr(usage, "output_tokens", None)
    total_tokens = _get_attr(usage, "total_tokens", None)
    if input_tokens is not None:
        metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens
    if output_tokens is not None:
        metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
    if total_tokens is not None:
        metrics[TOTAL_TOKENS_METRIC_KEY] = total_tokens
    return metrics or None


def _start_realtime_session(integration: Any, client: Any, model: Optional[str], connection: Any) -> _RealtimeState:
    session_span = integration.trace(
        "createRealtimeSession",
        instance=SimpleNamespace(_client=client),
        activate=False,
    )
    state = _RealtimeState(integration, session_span, client=client, model=model)
    state.tag_session()
    return state


# -- wrappers ---------------------------------------------------------------


def _integration() -> Any:
    integration = getattr(openai, "_datadog_integration", None)
    if integration is None or not integration.llmobs_enabled:
        return None
    return integration


def patched_connect(func, instance, args, kwargs):
    manager = func(*args, **kwargs)
    if _integration() is None:
        return manager
    try:
        manager._dd_client = getattr(instance, "_client", None)
        model = kwargs.get("model")
        manager._dd_model = model if isinstance(model, str) else None
    except Exception:
        log.debug("error annotating realtime connection manager", exc_info=True)
    return manager


def _attach_session(instance, connection):
    integration = _integration()
    if integration is None:
        return
    try:
        connection._dd_realtime_state = _start_realtime_session(
            integration, getattr(instance, "_dd_client", None), getattr(instance, "_dd_model", None), connection
        )
    except Exception:
        log.debug("error starting realtime session span", exc_info=True)


def patched_enter(func, instance, args, kwargs):
    connection = func(*args, **kwargs)
    _attach_session(instance, connection)
    return connection


async def patched_async_enter(func, instance, args, kwargs):
    connection = await func(*args, **kwargs)
    _attach_session(instance, connection)
    return connection


def patched_parse_event(func, instance, args, kwargs):
    # ``parse_event`` is the single sync observation point for server events: ``recv()``,
    # connection iteration, and the manual ``recv_bytes()`` + ``parse_event()`` path all funnel
    # through it (it is synchronous on both the sync and async connection classes).
    event = func(*args, **kwargs)
    state = getattr(instance, "_dd_realtime_state", None)
    if state is not None:
        state.on_server_event(event)
    return event


def patched_send(func, instance, args, kwargs):
    # Record the client event only after the send succeeds, so a failed send doesn't attribute
    # unsent audio/text to the next turn.
    result = func(*args, **kwargs)
    state = getattr(instance, "_dd_realtime_state", None)
    if state is not None:
        state.on_client_event(args[0] if args else kwargs.get("event"))
    return result


async def patched_async_send(func, instance, args, kwargs):
    result = await func(*args, **kwargs)
    state = getattr(instance, "_dd_realtime_state", None)
    if state is not None:
        state.on_client_event(args[0] if args else kwargs.get("event"))
    return result


def patched_close(func, instance, args, kwargs):
    try:
        return func(*args, **kwargs)
    finally:
        state = getattr(instance, "_dd_realtime_state", None)
        if state is not None:
            state.finish_session()


async def patched_async_close(func, instance, args, kwargs):
    try:
        return await func(*args, **kwargs)
    finally:
        state = getattr(instance, "_dd_realtime_state", None)
        if state is not None:
            state.finish_session()


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
    ("RealtimeConnection", "close", patched_close),
    ("AsyncRealtimeConnection", "parse_event", patched_parse_event),
    ("AsyncRealtimeConnection", "send", patched_async_send),
    ("AsyncRealtimeConnection", "close", patched_async_close),
)


def _realtime_modules():
    modules = []
    for path in _REALTIME_MODULE_PATHS:
        try:
            modules.append(importlib.import_module(path))
        except ImportError:
            continue
    return modules


def patch_realtime():
    for module in _realtime_modules():
        for class_name, method_name, wrapper in _REALTIME_WRAPS:
            cls = getattr(module, class_name, None)
            if cls is None or not hasattr(cls, method_name):
                continue
            try:
                wrap(module, "{}.{}".format(class_name, method_name), wrapper)
            except Exception:
                log.debug("failed to wrap realtime %s.%s", class_name, method_name, exc_info=True)


def unpatch_realtime():
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
