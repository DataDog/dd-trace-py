import base64
from collections import OrderedDict
from contextvars import ContextVar
import json
import math
import time
from typing import Any
from typing import Optional
from uuid import uuid4

from ddtrace.internal.logger import get_logger
from ddtrace.internal.threads import RLock
from ddtrace.llmobs._integrations.audio_utils import LLMOBS_AUDIO_INLINE_MAX_BYTES
from ddtrace.llmobs._integrations.audio_utils import format_audio_part_with_guard
from ddtrace.llmobs._integrations.audio_utils import pcm16_to_wav
from ddtrace.llmobs._integrations.elevenlabs import ElevenLabsIntegration
from ddtrace.llmobs.types import Message
from ddtrace.llmobs.types import ToolCall
from ddtrace.llmobs.types import ToolResult
from ddtrace.trace import Span


log = get_logger(__name__)


_RAW_LIMIT = LLMOBS_AUDIO_INLINE_MAX_BYTES * 3 // 4 - 128
_MAX_OPEN_TURNS = 2
_MAX_TOOLS = 64
_MAX_ACTIVITY_INTERVALS = 128
_VAD_HOLD_NS = 500_000_000
_CURRENT_STATE: ContextVar[Optional["ConversationState"]] = ContextVar("elevenlabs_conversation_state", default=None)
_EVENT_FIELDS = {
    "user_transcript": "user_transcription_event",
    "audio": "audio_event",
    "agent_response": "agent_response_event",
    "agent_response_correction": "agent_response_correction_event",
    "agent_response_complete": "agent_response_complete_event",
    "agent_chat_response_part": "text_response_part",
    "client_tool_call": "client_tool_call",
    "context_usage": "context_usage_event",
    "agent_tool_response_full_payload": "agent_tool_response_full_payload",
}


def _rate(value: Any) -> int:
    if isinstance(value, str) and value.startswith("pcm_"):
        try:
            rate = int(value[4:])
            return rate if 8000 <= rate <= 48000 else 0
        except ValueError:
            pass
    return 0


def _event_id(event: dict[str, Any]) -> Optional[int]:
    value = event.get("event_id")
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isascii() and value.isdigit():
        try:
            return int(value)
        except ValueError:
            pass
    return None


