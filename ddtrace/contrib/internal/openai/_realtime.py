"""Instrumentation for the OpenAI Realtime API (bidirectional WebSocket event stream).

The Realtime API is not request/response, so it can't reuse the streaming path. Instead we wrap
the connection's send/parse_event/close methods (all typed sub-resource sends funnel through
RealtimeConnection.send, and recv/iteration/recv_bytes() all funnel through parse_event) and feed
each observed event into a _RealtimeState machine.

Each conversation turn becomes its own llm span (started on response.created), carrying the
user/assistant transcripts, audio, token usage, and any tool calls for that turn (function calls and
MCP calls the model made, plus the function_call_output results the app fed back). Every turn span
is annotated with a per-connection session_id so the UI groups them into one conversation - there is
no parent
"session" span, which keeps each trace one turn small (no accumulation toward the per-event size
budget) and renders cleanly. (If the caller wraps the connection in their own LLMObs context, the
turn spans naturally nest under it.)

A turn span is finalized on response.done - except that the user's input transcription
(conversation.item.input_audio_transcription.completed) is asynchronous and frequently arrives after
response.done, so when the transcript isn't ready yet we hold the span open and finalize it once the
transcription lands (matched by input item_id), with fallbacks on the next response.created or on
close so a span can never leak.

Realtime audio is raw PCM16 (24kHz mono) by default, which the UI can't render directly, so we wrap
it in a WAV container (lossless, just a header) and emit a playable audio/wav audio_part alongside
the transcript. G.711 telephony audio (audio/pcmu, audio/pcma - used for phone-call integrations)
is decoded to PCM16 and likewise WAV-wrapped. Audio over the per-span-event size budget is dropped
(transcript kept); any other unsupported format falls back to an [audio] marker.

Known limitations (deferred by design):
- Out-of-band responses created with an inline response.create.response.input are not paired with
  that explicit input; their input message reflects the pending conversation turn instead.
- A single pending-input turn is tracked, so multiple committed items or overlapping/parallel
  responses may be collapsed or paired by arrival order. The Realtime API serializes turns in
  normal use.
"""

import importlib
import os
import time
from types import ModuleType
from types import SimpleNamespace
from typing import Any
from typing import Callable
from typing import Optional
import uuid

import openai

from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import deep_getattr
from ddtrace.llmobs._constants import AUDIO_FALLBACK_MARKER
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.audio_utils import G711_SAMPLE_RATE
from ddtrace.llmobs._integrations.audio_utils import LLMOBS_AUDIO_INLINE_MAX_BYTES
from ddtrace.llmobs._integrations.audio_utils import concat_base64_audio
from ddtrace.llmobs._integrations.audio_utils import format_audio_part_with_guard
from ddtrace.llmobs._integrations.audio_utils import g711_to_pcm16
from ddtrace.llmobs._integrations.audio_utils import g711_variant
from ddtrace.llmobs._integrations.audio_utils import is_pcm16_audio_mime
from ddtrace.llmobs._integrations.audio_utils import pcm16_to_wav
from ddtrace.llmobs._integrations.audio_utils import realtime_audio_format_to_mime
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import safe_load_json
from ddtrace.llmobs.types import Message
from ddtrace.llmobs.types import ToolCall
from ddtrace.llmobs.types import ToolResult


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


# Cap how much audio we buffer per side of a turn. Beyond this the size guard would drop it anyway
# (it exceeds the per-span-event budget once encoded), so we stop storing to avoid holding megabytes
# of audio in memory only to discard it at finalize.
_AUDIO_ACCUM_MAX_BYTES = LLMOBS_AUDIO_INLINE_MAX_BYTES


