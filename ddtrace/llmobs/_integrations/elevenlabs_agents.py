import base64
from collections import OrderedDict
import json
import math
import time
from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.native import uuid4
from ddtrace.internal.native._native import Context
from ddtrace.internal.threads import RLock
from ddtrace.llmobs._integrations.audio_utils import LLMOBS_AUDIO_INLINE_MAX_BYTES
from ddtrace.llmobs._integrations.audio_utils import format_audio_part_with_guard
from ddtrace.llmobs._integrations.audio_utils import pcm16_to_wav
from ddtrace.llmobs._routing import get_routing_context
from ddtrace.llmobs.types import Message
from ddtrace.llmobs.types import ToolCall
from ddtrace.llmobs.types import ToolResult


log = get_logger(__name__)


_RAW_LIMIT = LLMOBS_AUDIO_INLINE_MAX_BYTES * 3 // 4 - 128
_MAX_OPEN_TURNS = 2
_MAX_TOOLS = 64
_MAX_TOOL_RESULT_CHARS = 100_000
_MAX_RESPONSES = 64
_MAX_ACTIVITY_INTERVALS = 128
_VAD_HOLD_NS = 500_000_000
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
        self.responses: OrderedDict[str, str] = OrderedDict()
        self.completed_responses: set[str] = set()
        self.tool_end_ns = 0
        self.tool_execution_incomplete = False
        self.response_ns = now
        self.interrupted = False
        self.response_complete = False
        self.model: Optional[str] = None
        self.tool_results_truncated = False
        self.error = False
        self.root: Optional[Any] = None
        self.llm: Optional[Any] = None
        self.tool_calls: OrderedDict[str, ToolCall] = OrderedDict()
        self.tool_results: OrderedDict[str, ToolResult] = OrderedDict()

    def capture_tool_result(self, call_id: str, result: Any) -> str:
        # Share one text budget across all results in a turn, including updates
        # received through both the SDK callback and server payload events.
        remaining = _MAX_TOOL_RESULT_CHARS - sum(
            len(item["result"]) for key, item in self.tool_results.items() if key != call_id
        )
        text = str(result)
        if len(text) > remaining:
            self.tool_results_truncated = True
            text = text[: max(0, remaining)]
        self.tool_results[call_id] = ToolResult(tool_id=call_id, result=text, type="tool_result")
        return text

    def update_response(self, kind: str, data: dict[str, Any]) -> bool:
        response_id = data.get("response_id")
        key = response_id if isinstance(response_id, str) else ""
        if key not in self.responses:
            if len(self.responses) >= _MAX_RESPONSES:
                return False
            self.responses[key] = ""
        if kind == "agent_chat_response_part":
            if key in self.completed_responses:
                return False
            part = str(data.get("text") or "")
            self.responses[key] = (part if data.get("type") == "start" else self.responses[key] + part)[:100_000]
            if key and data.get("type") == "stop":
                self.completed_responses.add(key)
        else:
            field = "corrected_agent_response" if kind == "agent_response_correction" else "agent_response"
            self.responses[key] = str(data.get(field) or "")[:100_000]
            self.completed_responses.add(key)
        return True

    def response_text(self) -> str:
        return "\n".join(text for text in self.responses.values() if text)[:100_000]