class SpeechActivity:
    """Bounded silence observations; unobserved microphone samples remain active."""

    def __init__(self) -> None:
        self.silence: list[tuple[int, int]] = []
        self.last_offset: Optional[int] = None
        self.last_received: Optional[int] = None
        self.speaking = True
        self.overflow = False

    def _silence(self, start: int, end: int) -> None:
        start = max(0, start)
        if end <= start or self.overflow:
            return
        if self.silence and start <= self.silence[-1][1]:
            self.silence[-1] = (self.silence[-1][0], max(end, self.silence[-1][1]))
        elif len(self.silence) < _MAX_ACTIVITY_INTERVALS:
            self.silence.append((start, end))
        else:
            self.silence.clear()
            self.overflow = True

    def observe(self, score: Any, offset: int, now: int) -> None:
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return
        if not 0 <= score <= 1 or not math.isfinite(score):
            return
        if self.last_offset is not None:
            if offset < self.last_offset or (self.last_received is not None and now < self.last_received):
                return
            # Scores have no acoustic timestamp. Never extend a silence estimate
            # through a long delivery gap or beyond half a second of input samples.
            if not self.speaking and self.last_received is not None and now - self.last_received <= _VAD_HOLD_NS:
                self._silence(self.last_offset, min(offset, self.last_offset + _VAD_HOLD_NS))
            if offset - self.last_offset > _VAD_HOLD_NS or now - (self.last_received or now) > _VAD_HOLD_NS:
                self.speaking = True
        self.last_offset = offset
        self.last_received = now
        if score >= 0.5:
            self.speaking = True
        elif score <= 0.35:
            self.speaking = False

    def continue_at(self, duration: int) -> "SpeechActivity":
        following = SpeechActivity()
        if self.last_offset is not None:
            following.last_offset = self.last_offset - duration
            following.last_received = self.last_received
            following.speaking = self.speaking
        return following

    def extend(self, other: "SpeechActivity", duration: int, last_audio_ns: Optional[int]) -> None:
        if (
            not self.speaking
            and self.last_offset is not None
            and self.last_received is not None
            and last_audio_ns is not None
            and last_audio_ns - self.last_received <= _VAD_HOLD_NS
        ):
            self._silence(self.last_offset, min(duration, self.last_offset + _VAD_HOLD_NS))
        for start, end in other.silence:
            self._silence(start + duration, end + duration)
        if other.last_offset is not None:
            self.last_offset = other.last_offset + duration
            self.last_received = other.last_received
            self.speaking = other.speaking
        self.overflow = self.overflow or other.overflow

    def metadata(self, duration: int, last_audio_ns: Optional[int]) -> Optional[dict[str, Any]]:
        if self.last_offset is None or self.overflow or duration <= 0:
            return None
        silence = list(self.silence)
        if (
            not self.speaking
            and self.last_received is not None
            and last_audio_ns is not None
            and last_audio_ns - self.last_received <= _VAD_HOLD_NS
        ):
            silence.append((max(0, self.last_offset), min(duration, self.last_offset + _VAD_HOLD_NS)))
        intervals = []
        cursor = 0
        for start, end in silence:
            start, end = min(duration, start), min(duration, end)
            if start > cursor:
                intervals.append([cursor // 1_000_000, (start + 999_999) // 1_000_000])
            cursor = max(cursor, end)
        if cursor < duration:
            intervals.append([cursor // 1_000_000, (duration + 999_999) // 1_000_000])
        merged: list[list[int]] = []
        for start, end in intervals:
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        if len(merged) > _MAX_ACTIVITY_INTERVALS:
            return None
        return {"version": 1, "source": "elevenlabs_vad", "timing": "estimated", "intervals_ms": merged}


class AudioBuffer:
    def __init__(self) -> None:
        self.data = bytearray()
        self.start_ns: Optional[int] = None
        self.total_bytes = 0
        self.overflow = False
        self.activity = SpeechActivity()
        self.last_audio_ns: Optional[int] = None

    def append(self, encoded: Any, now: int, rate: int, input_audio: bool = False) -> None:
        if not rate or not isinstance(encoded, str):
            return
        if len(encoded) > (_RAW_LIMIT + 2) * 4 // 3:
            self.data.clear()
            self.overflow = True
            return
        try:
            chunk = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            return
        if len(chunk) % 2:
            return
        if self.start_ns is None:
            self.start_ns = now - len(chunk) * 1_000_000_000 // (2 * rate) if input_audio else now
        self.total_bytes += len(chunk)
        self.last_audio_ns = now
        if not self.overflow and len(self.data) + len(chunk) <= _RAW_LIMIT:
            self.data.extend(chunk)
        else:
            self.data.clear()
            self.overflow = True

    def duration_ns(self, rate: int) -> int:
        return self.total_bytes * 1_000_000_000 // (2 * rate) if rate else 0

    def extend(self, other: "AudioBuffer", rate: int) -> None:
        self.activity.extend(other.activity, self.duration_ns(rate), self.last_audio_ns)
        if self.start_ns is None:
            self.start_ns = other.start_ns
        self.total_bytes += other.total_bytes
        if other.last_audio_ns is not None:
            self.last_audio_ns = other.last_audio_ns
        if self.overflow or other.overflow or len(self.data) + len(other.data) > _RAW_LIMIT:
            self.data.clear()
            self.overflow = True
        else:
            self.data.extend(other.data)

    def trim(self, end_ns: int, rate: int) -> None:
        if self.start_ns is None or not rate:
            return
        size = max(0, (end_ns - self.start_ns) * rate // 1_000_000_000) * 2
        self.total_bytes = min(self.total_bytes, size)
        if not self.overflow:
            del self.data[size:]

    def end_ns(self, rate: int) -> Optional[int]:
        if self.start_ns is None or not rate:
            return None
        return self.start_ns + self.total_bytes * 1_000_000_000 // (2 * rate)


class Turn:
    def __init__(self, event_id: int, input_audio: AudioBuffer, now: int) -> None:
        self.event_id = event_id
        self.input_audio = input_audio
        self.output_audio = AudioBuffer()
        self.user_text = ""
        self.agent_text = ""
        self.text_parts = ""
        self.tool_end_ns = 0
        self.tool_execution_incomplete = False
        self.response_ns = now
        self.interrupted = False
        self.root: Optional[Span] = None
        self.llm: Optional[Span] = None
        self.tool_calls: OrderedDict[str, ToolCall] = OrderedDict()
        self.tool_results: OrderedDict[str, ToolResult] = OrderedDict()


class ConversationState:
    def __init__(self, integration: ElevenLabsIntegration, conversation: Any, parent: Any = None) -> None:
        self.integration = integration
        self.agent_id = str(getattr(conversation, "agent_id", ""))
        self.parent = parent
        self.session_id = str(uuid4())
        self.input_rate = 0
        self.output_rate = 0
        self.model: Optional[str] = None
        self.pending = AudioBuffer()
        self.pending_text = ""
        self.early_audio: list[tuple[str, int]] = []
        self.early_audio_size = 0
        self.turns: OrderedDict[int, Turn] = OrderedDict()
        self.tool_turns: OrderedDict[str, Turn] = OrderedDict()
        self.tool_spans: dict[str, Span] = {}
        self.last_interrupt_id = 0
        self.last_finalized_id = -1
        self.closing = False
        self.closed = False
        self.error = False
        self.lock = RLock()

    def metadata(self, turn: Optional[Turn] = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "agent_id": self.agent_id,
            "ttfa_eligible": False,
            "audio_timing_source": "sdk_transport",
            "audio_playback": "estimated",
        }
        if self.model:
            result["configured_llm"] = self.model
        if turn is not None:
            result["event_id"] = str(turn.event_id)
            result["interrupted"] = turn.interrupted
            if turn.tool_execution_incomplete:
                result["tool_execution_incomplete"] = True
            if turn.input_audio.overflow or turn.output_audio.overflow:
                result["audio_omitted"] = True
        return result

    def _span(self, operation: str, parent: Any) -> Span:
        return self.integration.trace(
            operation,
            submit_to_llmobs=True,
            activate=False,
            parent_context=parent,
            agent_id=self.agent_id,
        )

    def _turn(self, event_id: Optional[int], now: int) -> Optional[Turn]:
        if event_id is None or self.closed:
            return None
        if event_id in self.turns:
            return self.turns[event_id]
        if event_id <= self.last_finalized_id:
            return None
        while len(self.turns) >= _MAX_OPEN_TURNS:
            _, previous = self.turns.popitem(last=False)
            self._finish_turn(previous, now)
        turn = Turn(event_id, self.pending, now)
        self.pending = AudioBuffer()
        self.pending.activity = turn.input_audio.activity.continue_at(turn.input_audio.duration_ns(self.input_rate))
        turn.user_text = self.pending_text
        self.pending_text = ""
        turn.root = self._span("conversation.turn", self.parent)
        turn.root.start_ns = turn.input_audio.start_ns or now
        self.turns[event_id] = turn
        turn.llm = self._span("conversation.response", turn.root)
        turn.llm.start_ns = now
        self.turns[event_id] = turn
        return turn

    def on_send(self, raw: Any) -> None:
        with self.lock:
            if self.closed or self.closing:
                return
            event = json.loads(raw)
            if not isinstance(event, dict):
                return
            now = time.time_ns()
            if "user_audio_chunk" in event:
                encoded = event["user_audio_chunk"]
                if self.input_rate:
                    self.pending.append(encoded, now, self.input_rate, input_audio=True)
                elif isinstance(encoded, str) and len(self.early_audio) < 256:
                    self.early_audio_size += len(encoded)
                    if self.early_audio_size <= _RAW_LIMIT * 4 // 3:
                        self.early_audio.append((encoded, now))
            elif event.get("type") == "user_message" and isinstance(event.get("text"), str):
                self.pending_text = (self.pending_text + "\n" + event["text"]).strip()[:100_000]
            elif event.get("type") == "client_tool_result":
                call_id = event.get("tool_call_id")
                if not isinstance(call_id, str):
                    return
                turn = self.tool_turns.get(call_id)
                if turn is not None:
                    turn.tool_results[call_id] = ToolResult(
                        tool_id=call_id,
                        result=str(event.get("result") or ""),
                        type="tool_result",
                    )

    def on_receive(self, raw: Any) -> None:
        with self.lock:
            if self.closed or self.closing:
                return
            event = json.loads(raw)
            if not isinstance(event, dict):
                return
            now = time.time_ns()
            kind = event.get("type")
            if not isinstance(kind, str):
                return
            if kind == "conversation_initiation_metadata":
                data = event.get("conversation_initiation_metadata_event", {})
                self.session_id = str(data.get("conversation_id") or self.session_id)
                self.input_rate = _rate(data.get("user_input_audio_format"))
                self.output_rate = _rate(data.get("agent_output_audio_format"))
                for encoded, sent_ns in self.early_audio:
                    self.pending.append(encoded, sent_ns, self.input_rate, input_audio=True)
                self.early_audio.clear()
                self.early_audio_size = 0
                return
            if kind == "interruption":
                interrupt_id = _event_id(event.get("interruption_event", {}))
                if interrupt_id is not None:
                    self.last_interrupt_id = max(self.last_interrupt_id, interrupt_id)
                    for interrupted_turn in self.turns.values():
                        if interrupted_turn.event_id <= interrupt_id:
                            interrupted_turn.interrupted = True
                            interrupted_turn.output_audio.trim(now, self.output_rate)
                return
            if kind == "vad_score":
                data = event.get("vad_score_event")
                if self.input_rate and isinstance(data, dict):
                    self.pending.activity.observe(data.get("vad_score"), self.pending.duration_ns(self.input_rate), now)
                return
            key = _EVENT_FIELDS.get(kind)
            if key is None:
                return
            data = event.get(key, {})
            if not isinstance(data, dict):
                return
            if kind == "context_usage":
                model = data.get("model")
                if isinstance(model, str):
                    self.model = model
                return
            event_id = _event_id(data)
            if event_id is None:
                return
            if kind == "audio" and (event_id is None or event_id <= self.last_interrupt_id):
                return
            if kind in ("agent_response_complete", "context_usage", "agent_tool_response_full_payload"):
                turn = self.turns.get(event_id)
            else:
                turn = self._turn(event_id, now)
            if turn is None:
                return
            if kind == "user_transcript":
                turn.user_text = str(data.get("user_transcript") or "")
            elif kind == "audio":
                turn.output_audio.append(data.get("audio_base_64"), now, self.output_rate)
                self._bound_audio(turn)
            elif kind == "agent_response":
                turn.agent_text = str(data.get("agent_response") or "")
                turn.response_ns = now
            elif kind == "agent_response_correction":
                turn.agent_text = str(data.get("corrected_agent_response") or "")
                turn.response_ns = now
            elif kind == "agent_chat_response_part":
                part = str(data.get("text") or "")
                turn.text_parts = part if data.get("type") == "start" else (turn.text_parts + part)[:100_000]
            elif kind == "agent_response_complete":
                turn.response_ns = now
            elif kind == "client_tool_call":
                call_id = data.get("tool_call_id")
                if isinstance(call_id, str) and len(turn.tool_calls) < _MAX_TOOLS:
                    turn.tool_calls[call_id] = ToolCall(
                        name=str(data.get("tool_name") or ""),
                        arguments=data.get("parameters") or {},
                        tool_id=call_id,
                        type="tool",
                    )
                    self.tool_turns[call_id] = turn
                    while len(self.tool_turns) > _MAX_TOOLS:
                        self.tool_turns.popitem(last=False)
            elif kind == "agent_tool_response_full_payload":
                call_id = data.get("tool_call_id")
                if isinstance(call_id, str) and call_id in turn.tool_calls:
                    turn.tool_results[call_id] = ToolResult(
                        tool_id=call_id,
                        result=str(data.get("full_tool_result") or ""),
                        type="tool_result",
                    )

    def _bound_audio(self, turn: Turn) -> None:
        if len(turn.input_audio.data) + len(turn.output_audio.data) > _RAW_LIMIT:
            turn.output_audio.data.clear()
            turn.output_audio.overflow = True

    def begin_tool(self, call_id: str, name: str, parameters: dict[str, Any]) -> Optional[Span]:
        with self.lock:
            turn = self.tool_turns.get(call_id)
            if self.closed or turn is None or call_id in self.tool_spans:
                return None
            span = self._span("client_tool." + name, turn.root)
            try:
                self.integration.annotate_voice_span(
                    span,
                    "tool",
                    name,
                    self.session_id,
                    self.metadata(turn),
                    parent=turn.root,
                    input_value=json.dumps(parameters),
                )
            except Exception:
                span.finish()
                raise
            self.tool_spans[call_id] = span
            return span

    def end_tool(self, call_id: str, result: Any = None, error: bool = False) -> None:
        with self.lock:
            span = self.tool_spans.pop(call_id, None)
            if span is None:
                return
            if error:
                span.error = 1
            try:
                self.integration.llmobs_set_tags(span, [], {"output_value": str(result)})
            finally:
                span.finish()
                turn = self.tool_turns.get(call_id)
                if turn is not None:
                    turn.tool_end_ns = max(turn.tool_end_ns, span.start_ns + (span.duration_ns or 0))

    def _message(self, role: str, text: str, audio: AudioBuffer, rate: int, budget: int) -> tuple[Message, int]:
        message = Message(role=role, content=text)
        if rate and audio.data and not audio.overflow:
            part = format_audio_part_with_guard(pcm16_to_wav(bytes(audio.data), rate), "audio/wav", max_bytes=budget)
            if part is not None:
                message["audio_parts"] = [part]
                budget -= len(part["content"])
        return message, budget

    def _phase(self, name: str, turn: Turn, audio: AudioBuffer, rate: int, text: str) -> None:
        start = audio.start_ns
        end = audio.end_ns(rate)
        if start is None or end is None or end <= start or turn.root is None:
            return
        metadata = self.metadata(turn)
        if name == "user speech" and not audio.overflow:
            activity = audio.activity.metadata(audio.duration_ns(rate), audio.last_audio_ns)
            if activity is not None:
                metadata["user_speech_activity"] = activity
        span = self._span("conversation." + name.replace(" ", "_"), turn.root)
        span.start_ns = start
        try:
            self.integration.annotate_voice_span(
                span,
                "workflow",
                name,
                self.session_id,
                metadata,
                parent=turn.root,
                output_value=text or None,
            )
        finally:
            span.finish(finish_time=end / 1e9)

    def _finish_turn(self, turn: Turn, now: int) -> None:
        # Observation must never prevent the SDK from handling subsequent messages.
        try:
            for call_id in turn.tool_calls:
                if call_id in self.tool_spans:
                    turn.tool_execution_incomplete = True
                    self.end_tool(call_id)
            self._annotate_turn(turn, now)
        except Exception:
            log.debug("Could not annotate ElevenLabs audio turn", exc_info=True)
        finally:
            for span in (turn.llm, turn.root):
                if span is not None and not span.finished:
                    span.finish(finish_time=max(span.start_ns, now) / 1e9)
            self.last_finalized_id = max(self.last_finalized_id, turn.event_id)
            for call_id in turn.tool_calls:
                self.tool_turns.pop(call_id, None)
            turn.input_audio.data.clear()
            turn.output_audio.data.clear()

    def _annotate_turn(self, turn: Turn, now: int) -> None:
        if turn.root is None or turn.llm is None:
            return
        if self.closing:
            turn.output_audio.trim(now, self.output_rate)
        text = turn.agent_text or turn.text_parts
        input_message, budget = self._message(
            "user",
            turn.user_text,
            turn.input_audio,
            self.input_rate,
            LLMOBS_AUDIO_INLINE_MAX_BYTES,
        )
        output_message, _ = self._message("assistant", text, turn.output_audio, self.output_rate, budget)
        if turn.tool_results:
            input_message["tool_results"] = list(turn.tool_results.values())
        if turn.tool_calls:
            output_message["tool_calls"] = list(turn.tool_calls.values())
        metadata = self.metadata(turn)
        self.integration.annotate_voice_span(
            turn.llm,
            "llm",
            "elevenlabs response",
            self.session_id,
            metadata,
            parent=turn.root,
            input_messages=[input_message],
            output_messages=[output_message],
        )
        self.integration.annotate_voice_span(
            turn.root,
            "workflow",
            "elevenlabs audio turn",
            self.session_id,
            metadata,
            input_value=turn.user_text or None,
            output_value=text or None,
        )
        self._phase("user speech", turn, turn.input_audio, self.input_rate, turn.user_text)
        self._phase("agent speech", turn, turn.output_audio, self.output_rate, text)
        if self.error:
            turn.root.error = turn.llm.error = 1
        turn.llm.finish(finish_time=max(turn.llm.start_ns, turn.response_ns) / 1e9)
        end = max(
            turn.root.start_ns,
            turn.response_ns,
            turn.tool_end_ns,
            turn.input_audio.end_ns(self.input_rate) or 0,
            turn.output_audio.end_ns(self.output_rate) or 0,
        )
        turn.root.finish(finish_time=end / 1e9)

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closing = True
            now = time.time_ns()
            if self.turns:
                last = next(reversed(self.turns.values()))
                # Retain the final microphone window without inventing an extra response.
                last.input_audio.extend(self.pending, self.input_rate)
                self._bound_audio(last)
            self.pending = AudioBuffer()
            self.early_audio.clear()
            self.pending_text = ""
            for turn in self.turns.values():
                self._finish_turn(turn, now)
            self.turns.clear()
            self.tool_turns.clear()
            self.closed = True