class _AudioAccumulator:
    """Collects base64 audio chunks with a running decoded-byte cap.

    ``present`` records that audio was seen at all (so a turn can still surface an ``[audio]`` marker
    even when the bytes were dropped), and ``oversize`` marks that the cap was hit.
    """

    def __init__(self) -> None:
        self.chunks: list[str] = []
        self.present: bool = False
        self.oversize: bool = False
        self._bytes: int = 0
        # Wall-clock (unix ns) when the first chunk of this segment was observed. Anchors the segment
        # on the shared session timeline for full-conversation playback. Set even when bytes are later
        # dropped (oversize), since ``present`` still surfaces a marker.
        self.start_ns: Optional[int] = None

    def append(self, b64: str) -> None:
        if not b64:
            return
        if self.start_ns is None:
            self.start_ns = time.time_ns()
        self.present = True
        if self.oversize:
            return
        self._bytes += (len(b64) * 3) // 4  # decoded size ~= 3/4 of the base64 length
        if self._bytes > _AUDIO_ACCUM_MAX_BYTES:
            self.oversize = True
            self.chunks = []  # free what we had — the guard would drop the whole thing anyway
            return
        self.chunks.append(b64)

    def clear(self) -> None:
        self.chunks = []
        self.present = False
        self.oversize = False
        self._bytes = 0
        self.start_ns = None


class _InputTurn:
    """Accumulated user input (audio + transcript/text) for a single turn."""

    def __init__(self) -> None:
        self.audio = _AudioAccumulator()
        self.text: str = ""
        self.transcript: str = ""
        self.item_id: Optional[str] = None
        # Wall-clock (unix ns) when the user's input audio was committed (~ end of user speech). Used
        # to measure response latency from real speech-end rather than the padded buffer end.
        self.speech_end_ns: Optional[int] = None
        # Tool results the app fed back (function_call_output) before the next response.
        self.tool_results: list[ToolResult] = []


class _ResponseTurn:
    """Accumulated assistant output for a single ``response.*`` lifecycle."""

    def __init__(self, input_turn: _InputTurn) -> None:
        self.input = input_turn
        self.audio = _AudioAccumulator()
        self.transcript: str = ""
        self.text: str = ""
        self.usage: Any = None
        self.model: Optional[str] = None
        self.status: Optional[str] = None
        self.span: Any = None
        # Function/MCP calls the model made this turn (+ inline MCP results).
        self.tool_calls: list[ToolCall] = []
        self.tool_results: list[ToolResult] = []


class _PendingToolSpan:
    """A tool-kind child span opened for a single function/MCP call, awaiting its result.

    The span is parented under the emitting turn's span and started at the wall-clock moment the
    call was first observed, so its duration reflects real tool-execution latency. ``arguments`` is
    the parsed call input (backfilled from ``function_call_arguments.done`` or the item on
    ``response.done`` when the earlier events didn't carry it).
    """

    def __init__(self, span: Any, turn: "_ResponseTurn", name: str) -> None:
        self.span = span
        self.turn = turn
        self.name = name
        self.arguments: Any = None