class ConversationState:
    def __init__(self, integration: Any, conversation: Any, parent: Any = None) -> None:
        self.integration = integration
        self.agent_id = str(getattr(conversation, "agent_id", ""))
        self.parent = parent
        self.llmobs_parent = integration.capture_context()
        self.routing_context = get_routing_context()
        # Bootstrap fallback; initiation metadata supplies the provider conversation ID.
        self.session_id = uuid4()
        self.input_rate = 0
        self.output_rate = 0
        self.pending = AudioBuffer()
        self.pending_text = ""
        self.early_audio: list[tuple[str, int]] = []
        self.early_audio_size = 0
        self.early_audio_overflow = False
        self.turns: OrderedDict[int, Turn] = OrderedDict()
        self.tool_turns: OrderedDict[str, Turn] = OrderedDict()
        self.tool_spans: dict[str, Any] = {}
        self.last_interrupt_id = -1
        self.last_finalized_id = -1
        self.closing = False
        self.closed = False
        self._error = False
        self.lock = RLock()

    @property
    def error(self) -> bool:
        return self._error

    @error.setter
    def error(self, value: bool) -> None:
        with self.lock:
            self._error = value
            if value:
                for turn in self.turns.values():
                    if not turn.response_complete and not turn.interrupted:
                        turn.error = True

    def activate_tool(self, span: Any, context_provider: Any) -> Any:
        return self.integration.activate_tool(span, context_provider, self.routing_context)

    def metadata(self, turn: Optional[Turn] = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "agent_id": self.agent_id,
            "ttfa_eligible": False,
            "audio_timing_source": "sdk_transport",
            "audio_playback": "estimated",
        }
        if turn is not None:
            if turn.model:
                result["configured_llm"] = turn.model
            if turn.tool_results_truncated:
                result["tool_results_truncated"] = True
            result["event_id"] = str(turn.event_id)
            result["interrupted"] = turn.interrupted
            if turn.tool_execution_incomplete:
                result["tool_execution_incomplete"] = True
            if turn.input_audio.overflow or turn.output_audio.overflow:
                result["audio_omitted"] = True
        return result

    def _span(self, operation: str, parent: Any, llmobs_parent: Any) -> Any:
        return self.integration.trace(
            operation,
            submit_to_llmobs=True,
            activate=False,
            # An empty context is an explicit root; None would use the processing context.
            parent_context=parent if parent is not None else Context(),
            llmobs_parent=llmobs_parent,
            routing_context=self.routing_context,
            agent_id=self.agent_id,
        )

    def _turn(self, event_id: Optional[int], now: int) -> Optional[Turn]:
        if event_id is None or self.closed:
            return None
        if event_id in self.turns:
            return self.turns[event_id]
        # Known turns can still receive late text after interruption. Unknown stale
        # events must never take the next utterance's pending audio or activity.
        if event_id <= max(self.last_finalized_id, self.last_interrupt_id):
            return None
        while len(self.turns) >= _MAX_OPEN_TURNS:
            _, previous = self.turns.popitem(last=False)
            self._finish_turn(previous, now)
        # A newer response retires earlier generation even when completion events
        # are not subscribed. Retention still permits late transcripts and audio.
        for previous in self.turns.values():
            if previous.event_id < event_id:
                previous.response_complete = True
        turn = Turn(event_id, self.pending, now)
        self.pending = AudioBuffer()
        self.pending.activity = turn.input_audio.activity.continue_at(turn.input_audio.duration_ns(self.input_rate))
        turn.user_text = self.pending_text
        self.pending_text = ""
        turn.root = self._span("conversation.turn", self.parent, self.llmobs_parent)
        turn.root.start_ns = turn.input_audio.start_ns or now
        self.turns[event_id] = turn
        turn.llm = self._span("conversation.response", turn.root, turn.root)
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
                elif isinstance(encoded, str) and not self.early_audio_overflow:
                    self.early_audio_size += len(encoded)
                    if len(self.early_audio) < 256 and self.early_audio_size <= _RAW_LIMIT * 4 // 3:
                        self.early_audio.append((encoded, now))
                    else:
                        self.early_audio.clear()
                        self.early_audio_overflow = True
            elif event.get("type") == "user_message" and isinstance(event.get("text"), str):
                self.pending_text = (self.pending_text + "\n" + event["text"]).strip()[:100_000]
            elif event.get("type") == "client_tool_result":
                call_id = event.get("tool_call_id")
                if not isinstance(call_id, str):
                    return
                turn = self.tool_turns.get(call_id)
                if turn is not None:
                    turn.capture_tool_result(call_id, event.get("result") or "")

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
                if self.early_audio_overflow:
                    self.pending.data.clear()
                    self.pending.overflow = True
                for encoded, sent_ns in self.early_audio:
                    self.pending.append(encoded, sent_ns, self.input_rate, input_audio=True)
                self.early_audio_overflow = False
                self.early_audio.clear()
                self.early_audio_size = 0
                return
            if kind == "interruption":
                interrupt_id = _event_id(event.get("interruption_event", {}))
                if interrupt_id is not None:
                    self.last_interrupt_id = max(self.last_interrupt_id, interrupt_id)
                    for interrupted_turn in self.turns.values():
                        playback_end = interrupted_turn.output_audio.end_ns(self.output_rate) or 0
                        if interrupted_turn.event_id <= interrupt_id and (
                            not interrupted_turn.response_complete or playback_end > now
                        ):
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
            if kind == "context_usage":
                model = data.get("model")
                if isinstance(model, str):
                    turn.model = model
            elif kind == "user_transcript":
                turn.user_text = str(data.get("user_transcript") or "")
            elif kind == "audio":
                turn.output_audio.append(data.get("audio_base_64"), now, self.output_rate)
                self._bound_audio(turn)
            elif kind in ("agent_response", "agent_response_correction", "agent_chat_response_part"):
                # Accepted stream parts are the end-time fallback when
                # transport stops before a final response; ignored late parts must not extend it.
                if turn.update_response(kind, data):
                    turn.response_ns = now
            elif kind == "agent_response_complete":
                turn.response_ns = now
                turn.response_complete = True
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
                    turn.capture_tool_result(call_id, data.get("full_tool_result") or "")

    def _bound_audio(self, turn: Turn) -> None:
        if len(turn.input_audio.data) + len(turn.output_audio.data) > _RAW_LIMIT:
            turn.output_audio.data.clear()
            turn.output_audio.overflow = True

    def begin_tool(self, call_id: str, name: str, parameters: dict[str, Any]) -> Optional[Any]:
        with self.lock:
            turn = self.tool_turns.get(call_id)
            if self.closed or turn is None or call_id in self.tool_spans:
                return None
            span = self._span("client_tool." + name, turn.root, turn.root)
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
                turn = self.tool_turns.get(call_id)
                output = (
                    turn.capture_tool_result(call_id, result)
                    if turn is not None
                    else str(result)[:_MAX_TOOL_RESULT_CHARS]
                )
                self.integration.llmobs_set_tags(span, [], {"output_value": output, "metadata": self.metadata(turn)})
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
        span = self._span("conversation." + name.replace(" ", "_"), turn.root, turn.root)
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
        text = turn.response_text()
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
        if turn.error:
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