class _RealtimeState:
    """Drives per-turn LLMObs spans (grouped by session_id) off the realtime event stream."""

    def __init__(self, integration: Any, client: Any = None, model: Optional[str] = None) -> None:
        self._integration = integration
        self._client = client
        self._model = model
        # Per-connection id used to group every turn span into one conversation in the UI.
        self._session_id = uuid.uuid4().hex
        self._session_config: dict[str, Any] = {}
        self._input_audio_mime: str = ""
        self._output_audio_mime: str = ""
        # Realtime PCM is 24kHz mono by spec; overridden from the format object when present.
        self._input_audio_rate: int = 24000
        self._output_audio_rate: int = 24000
        # Whether the session enabled input-audio transcription. We only defer a turn's span to wait
        # for a transcript when it's actually configured — otherwise no transcript is ever coming.
        self._input_transcription_enabled: bool = False
        self._pending_input = _InputTurn()
        self._responses: dict[str, Any] = {}
        self._input_transcripts: dict[str, str] = {}
        # call_id -> function name, so a later function_call_output can be labeled with its tool name.
        self._tool_call_names: dict[str, str] = {}
        # call_id -> open tool-kind child span, from when the call is first observed until its result
        # lands (function_call_output for client calls, inline output for server MCP calls).
        self._pending_tool_spans: dict[str, _PendingToolSpan] = {}
        # Turns whose response is done but whose input transcription hasn't arrived yet.
        self._awaiting: list[Any] = []
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
                    self._pending_input.audio.append(audio)
            elif event_type == "input_audio_buffer.clear":
                # Discarded input audio must not be attributed to the next response.
                self._pending_input.audio.clear()
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
                self._pending_input.speech_end_ns = time.time_ns()
                return
            if event_type == "input_audio_buffer.cleared":
                self._pending_input.audio.clear()
                return
            if event_type == "conversation.item.input_audio_transcription.completed":
                item_id = _get_attr(event, "item_id", None)
                transcript = str(_get_attr(event, "transcript", "") or "")
                if item_id is not None:
                    self._input_transcripts[item_id] = transcript
                if self._pending_input.item_id == item_id and not self._pending_input.transcript:
                    self._pending_input.transcript = transcript
                # A finished turn may have been waiting on exactly this transcript — finalize it now.
                for turn in [t for t in self._awaiting if t.input.item_id == item_id]:
                    turn.input.transcript = turn.input.transcript or transcript
                    self._awaiting.remove(turn)
                    self._finalize_turn(turn)
                return
            if event_type == "conversation.item.input_audio_transcription.failed":
                # Transcription won't arrive for this item; finalize any turn waiting on it so its
                # span doesn't hang (would otherwise wait until the next turn or close).
                item_id = _get_attr(event, "item_id", None)
                for turn in [t for t in self._awaiting if t.input.item_id == item_id]:
                    self._awaiting.remove(turn)
                    self._finalize_turn(turn)
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
        # Tool-call lifecycle: open a tool span when the call is first seen, capture its arguments.
        if event_type == "response.output_item.added":
            self._maybe_start_tool_span(turn, _get_attr(event, "item", None))
            return
        if event_type == "response.function_call_arguments.delta":
            # First delta is a fallback start signal when output_item.added wasn't observed.
            self._ensure_tool_span(turn, str(_get_attr(event, "call_id", "") or ""), "")
            return
        if event_type == "response.function_call_arguments.done":
            call_id = str(_get_attr(event, "call_id", "") or "")
            pending = self._ensure_tool_span(turn, call_id, "")
            args = _get_attr(event, "arguments", None)
            if pending is not None and args is not None:
                pending.arguments = safe_load_json(str(args))
            return
        if normalized == "response.audio.delta":
            delta = _get_attr(event, "delta", None)
            if delta:
                turn.audio.append(delta)
        elif normalized == "response.audio_transcript.delta":
            turn.transcript += str(_get_attr(event, "delta", "") or "")
        elif normalized == "response.audio_transcript.done":
            turn.transcript = str(_get_attr(event, "transcript", turn.transcript) or "")
        elif normalized == "response.text.delta":
            turn.text += str(_get_attr(event, "delta", "") or "")
        elif normalized == "response.text.done":
            turn.text = str(_get_attr(event, "text", turn.text) or "")

    # -- tool span lifecycle ------------------------------------------------

    def _maybe_start_tool_span(self, turn: "_ResponseTurn", item: Any) -> None:
        """Open a tool span for a function_call/mcp_call output item as soon as it's observed."""
        if item is None:
            return
        item_type = _get_attr(item, "type", None)
        if item_type not in ("function_call", "mcp_call"):
            return
        call_id = str(_get_attr(item, "call_id", "") or _get_attr(item, "id", "") or "")
        name = str(_get_attr(item, "name", "") or "")
        pending = self._ensure_tool_span(turn, call_id, name)
        if pending is not None and pending.arguments is None:
            args = _get_attr(item, "arguments", None)
            if args:
                pending.arguments = safe_load_json(str(args))

    def _ensure_tool_span(
        self, turn: "_ResponseTurn", call_id: str, name: str, start_ns: Optional[int] = None
    ) -> Optional["_PendingToolSpan"]:
        """Return the open tool span for ``call_id``, creating it (parented under the turn) if new.

        The span's start time is the wall-clock moment the call was first observed so its duration
        measures real tool-execution latency; a lazily-created span (its start event was never seen)
        falls back to the turn span's start.
        """
        if not call_id or turn is None or turn.span is None:
            return None
        pending = self._pending_tool_spans.get(call_id)
        if pending is not None:
            if name and not pending.name:
                pending.name = name
            return pending
        try:
            # Parent under the emitting turn's span (APM child_of) and back-date to when the call was
            # observed. The turn span may finish (at response.done) before this tool span does — that
            # is expected; the child simply finishes afterward.
            tool_span = self._integration.trace(
                "createRealtimeToolCall",
                submit_to_llmobs=True,
                instance=SimpleNamespace(_client=self._client),
                activate=False,
                parent_context=turn.span,
            )
            tool_span.start_ns = int(start_ns if start_ns is not None else time.time_ns())
        except Exception:
            log.debug("error starting realtime tool span", exc_info=True)
            return None
        pending = _PendingToolSpan(tool_span, turn, name)
        self._pending_tool_spans[call_id] = pending
        return pending

    def _finish_tool_span(self, call_id: str, result: Any, error: Any = None) -> None:
        """Finalize and finish the tool span for ``call_id`` with its result/error."""
        pending = self._pending_tool_spans.pop(call_id, None)
        if pending is None or pending.span is None:
            return
        try:
            output = result if result is not None else error
            self._integration._llmobs_set_tags_from_realtime_tool(
                pending.span,
                pending.turn.span,
                name=pending.name,
                call_id=call_id,
                arguments=pending.arguments,
                result=output,
                session_id=self._session_id,
                error=result is None and error is not None,
            )
        except Exception:
            log.debug("error tagging realtime tool span", exc_info=True)
        finally:
            pending.span.finish()

    # -- span lifecycle -----------------------------------------------------

    def _start_response(self, response_id: Optional[str]) -> None:
        if response_id is None:
            return
        # A new turn starting means a prior turn's input transcription is almost certainly not coming
        # anymore — flush anything still waiting so its span doesn't hang.
        self._flush_awaiting()
        turn = _ResponseTurn(self._pending_input)
        self._pending_input = _InputTurn()
        turn.model = self._model
        try:
            # No parent_context: each turn is its own root trace, grouped by session_id (or nested
            # under the caller's own LLMObs context if there is one).
            turn.span = self._integration.trace(
                "createRealtimeResponse",
                instance=SimpleNamespace(_client=self._client),
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
        turn.tool_calls, turn.tool_results = _extract_response_tools(response)
        # Backfill each call's name/arguments onto its open tool span (earlier events may not have
        # carried them), then handle the two result paths. Client function_call spans stay open until
        # the app returns a function_call_output; server MCP calls carry their result inline here.
        mcp_results = {tr.get("tool_id"): tr.get("result") for tr in turn.tool_results}
        for tool_call in turn.tool_calls:
            call_id = tool_call.get("tool_id")
            if not call_id:
                continue
            name = tool_call.get("name", "")
            call_type = tool_call.get("type")
            if call_type == "mcp_call":
                # Ensure a span exists even if output_item.added was never seen, then finish inline.
                self._ensure_tool_span(turn, call_id, name, start_ns=turn.span.start_ns if turn.span else None)
            pending = self._pending_tool_spans.get(call_id)
            if pending is not None:
                if not pending.name and name:
                    pending.name = name
                if pending.arguments is None:
                    pending.arguments = tool_call.get("arguments")
            if call_type == "function":
                # Label the eventual function_call_output with this call's tool name.
                self._tool_call_names[call_id] = name
            elif call_type == "mcp_call":
                self._finish_tool_span(call_id, mcp_results.get(call_id))
        if not turn.input.transcript and turn.input.item_id is not None:
            turn.input.transcript = self._input_transcripts.get(turn.input.item_id, "")
        # Hold the span open for a late input transcription ONLY when transcription is actually
        # enabled — otherwise no transcript is ever coming and waiting would needlessly delay the
        # span (every turn until the next one, and the last turn until close).
        if not turn.input.transcript and turn.input.item_id is not None and self._input_transcription_enabled:
            self._awaiting.append(turn)
            return
        self._finalize_turn(turn)

    def _flush_awaiting(self) -> None:
        for turn in self._awaiting:
            self._finalize_turn(turn)
        self._awaiting = []

    def finish_session(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Finalize anything still open: turns awaiting a transcription, plus in-flight turns that
        # never saw ``response.done`` (closed mid-turn). Whatever partial data we have is submitted.
        self._flush_awaiting()
        for turn in list(self._responses.values()):
            if not turn.input.transcript and turn.input.item_id is not None:
                turn.input.transcript = self._input_transcripts.get(turn.input.item_id, "")
            self._finalize_turn(turn)
        # Finish any tool spans whose result never arrived (client call still in flight at close) so
        # none leak — submitted with whatever arguments we captured and an empty result.
        for call_id in list(self._pending_tool_spans):
            self._finish_tool_span(call_id, "")
        self._responses.clear()
        self._input_transcripts.clear()
        self._tool_call_names.clear()
        self._pending_tool_spans.clear()

    # -- tagging helpers ----------------------------------------------------

    def _finalize_turn(self, turn: _ResponseTurn) -> None:
        if turn.span is None:
            return
        # Drop the cached transcript for this turn's input item so the map can't grow across a long
        # session (every finalize path goes through here).
        if turn.input.item_id is not None:
            self._input_transcripts.pop(turn.input.item_id, None)
        try:
            if turn.status == "failed":
                turn.span.error = 1
            self._tag_response(turn)
        except Exception:
            log.debug("error tagging realtime response span", exc_info=True)
        finally:
            turn.span.finish()

    def _tag_response(self, turn: _ResponseTurn) -> None:
        input_message = self._build_message(
            "user",
            turn.input.transcript or turn.input.text,
            turn.input.audio,
            self._input_audio_mime,
            self._input_audio_rate,
        )
        # Attach tool results the app fed back (function_call_output) to the input message.
        if turn.input.tool_results:
            if input_message is None:
                input_message = Message(role="user", content="")
            input_message["tool_results"] = turn.input.tool_results

        output_message = self._build_message(
            "assistant",
            turn.transcript or turn.text,
            turn.audio,
            self._output_audio_mime,
            self._output_audio_rate,
        )
        # Attach the model's tool calls (and inline MCP results) to the output message. A turn can be
        # tool-call-only with no audio/text, so create the message if _build_message returned None.
        if turn.tool_calls or turn.tool_results:
            if output_message is None:
                output_message = Message(role="assistant", content="")
            if turn.tool_calls:
                output_message["tool_calls"] = turn.tool_calls
            if turn.tool_results:
                output_message["tool_results"] = turn.tool_results

        self._integration._llmobs_set_tags_from_realtime_response(
            turn.span,
            turn.model,
            [input_message] if input_message else [],
            [output_message] if output_message else [],
            metadata=self._session_metadata(),
            metrics=_usage_metrics(turn.usage),
            session_id=self._session_id,
            audio_timing=_audio_timing(turn),
        )

    def _build_message(
        self, role: str, content: str, audio: "_AudioAccumulator", mime_type: str, sample_rate: int
    ) -> Optional[Message]:
        audio_part = None
        if audio.chunks:
            audio_bytes = concat_base64_audio(audio.chunks)
            g711 = g711_variant(mime_type)
            if is_pcm16_audio_mime(mime_type):
                # Raw PCM16 isn't renderable on its own; wrap it in a WAV container (lossless) so it
                # plays in the UI. Realtime PCM is 24kHz mono.
                audio_part = format_audio_part_with_guard(pcm16_to_wav(audio_bytes, sample_rate), "audio/wav")
            elif g711 is not None:
                # G.711 telephony audio (phone-call integrations): decode to PCM16, then WAV-wrap at
                # the fixed 8kHz G.711 rate so it's playable.
                pcm = g711_to_pcm16(audio_bytes, g711)
                audio_part = format_audio_part_with_guard(pcm16_to_wav(pcm, G711_SAMPLE_RATE), "audio/wav")
            else:
                audio_part = format_audio_part_with_guard(audio_bytes, mime_type)
        if not content and not audio_part and audio.present:
            # Audio was captured but couldn't be turned into a playable part (unsupported format, or
            # over the size budget / accumulation cap) and there's no transcript; surface a marker so
            # the turn isn't silently empty.
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
            if _get_attr(audio_input, "transcription", None) is not None:
                self._input_transcription_enabled = True
        # Legacy flat fields (older SDKs).
        if _get_attr(session, "input_audio_transcription", None) is not None:
            self._input_transcription_enabled = True
        input_format = input_format if input_format is not None else _get_attr(session, "input_audio_format", None)
        output_format = output_format if output_format is not None else _get_attr(session, "output_audio_format", None)
        voice = voice if voice is not None else _get_attr(session, "voice", None)

        if input_format is not None:
            self._input_audio_mime = realtime_audio_format_to_mime(input_format)
            self._session_config["input_audio_format"] = self._input_audio_mime
            input_rate = _get_attr(input_format, "rate", None)
            if input_rate:
                self._input_audio_rate = int(input_rate)
        if output_format is not None:
            self._output_audio_mime = realtime_audio_format_to_mime(output_format)
            self._session_config["output_audio_format"] = self._output_audio_mime
            output_rate = _get_attr(output_format, "rate", None)
            if output_rate:
                self._output_audio_rate = int(output_rate)
        if voice is not None:
            self._session_config["voice"] = str(voice)

    def _absorb_input_item(self, item: Any) -> None:
        if item is None:
            return
        # A tool result the app feeds back becomes a tool_result on the next turn's input.
        if _get_attr(item, "type", None) == "function_call_output":
            output = _get_attr(item, "output", None)
            call_id = str(_get_attr(item, "call_id", "") or "")
            result = ToolResult(
                tool_id=call_id,
                result=str(output) if output is not None else "",
                type="function_call_output",
            )
            # Label the result with the function name carried by the originating call.
            name = self._tool_call_names.pop(call_id, None)
            if name:
                result["name"] = name
            self._pending_input.tool_results.append(result)
            # This is the real tool-execution result for the open function_call span: finish it now,
            # giving the child span a duration that spans the actual tool call.
            self._finish_tool_span(call_id, str(output) if output is not None else "")
            return
        # Only user items contribute to the input turn; skip assistant/system items.
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
                    self._pending_input.audio.append(audio)
                transcript = _get_attr(part, "transcript", None)
                if transcript:
                    self._pending_input.transcript += str(transcript)


def _extract_response_tools(response: Any) -> tuple[list[ToolCall], list[ToolResult]]:
    """Pull function_call / mcp_call tool usage from a response.done's output items.

    Function calls become ToolCalls (the app returns their result later via function_call_output,
    captured on the next turn's input). MCP calls run server-side, so their result is inline on the
    item and is captured as a ToolResult alongside the call.
    """
    tool_calls: list[ToolCall] = []
    tool_results: list[ToolResult] = []
    for item in _get_attr(response, "output", None) or []:
        item_type = _get_attr(item, "type", "")
        if item_type == "function_call":
            tool_calls.append(
                ToolCall(
                    name=str(_get_attr(item, "name", "") or ""),
                    arguments=safe_load_json(str(_get_attr(item, "arguments", "") or "")),
                    tool_id=str(_get_attr(item, "call_id", "") or _get_attr(item, "id", "") or ""),
                    type="function",
                )
            )
        elif item_type == "mcp_call":
            call_id = str(_get_attr(item, "id", "") or "")
            name = str(_get_attr(item, "name", "") or "")
            tool_calls.append(
                ToolCall(
                    name=name,
                    arguments=safe_load_json(str(_get_attr(item, "arguments", "") or "")),
                    tool_id=call_id,
                    type="mcp_call",
                )
            )
            output = _get_attr(item, "output", None)
            error = _get_attr(item, "error", None)
            if output is not None or error is not None:
                tool_results.append(
                    ToolResult(
                        name=name,
                        result=str(output if output is not None else error),
                        tool_id=call_id,
                        type="mcp_tool_result",
                    )
                )
    return tool_calls, tool_results


def _usage_metrics(usage: Any) -> Optional[dict[str, Any]]:
    if not usage:
        return None
    metrics: dict[str, Any] = {}
    input_tokens = _get_attr(usage, "input_tokens", None)
    output_tokens = _get_attr(usage, "output_tokens", None)
    total_tokens = _get_attr(usage, "total_tokens", None)
    if input_tokens is not None:
        metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens
    if output_tokens is not None:
        metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens  # mirror the chat/responses fallback
    if total_tokens is not None:
        metrics[TOTAL_TOKENS_METRIC_KEY] = total_tokens
    return metrics or None


def _audio_timing(turn: "_ResponseTurn") -> Optional[dict[str, int]]:
    """Absolute (unix ns) anchors for this turn's audio segments on the shared session timeline.

    These let the full-conversation-playback UI place each segment and compute time-to-response.
    Captured on our own clock when the audio was observed, so they are provider-agnostic. Emitted as
    span metadata under the ``_dd.`` prefix (not tags — timestamps are high-cardinality) so they are
    treated as internal and hidden from the metadata UI. Keys are omitted when the segment has no
    audio (a text-only or tool-only turn carries none of them).
    """
    timing: dict[str, int] = {}
    if turn.input.audio.start_ns is not None:
        timing["_dd.llmobs.audio.input.start_time_unix_nano"] = turn.input.audio.start_ns
    if turn.audio.start_ns is not None:
        timing["_dd.llmobs.audio.output.start_time_unix_nano"] = turn.audio.start_ns
    if turn.input.speech_end_ns is not None:
        timing["_dd.llmobs.audio.input.speech_end_time_unix_nano"] = turn.input.speech_end_ns
    return timing or None


def _start_realtime_state(integration: Any, client: Any, model: Optional[str]) -> _RealtimeState:
    return _RealtimeState(integration, client=client, model=model)


# -- wrappers ---------------------------------------------------------------


def _integration() -> Any:
    integration = getattr(openai, "_datadog_integration", None)
    if integration is None or not integration.llmobs_enabled:
        return None
    return integration


def patched_connect(func: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
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


def _attach_session(instance: Any, connection: Any) -> None:
    integration = _integration()
    if integration is None:
        return
    try:
        connection._dd_realtime_state = _start_realtime_state(
            integration, getattr(instance, "_dd_client", None), getattr(instance, "_dd_model", None)
        )
    except Exception:
        log.debug("error starting realtime state", exc_info=True)


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
    # ``parse_event`` is the single sync observation point for server events: ``recv()``,
    # connection iteration, and the manual ``recv_bytes()`` + ``parse_event()`` path all funnel
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
    if not asbool(os.environ.get("DD_OPENAI_REALTIME_ENABLED", "true")):
        return
    for module in _realtime_modules():
        for class_name, method_name, wrapper in _REALTIME_WRAPS:
            cls = getattr(module, class_name, None)
            if cls is None or not hasattr(cls, method_name):
                continue
            try:
                wrap(module, "{}.{}".format(class_name, method_name), wrapper)
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
