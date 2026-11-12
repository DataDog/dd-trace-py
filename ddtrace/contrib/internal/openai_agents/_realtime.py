"""Observe the Agents SDK's raw WebSocket boundary without consuming its iterator.

The SDK's provider tracing is independent of this observer. One record belongs to
one connection attempt; receive-loop completion owns transport finalization.
"""

import asyncio
import base64
from collections import OrderedDict
import contextvars
from dataclasses import dataclass
from dataclasses import field
import json
import math
import sys
import time
from types import ModuleType
from types import SimpleNamespace
from typing import Any
from typing import Callable
from typing import Optional
from urllib.parse import parse_qs
from urllib.parse import urlsplit
import weakref

import agents
import openai

from ddtrace.contrib.internal.openai._realtime import _AUDIO_ACCUM_MAX_BYTES
from ddtrace.contrib.internal.openai._realtime import _bytes_per_second
from ddtrace.contrib.internal.openai._realtime import _decoded_b64_len
from ddtrace.contrib.internal.openai._realtime import _encoded_realtime_audio_size
from ddtrace.contrib.internal.openai._realtime import _get_attr
from ddtrace.contrib.internal.openai._realtime import _RealtimeState
from ddtrace.contrib.internal.openai._realtime import _ResponseTurn
from ddtrace.contrib.internal.openai._realtime import _segment_duration_ns
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.native import uuid4
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)
_MODULE = "agents.realtime.openai_realtime"
_MAX_TURNS = 16
_MAX_ITEMS = 256
# Encoded strings are retained, so account their encoded size, including padding.
_MAX_CACHE_BYTES = 12 * 1024 * 1024
_AUDIO_WAIT = 5.0
_TRANSCRIPT_WAIT = 2.5
_PLAYBACK_WAIT = 120.0
_MAX_ATTEMPTS = 2
_MAX_RETRIEVAL_TASKS = 2
_RETRIEVAL_EVENT_OVERHEAD = 16 * 1024
_MISSING = object()


def _audio_speed(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0.25 <= value <= 1.5:
        return float(value)
    return None


@dataclass
class _Item:
    id: str
    side: Any
    entry: Optional["_Playback"]
    mime: str
    rate: int
    expected: set[int] = field(default_factory=set)
    parts: dict[int, str] = field(default_factory=dict)
    transcripts: dict[int, str] = field(default_factory=dict)
    caps: dict[int, float] = field(default_factory=dict)
    last_index: int = sys.maxsize
    complete: bool = False
    attempts: int = 0
    pending: int = 0
    failed: bool = False
    wanted: bool = False
    generation_done: bool = False
    input_audio: bool = False
    raw_byte_bound: Optional[int] = None
    audio_start_ms: Optional[float] = None
    audio_end_ms: Optional[float] = None
    reply_revision: int = 0
    final_input_transcript: Optional[str] = None


@dataclass
class _InputTiming:
    start_ms: Optional[float] = None
    end_ms: Optional[float] = None
    start_ns: Optional[int] = None
    end_ns: Optional[int] = None


@dataclass
class _ApplicationRetrieval:
    item: _Item
    sent: bool = False
    failed: bool = False


@dataclass
class _Playback:
    id: str
    turn: _ResponseTurn
    mime: str
    rate: int
    # An unspecified speed is bounded at the slowest documented playback rate.
    speed: Optional[float] = 0.25
    audio_byte_bound: Optional[int] = None
    items: list[str] = field(default_factory=list)
    has_audio: bool = False
    done: bool = False
    start_ns: Optional[int] = None
    end_ns: Optional[int] = None
    stopped: bool = False
    cleared: bool = False
    truncated: bool = False
    derived: bool = False
    timer: Optional[asyncio.TimerHandle] = None
    phase: str = ""
    window: Optional[list["_Playback"]] = None


class _AgentsRealtimeState(_RealtimeState):
    """Reuse OpenAI extraction; keep Agents async waits on its own event loop."""

    def __init__(self, integration: Any, url: str, model: Optional[str], owner: Any) -> None:
        super().__init__(integration, SimpleNamespace(_base_url=url), model)
        call_id = parse_qs(urlsplit(url).query).get("call_id", [None])[0]
        self.sideband = bool(call_id)
        if call_id:
            self._session_id = call_id
        self.owner = weakref.ref(owner)
        self.context = contextvars.copy_context()
        self.loop = asyncio.get_running_loop()
        self._emitting: Optional[_Playback] = None
        self._preparing_response: Optional[_Playback] = None
        self.entries: OrderedDict[str, _Playback] = OrderedDict()
        self.items: OrderedDict[str, _Item] = OrderedDict()
        self.input_offsets: OrderedDict[str, _InputTiming] = OrderedDict()
        self.requests: dict[str, _Item] = {}
        self.application_requests: OrderedDict[str, _ApplicationRetrieval] = OrderedDict()
        self._request_prefix = "ddtrace_realtime_audio_" + uuid4() + "_"
        self._request_sequence = 0
        self.transcript_timers: dict[int, asyncio.TimerHandle] = {}
        self.tasks: set[asyncio.Task[None]] = set()
        self.retrieve_enabled = asbool(env.get("DD_OPENAI_REALTIME_SIDEBAND_AUDIO_RETRIEVAL_ENABLED", "true"))
        self.active: Optional[_Playback] = None
        self._vad_silence_ns = 0
        self._output_audio_speed: Optional[float] = 0.25
        self.handling = 0
        self.intents: dict[str, int] = {}
        self._input_audio_mime = self._output_audio_mime = "audio/pcm"

    def on_client_event(self, event: Any) -> None:
        if self.sideband and isinstance(event, dict) and event.get("type") == "conversation.item.truncate":
            return
        super().on_client_event(event)

    def _start_response(self, response_id: Optional[str]) -> None:
        if response_id is None or response_id in self.entries:
            return
        while len(self.entries) >= _MAX_TURNS:
            self._release(next(iter(self.entries.values())), force=True)
        # Unlike a request/response client, a sideband can receive another generation
        # before playback or transcription for the preceding one has completed.
        awaiting, playing = self._awaiting, self._playing
        self._awaiting, self._playing = [], []
        try:
            super()._start_response(response_id)
        finally:
            self._awaiting, self._playing = awaiting, playing
        turn = self._responses.get(response_id)
        if turn is not None:
            self.entries[response_id] = _Playback(
                response_id, turn, self._output_audio_mime, self._output_audio_rate, speed=self._output_audio_speed
            )
            item = self.items.get(turn.input.item_id or "")
            if item:
                item.entry = self.entries[response_id]

    def _finish_response(self, response_id: Optional[str], response: Any) -> None:
        entry = self.entries.get(response_id or "")
        if entry is None:
            super()._finish_response(response_id, response)
            return
        if isinstance(response, dict):
            response = dict(response)
            usage = response.get("usage")
            if isinstance(usage, dict):
                response["usage"] = _usage_snapshot(usage)
        # Populate generation fields before cache pressure can evict this turn.
        # Extraction remains with the shared lifecycle; emission waits for our
        # item admission and playback/transcription decisions below.
        self._preparing_response = entry
        try:
            super()._finish_response(response_id, response)
        finally:
            self._preparing_response = None
        entry.done = True
        if self.sideband and isinstance(response, dict):
            audio_output = _get_attr(_get_attr(response, "audio", None), "output", None)
            speed = _get_attr(audio_output, "speed", _MISSING)
            if speed is not _MISSING and speed is not None:
                entry.speed = _audio_speed(speed)
            details = _get_attr(response.get("usage"), "output_token_details", None)
            audio_tokens = _get_attr(details, "audio_tokens", None)
            byte_rate = _bytes_per_second(entry.mime, entry.rate)
            if type(audio_tokens) is int and 0 < audio_tokens <= _AUDIO_ACCUM_MAX_BYTES and byte_rate and entry.speed:
                # Realtime assistant audio uses one token per 50 ms. Include a
                # final-frame margin and the speed applied after generation.
                # This bounds the whole response, not each item's allocation.
                # https://developers.openai.com/api/docs/guides/voice-latency-cost?voice-api=realtime
                entry.audio_byte_bound = math.ceil((audio_tokens + 1) * byte_rate / (20 * entry.speed))
                if entry.mime == "audio/pcm":
                    entry.audio_byte_bound += entry.audio_byte_bound % 2
            output = response.get("output") or []
            transcripts, texts = [], []
            for data in output:
                if not isinstance(data, dict) or data.get("type") != "message":
                    continue
                for part in data.get("content") or []:
                    if isinstance(part, dict):
                        if isinstance(part.get("transcript"), str):
                            transcripts.append(part["transcript"])
                        if isinstance(part.get("text"), str):
                            texts.append(part["text"])
            if transcripts:
                entry.turn.transcript = "".join(transcripts)
            if texts:
                entry.turn.text = "".join(texts)
            self._preparing_response = entry
            try:
                for data in output:
                    self._bind_output(entry, data)
                    if self.entries.get(entry.id) is not entry:
                        return
            finally:
                self._preparing_response = None
            ordered = [
                data["id"]
                for data in output
                if isinstance(data, dict) and data.get("type") == "message" and data.get("id") in self.items
            ]
            if ordered:
                for item_id in set(entry.items) - set(ordered):
                    self._forget_item(item_id)
                entry.items = ordered
                self._rebuild(self.items[ordered[0]])
        if self.entries.get(entry.id) is not entry:
            return
        if entry.turn in self._awaiting:
            self.transcript_timers[id(entry.turn)] = self.loop.call_later(
                _TRANSCRIPT_WAIT, self._transcript_timeout, entry, context=self.context.copy()
            )
        else:
            self._finalize_turn(entry.turn)

    def _transcript_timeout(self, entry: _Playback) -> None:
        self.transcript_timers.pop(id(entry.turn), None)
        if entry.turn in self._awaiting:
            self._awaiting.remove(entry.turn)
        self._check(entry)

    def _finalize_turn(self, turn: _ResponseTurn, force: bool = False) -> None:
        entry = next((e for e in self.entries.values() if e.turn is turn), None)
        if entry is None or self._preparing_response is entry:
            return
        if not force and self.sideband:
            self._check(entry)
            return
        if not force and self._park_for_playback(turn):
            return
        self._release(entry, force=force)

    def _park_for_playback(self, turn: _ResponseTurn) -> bool:
        if self.sideband:
            return False
        if turn in self._playing:
            return True
        if not super()._park_for_playback(turn):
            return False
        entry = next(e for e in self.entries.values() if e.turn is turn)
        if entry.timer:
            entry.timer.cancel()
        delay = max(0.0, ((turn.playback_end_ns or time.time_ns()) - time.time_ns()) / 1e9)
        entry.timer = self.loop.call_later(delay, self._release, entry, True, context=self.context.copy())
        return True

    def _check(self, entry: Optional[_Playback]) -> None:
        if entry is None or entry.id not in self.entries or not entry.done:
            return
        if not self.sideband:
            if entry.turn not in self._awaiting and entry.turn not in self._playing:
                self._release(entry)
            return
        self._derive_window(entry)
        pending = entry.turn in self._awaiting or bool(self.handling)
        if entry.has_audio and (not entry.stopped or (entry.cleared and not entry.truncated and not entry.derived)):
            pending = True
        if entry.window is not None:
            pending = True
        owned = [i for i in self.items.values() if i.entry is entry and i.last_index >= 0]
        if any(i.pending or i.wanted or self.intents.get(i.id, 0) for i in owned):
            pending = True
        if not pending:
            self._release(entry)
            return
        queued = self.active is not None and self.active is not entry and not self.active.stopped
        phase = "playback" if entry.start_ns is not None or queued else "audio"
        if entry.stopped:
            phase = "audio"
        if entry.phase != phase:
            entry.phase = phase
            if entry.timer:
                entry.timer.cancel()
            entry.timer = self.loop.call_later(
                _PLAYBACK_WAIT if phase == "playback" else _AUDIO_WAIT,
                self._release,
                entry,
                True,
                context=self.context.copy(),
            )

    def _release(self, entry: _Playback, force: bool = False) -> None:
        if self.entries.pop(entry.id, None) is None:
            return
        if entry.timer:
            entry.timer.cancel()
        timer = self.transcript_timers.pop(id(entry.turn), None)
        if timer:
            timer.cancel()
        if entry.window:
            for other in entry.window:
                other.window = None
        if entry.turn in self._awaiting:
            self._awaiting.remove(entry.turn)
        if entry.turn in self._playing:
            self._playing.remove(entry.turn)
        self._responses.pop(entry.id, None)
        if self.sideband and (not entry.stopped or (entry.cleared and not entry.truncated and not entry.derived)):
            entry.turn.audio.clear()
            entry.turn.audio.present = entry.has_audio
            if not entry.stopped:
                entry.start_ns = entry.end_ns = None
        if not entry.turn.input.transcript and entry.turn.input.item_id is not None:
            entry.turn.input.transcript = self._input_transcripts.get(entry.turn.input.item_id, "")
        # Leave the record available only while tagging its exact server timing.
        self._emitting = entry
        onset = entry.turn.input.speech_start_ns or entry.turn.input.audio.start_ns
        if onset is not None and entry.turn.root_span is not None:
            entry.turn.root_span.start_ns = min(entry.turn.root_span.start_ns, onset)
        try:
            super()._finalize_turn(entry.turn, force=True)
        finally:
            self._emitting = None
            entry.turn.audio.clear()
            entry.turn.input.audio.clear()
            for item_id in [k for k, i in self.items.items() if i.entry is entry]:
                self._forget_item(item_id)
            entry.items.clear()
        if self.active is entry:
            self.active = None
            for other in list(self.entries.values()):
                if other.phase == "playback" and other.start_ns is None:
                    other.phase = ""
                    self._check(other)

    def _emit_phase_spans(self, turn: _ResponseTurn) -> None:
        entry = getattr(self, "_emitting", None)
        if not self.sideband or entry is None:
            super()._emit_phase_spans(turn)
            return
        # Let the shared implementation emit caller speech only. Output audio's
        # receipt/generation time is not evidence that it reached the listener.
        start = turn.audio.start_ns
        turn.audio.start_ns = None
        try:
            super()._emit_phase_spans(turn)
        finally:
            turn.audio.start_ns = start
        if entry.start_ns is not None and entry.end_ns is not None and turn.root_span is not None:
            self._emit_workflow_span(
                "createRealtimeAgentSpeech",
                "agent speech",
                turn.root_span,
                entry.start_ns,
                entry.end_ns,
                output_value=turn.transcript or turn.text or None,
            )

    def _session_metadata(self) -> dict[str, Any]:
        metadata = super()._session_metadata()
        entry = getattr(self, "_emitting", None)
        if entry and entry.derived:
            metadata["agent_speech_timing"] = "audio_duration"
        if self.sideband and entry and entry.has_audio and not entry.turn.audio.chunks:
            metadata["output_audio_capture"] = "omitted"
        return metadata

    def _turn_end_ns(self, turn: _ResponseTurn) -> Optional[int]:
        if not self.sideband:
            end = super()._turn_end_ns(turn)
            ends = [end] if end is not None else []
        else:
            entry = getattr(self, "_emitting", None)
            ends = [
                v
                for v in (turn.response_done_ns, turn.input.speech_end_ns, entry.end_ns if entry else None)
                if v is not None
            ]
        # The shared finalizer finishes generation first. A connection ending
        # mid-response must include that observed work after caller speech ends.
        duration = getattr(turn.span, "duration_ns", None)
        if isinstance(duration, int):
            ends.append(turn.span.start_ns + duration)
        return max(ends) if ends else None

    def _forget_item(self, item_id: str) -> None:
        item = self.items.pop(item_id, None)
        if item:
            item.parts.clear()
            for request_id in [k for k, v in self.requests.items() if v is item]:
                self.requests.pop(request_id, None)
            for request_id in [k for k, v in self.application_requests.items() if v.item is item]:
                self.application_requests.pop(request_id, None)
        self.intents.pop(item_id, None)

    def _bind(self, item_id: str, side: Any, entry: Optional[_Playback], mime: str, rate: int) -> Optional[_Item]:
        if entry is not None and self.entries.get(entry.id) is not entry:
            return None
        item = self.items.get(item_id)
        if item is None:
            while len(self.items) >= _MAX_ITEMS:
                old_id, old = next(iter(self.items.items()))
                if old.entry is not None and self.entries.get(old.entry.id) is old.entry:
                    self._release(old.entry, force=True)
                else:
                    old.side.audio.clear()
                # Even an already-retired owner must make admission advance.
                self._forget_item(old_id)
                if entry is not None and self.entries.get(entry.id) is not entry:
                    return None
            item = _Item(item_id, side, entry, mime, rate)
            item.input_audio = entry is None or side is entry.turn.input
            self.items[item_id] = item
        return item

    def _update_session_config(self, session: Any) -> None:
        super()._update_session_config(session)
        audio = _get_attr(session, "audio", None)
        speed = _get_attr(_get_attr(audio, "output", None), "speed", _MISSING)
        if speed is not _MISSING and speed is not None:
            self._output_audio_speed = _audio_speed(speed)
        detection = _get_attr(_get_attr(audio, "input", None), "turn_detection", _MISSING)
        if detection is _MISSING:
            detection = _get_attr(session, "turn_detection", _MISSING)
        if detection is _MISSING:
            return
        silence = _get_attr(detection, "silence_duration_ms", 0)
        self._vad_silence_ns = (
            int(silence * 1_000_000)
            if _get_attr(detection, "type", None) == "server_vad"
            and isinstance(silence, (int, float))
            and not isinstance(silence, bool)
            and 0 <= silence < 1e12
            else 0
        )

    def _remember_input(self, event: dict[str, Any]) -> None:
        item_id = event.get("item_id")
        if not isinstance(item_id, str):
            return
        event_type = event.get("type")
        if event_type != "input_audio_buffer.committed":
            timing = self.input_offsets.setdefault(item_id, _InputTiming())
            while len(self.input_offsets) > _MAX_ITEMS:
                self.input_offsets.popitem(last=False)
            now = time.time_ns()
            key = "audio_start_ms" if event_type == "input_audio_buffer.speech_started" else "audio_end_ms"
            value = event.get(key)
            offset = (
                value if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 1e12 else None
            )
            if event_type == "input_audio_buffer.speech_started":
                if timing.start_ns is None:
                    timing.start_ns, timing.start_ms = now, offset
            else:
                if timing.end_ns is not None and (
                    offset is None or (timing.end_ms is not None and offset <= timing.end_ms)
                ):
                    return
                if (
                    timing.end_ms is None
                    and timing.start_ns is not None
                    and timing.start_ms is not None
                    and offset is not None
                    and offset >= timing.start_ms
                ):
                    # A sideband observes no microphone frames. Backdate from the
                    # full VAD interval, including its pre-detection audio, so an
                    # interrupted reply and the caller can overlap on the timeline.
                    timing.start_ns = min(timing.start_ns, max(0, now - int((offset - timing.start_ms) * 1e6)))
                if offset is not None:
                    timing.end_ms = offset
                timing.end_ns = max(timing.end_ns or 0, timing.start_ns or 0, now - self._vad_silence_ns)
            return
        committed_timing = self.input_offsets.pop(item_id) if item_id in self.input_offsets else None
        # VAD offsets precede commit and may straddle an unrelated response.
        # Bind media and its timing only once commit identifies their owner.
        item = self._bind(
            item_id,
            self._pending_input,
            None,
            self._pending_input.audio.mime or self._input_audio_mime,
            self._pending_input.audio.rate or self._input_audio_rate,
        )
        if item is None:
            return
        item.expected.add(0)
        item.side.audio.present = True
        if committed_timing is not None:
            if item.audio_start_ms is None:
                item.audio_start_ms = committed_timing.start_ms
            if committed_timing.end_ms is not None:
                item.audio_end_ms = committed_timing.end_ms
            if committed_timing.start_ns is not None:
                item.side.speech_start_ns = committed_timing.start_ns
            if committed_timing.end_ns is not None:
                item.side.speech_end_ns = max(committed_timing.end_ns, committed_timing.start_ns or 0)
        item.generation_done = True
        rate = _bytes_per_second(item.mime, item.rate)
        if (
            rate
            and item.audio_start_ms is not None
            and item.audio_end_ms is not None
            and item.audio_end_ms >= item.audio_start_ms
        ):
            item.raw_byte_bound = math.ceil((item.audio_end_ms - item.audio_start_ms) * rate / 1000)
        elif item.generation_done and item.side.audio.total_decoded_bytes:
            item.raw_byte_bound = item.side.audio.total_decoded_bytes

    def _known_audio_bytes(self, item: _Item) -> Optional[int]:
        if item.raw_byte_bound is not None:
            return item.raw_byte_bound
        rate = _bytes_per_second(item.mime, item.rate)
        total = 0
        for index in item.expected:
            if not 0 <= index <= item.last_index:
                continue
            if index in item.caps and rate:
                total += math.ceil(item.caps[index] * rate / 1000)
            elif item.complete and index in item.parts:
                total += _decoded_b64_len(item.parts[index])
            else:
                if not item.input_audio and item.entry is not None:
                    return item.entry.audio_byte_bound
                return None
        return total if item.expected else None

    def _request_allowed(self, item: _Item) -> bool:
        bound = self._known_audio_bytes(item)
        if bound is None or bound <= 0:
            return False
        owned = (
            [self.items[k] for k in item.entry.items if k in self.items]
            if not item.input_audio and item.entry
            else [item]
        )
        logical_bytes = 0
        for other in owned:
            if not other.expected or other.last_index < 0:
                continue
            other_bound = self._known_audio_bytes(other)
            if other_bound is None or (other.mime, other.rate) != (item.mime, item.rate):
                return False
            logical_bytes += other_bound
        if not item.input_audio and item.entry and item.entry.audio_byte_bound is not None:
            logical_bytes = min(logical_bytes, item.entry.audio_byte_bound)
        encoded = _encoded_realtime_audio_size(logical_bytes, item.mime)
        if encoded is None or encoded > _AUDIO_ACCUM_MAX_BYTES or bound > _AUDIO_ACCUM_MAX_BYTES:
            return False
        owner = self.owner()
        socket = getattr(owner, "_websocket", None)
        protocol = getattr(socket, "protocol", None)
        limit = getattr(protocol, "max_message_size", _MISSING)
        if limit is _MISSING:
            limit = getattr(protocol, "max_size", _MISSING)
        if limit is _MISSING:
            settings = getattr(owner, "_transport_config", None) or {}
            limit = settings.get("max_size", 8 * 1024 * 1024)
        if limit is None:
            return True
        if type(limit) is not int or limit <= 0:
            return False
        metadata = _RETRIEVAL_EVENT_OVERHEAD + len(json.dumps(item.id))
        metadata += sum(len(json.dumps(t)) for t in item.transcripts.values())
        # Include possible JSON slash escaping as well as base64 expansion.
        wire_bytes = 2 * (4 * ((bound + 2) // 3)) + metadata
        return wire_bytes <= limit

    def _want_item(self, item: _Item) -> None:
        item.wanted = bool(
            self.retrieve_enabled
            and item.generation_done
            and item.expected
            and item.last_index >= 0
            and not item.complete
            and not item.failed
            and self._request_allowed(item)
        )

    def _is_own_request(self, request_id: Any) -> bool:
        if not isinstance(request_id, str) or not request_id.startswith(self._request_prefix):
            return False
        suffix = request_id[len(self._request_prefix) :]
        return bool(
            suffix
            and len(suffix) <= len(str(self._request_sequence))
            and suffix.isascii()
            and suffix.isdecimal()
            and not suffix.startswith("0")
            and 0 < int(suffix) <= self._request_sequence
        )

    def _bind_output(self, entry: _Playback, data: Any) -> None:
        if not isinstance(data, dict) or data.get("type") != "message":
            return
        item_id = data.get("id")
        if not isinstance(item_id, str) or not item_id:
            return
        item = self._bind(item_id, entry.turn, entry, entry.mime, entry.rate)
        if item is None:
            return
        if data.get("status") in ("completed", "incomplete"):
            item.generation_done = True
        if item_id not in entry.items:
            entry.items.append(item_id)
        for index, part in enumerate(data.get("content") or []):
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("audio", "output_audio"):
                item.expected.add(index)
                entry.has_audio = True
                entry.turn.audio.present = True
            text = part.get("transcript")
            if not isinstance(text, str):
                text = part.get("text")
            if isinstance(text, str):
                item.transcripts[index] = text
        self._want_item(item)
        if self._preparing_response is not entry:
            self._rebuild(item)

    def _cache_bytes(self) -> int:
        retained = {id(part): part for item in self.items.values() for part in item.parts.values()}
        for entry in self.entries.values():
            for audio in (entry.turn.audio, entry.turn.input.audio):
                retained.update((id(chunk), chunk) for chunk in audio.chunks)
        retained.update((id(chunk), chunk) for chunk in self._pending_input.audio.chunks)
        return sum(len(part) for part in retained.values())

    def _retrieved(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        item = self.items.get(data.get("id", ""))
        if item is None:
            return
        item.reply_revision += 1
        item.pending = 0
        item.wanted = False
        for request_id in [k for k, v in self.application_requests.items() if v.item is item]:
            self.application_requests.pop(request_id, None)
        for request_id in [k for k, v in self.requests.items() if v is item]:
            self.requests.pop(request_id or "", None)
        parts: dict[int, str] = {}
        expected: set[int] = set()
        transcripts: dict[int, str] = {}
        for index, part in enumerate(data.get("content") or []):
            if not isinstance(part, dict):
                continue
            transcript = part.get("transcript")
            if not isinstance(transcript, str):
                transcript = part.get("text")
            if isinstance(transcript, str):
                transcripts[index] = transcript
            if part.get("type") in ("audio", "output_audio", "input_audio"):
                expected.add(index)
                audio = part.get("audio")
                if isinstance(audio, str) and audio:
                    parts[index] = audio
        item.expected.update(expected)
        status = data.get("status")
        stable = status == "completed" or (status == "incomplete" and item.generation_done)
        if stable and (transcripts or (item.entry and item.entry.truncated)):
            item.transcripts = transcripts
            if item.entry and not item.input_audio and item.entry.truncated:
                item.entry.turn.transcript = ""
        else:
            item.transcripts.update(transcripts)
        # Release the accumulator and old snapshot before accounting a replacement.
        item.side.audio.clear()
        item.parts.clear()
        incoming = sum(len(p) for p in parts.values())
        decoded = sum(_decoded_b64_len(p) for p in parts.values())
        valid = decoded <= _AUDIO_ACCUM_MAX_BYTES and self._cache_bytes() + incoming <= _MAX_CACHE_BYTES
        if valid:
            # Admission precedes decoding; malformed input never becomes a playable clip.
            try:
                for part in parts.values():
                    base64.b64decode(part, validate=True)
            except (ValueError, TypeError):
                valid = False
        relevant = {index for index in item.expected if 0 <= index <= item.last_index}
        item.complete = stable and bool(relevant) and relevant.issubset(parts) and valid
        if item.complete:
            item.parts = parts
        if valid:
            self._want_item(item)
        self._rebuild(item)
        if item.input_audio:
            transcript = item.final_input_transcript
            if transcript is None:
                transcript = "".join(item.transcripts[k] for k in sorted(item.transcripts))
            if item.side.item_id == item.id:
                item.side.transcript = transcript
            if transcript:
                super().on_server_event(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "item_id": item.id,
                        "transcript": transcript,
                    }
                )
        self._check(item.entry)

    def _rebuild(self, item: _Item) -> None:
        entry = item.entry
        owned = [self.items[k] for k in entry.items if k in self.items] if entry and item.side is entry.turn else [item]
        audio = item.side.audio
        audio.clear()
        audio.present = any(i.expected for i in owned)
        if entry and item.side is entry.turn:
            texts = [
                current.transcripts[k]
                for current in owned
                for k in sorted(current.transcripts)
                if 0 <= k <= current.last_index
            ]
            if texts:
                entry.turn.transcript = "".join(texts)

        selected: list[tuple[_Item, int, str]] = []
        complete = bool(owned)
        for current in owned:
            if current.last_index < 0:
                continue
            for index in sorted(current.expected):
                if index > current.last_index:
                    continue
                part = current.parts.get(index)
                if part is None or not current.complete:
                    complete = False
                elif (current.mime, current.rate) != (item.mime, item.rate):
                    complete = False
                else:
                    selected.append((current, index, part))
        total = sum(_decoded_b64_len(p) for _, _, p in selected)
        if not complete or total > _AUDIO_ACCUM_MAX_BYTES:
            audio.oversize = total > _AUDIO_ACCUM_MAX_BYTES
            return
        for current, index, part in selected:
            cap = current.caps.get(index)
            if cap is not None:
                rate = _bytes_per_second(current.mime, current.rate)
                if rate is None:
                    audio.clear()
                    audio.present = True
                    return
                count = int(cap * rate / 1000)
                if current.mime == "audio/pcm":
                    count -= count % 2
                # bytes slices own their backing allocation; no hidden memoryview tail.
                raw = base64.b64decode(part, validate=True)[:count]
                part = base64.b64encode(raw).decode("ascii")
            if self._cache_bytes() + (0 if part is current.parts.get(index) else len(part)) > _MAX_CACHE_BYTES:
                audio.clear()
                audio.present = True
                return
            audio.append(part, current.mime, current.rate)
        if item.input_audio and item.side.speech_start_ns is None:
            end = item.side.speech_end_ns
            duration = _segment_duration_ns(audio.total_decoded_bytes, audio.mime, audio.rate)
            audio.start_ns = end - duration if end is not None and duration is not None else None

    def _derive_window(self, entry: _Playback) -> None:
        window = entry.window
        if not window or not all(e.done for e in window):
            return
        if any(
            i.pending or i.wanted or self.intents.get(i.id, 0)
            for i in self.items.values()
            if any(i.entry is e for e in window)
        ):
            return
        # The clear precedes its truncation acknowledgement and stored prefix.
        # Keep the window owned until that evidence can resolve its final clip.
        if window[-1].cleared and not window[-1].truncated:
            return
        for member in window:
            member.window = None
        start, end = window[0].start_ns, window[-1].end_ns
        if start is None or end is None:
            return
        if any(e.truncated or e.cleared for e in window):
            tail_known = False
            last = window[-1]
            items = [self.items[k] for k in last.items if k in self.items and self.items[k].expected]
            audio = last.turn.audio
            duration = _segment_duration_ns(audio.total_decoded_bytes, audio.mime, audio.rate)
            if len(items) == 1 and items[0].complete and items[0].expected == {0}:
                cap = items[0].caps.get(0)
                if (
                    cap is not None
                    and cap > 0
                    and duration is not None
                    and duration > 0
                    and duration == int(cap * 1_000_000)
                    and not audio.oversize
                ):
                    onset = end - duration
                    done = last.turn.response_done_ns
                    # A complete, single-item played prefix fixes this suffix's
                    # duration. If generation finished before its inferred start,
                    # it was available for uninterrupted playback through clear.
                    # Earlier clips still lack a boundary and remain unplaced.
                    if onset >= start and done is not None and done <= onset:
                        last.start_ns = onset
                        last.derived = True
                        tail_known = True
            if not tail_known or any(member.truncated for member in window[:-1]):
                return
        durations = []
        for member in window:
            audio = member.turn.audio
            duration = _segment_duration_ns(audio.total_decoded_bytes, audio.mime, audio.rate)
            if member.has_audio and (not audio.chunks or audio.oversize or not duration):
                return
            durations.append(duration or 0)
        if sum(durations) > end - start + 1_000_000:
            return
        cursor = end
        for index in range(len(window) - 1, -1, -1):
            member = window[index]
            if not durations[index]:
                continue
            member.start_ns = start if index == 0 else max(start, cursor - durations[index])
            member.end_ns = min(cursor, member.start_ns + durations[index]) if index == 0 else cursor
            member.derived = len(window) > 1
            cursor = member.start_ns

    def observe_server(self, event: Any) -> bool:
        if self._closed or not isinstance(event, dict):
            return False
        event_type = event.get("type")
        if event_type == "error":
            request_id = (event.get("error") or {}).get("event_id")
            if self._is_own_request(request_id):
                item = self.requests.pop(str(request_id), None)
                if item:
                    item.pending = max(0, item.pending - 1)
                    item.failed = True
                    item.wanted = False
                    self._check(item.entry)
                return True
            application = self.application_requests.pop(request_id, None) if isinstance(request_id, str) else None
            if application:
                application.failed = True
                if application.sent:
                    application.item.pending = max(0, application.item.pending - 1)
                self._want_item(application.item)
                self._check(application.item.entry)
        if event_type in ("response.output_item.added", "response.output_item.done"):
            tool = event.get("item")
            if isinstance(tool, dict) and tool.get("type") == "function_call":
                call_id, name = tool.get("call_id"), tool.get("name")
                if isinstance(call_id, str) and isinstance(name, str):
                    self._tool_call_names[call_id] = name
        if self.sideband:
            if event_type in ("input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped"):
                self._remember_input(event)
                return False
            if event_type in ("response.output_item.added", "response.output_item.done"):
                entry = self.entries.get(event.get("response_id") or "")
                if entry:
                    self._bind_output(entry, event.get("item"))
                    item = self.items.get((event.get("item") or {}).get("id", ""))
                    if item and event_type == "response.output_item.done":
                        item.generation_done = True
                        self._want_item(item)
            elif event_type == "conversation.item.retrieved":
                self._retrieved(event.get("item"))
            elif event_type == "conversation.item.truncated":
                item = self.items.get(event.get("item_id") or "")
                end_ms, index = event.get("audio_end_ms"), event.get("content_index", 0)
                if (
                    item
                    and type(index) is int
                    and index >= 0
                    and isinstance(end_ms, (int, float))
                    and not isinstance(end_ms, bool)
                    and 0 <= end_ms < 1e12
                ):
                    item.caps[index] = min(item.caps.get(index, end_ms), end_ms)
                    item.last_index = min(item.last_index, index)
                    if item.complete and item.entry and not item.input_audio:
                        rate = _bytes_per_second(item.mime, item.rate)
                        part = item.parts.get(index)
                        cut = rate is None or part is None or int(end_ms * rate / 1000) < _decoded_b64_len(part)
                        for part_index in list(item.transcripts):
                            if part_index > index or (part_index == index and cut):
                                item.transcripts.pop(part_index, None)
                        item.entry.turn.transcript = ""
                    if item.entry:
                        item.entry.truncated = True
                        after = False
                        for item_id in item.entry.items:
                            other = self.items.get(item_id)
                            if other and after:
                                other.last_index = -1
                                other.parts.clear()
                                other.wanted = False
                            after |= item_id == item.id
                    self._rebuild(item)
                    self._want_item(item)
                    self._check(item.entry)
                # The shared truncation path uses arrival-order byte offsets; the
                # sideband path above uses stored item/content order instead.
                return False
            elif event_type == "output_audio_buffer.started":
                entry = self.entries.get(event.get("response_id") or "")
                if entry:
                    if self.active and self.active is not entry:
                        self.active.end_ns = time.time_ns()
                        self.active.stopped = True
                    entry.start_ns = time.time_ns()
                    entry.has_audio = True
                    self.active = entry
            elif event_type in ("output_audio_buffer.stopped", "output_audio_buffer.cleared"):
                last = self.entries.get(event.get("response_id") or "") or self.active
                if last:
                    now = time.time_ns()
                    window = []
                    collecting = self.active is None
                    for entry in self.entries.values():
                        collecting |= entry is self.active
                        if collecting:
                            window.append(entry)
                            entry.stopped = True
                            entry.cleared |= event_type.endswith(".cleared")
                        if entry is last:
                            break
                    last.end_ns = now
                    if len(window) > 1:
                        for entry in window:
                            entry.window = window
                    self.active = None
        if event_type == "input_audio_buffer.cleared":
            self.input_offsets.clear()
        super().on_server_event(event)
        if self.sideband and event_type == "input_audio_buffer.committed":
            self._remember_input(event)
        if event_type == "response.created":
            response = event.get("response") or {}
            entry = self.entries.get(response.get("id", ""))
            audio = response.get("audio") or {}
            output = audio.get("output") or {}
            audio_format = output.get("format") or response.get("output_audio_format")
            if entry and output.get("speed") is not None:
                entry.speed = _audio_speed(output["speed"])
            if entry and audio_format:
                entry.mime = self._integration.realtime_audio_format(audio_format)
                rate = audio_format.get("rate") if isinstance(audio_format, dict) else None
                if type(rate) is int and rate > 0:
                    entry.rate = rate
        if self.sideband and event_type in (
            "conversation.item.input_audio_transcription.completed",
            "conversation.item.input_audio_transcription.failed",
        ):
            item = self.items.get(event.get("item_id") or "")
            if item:
                transcript = event.get("transcript")
                if isinstance(transcript, str):
                    item.transcripts[event.get("content_index", 0)] = transcript
                    if item.input_audio:
                        item.final_input_transcript = transcript
                        if item.side.item_id == item.id:
                            item.side.transcript = transcript
                self._want_item(item)
        # Bound orphan transcription and tool-name maps too; entries own the data
        # they still need. No connection-wide history grows with the conversation.
        for mapping in (self._input_transcripts, self._tool_call_names):
            while len(mapping) > _MAX_ITEMS:
                mapping.pop(next(iter(mapping)))
        for entry in list(self.entries.values()):
            self._check(entry)
        return False

    def after_handler(self) -> None:
        if self.handling or self._closed:
            return
        if not self.sideband:
            for entry in list(self.entries.values()):
                self._check(entry)
            return
        for item in list(self.items.values()):
            if len(self.tasks) >= _MAX_RETRIEVAL_TASKS:
                break
            if not item.wanted or item.pending or self.intents.get(item.id, 0):
                continue
            if (
                not self.retrieve_enabled
                or item.failed
                or item.attempts >= _MAX_ATTEMPTS
                or not self._request_allowed(item)
            ):
                item.wanted = False
                self._check(item.entry)
                continue
            item.wanted = False
            item.pending += 1
            item.attempts += 1
            task = self.context.copy().run(self.loop.create_task, self._retrieve(item))
            self.tasks.add(task)
            task.add_done_callback(self._retrieval_done)
        for entry in list(self.entries.values()):
            self._check(entry)

    def _retrieval_done(self, task: asyncio.Task[None]) -> None:
        self.tasks.discard(task)
        # Sends already inside websockets flow control are not cancelled on
        # eviction: cancellation cannot retract their frames. The fixed number
        # of task slots bounds those retired sends until they drain or close.
        _after_handler(self)

    async def _retrieve(self, item: _Item) -> None:
        owner = self.owner()
        request_id = ""
        try:
            if owner is None or self._closed or self.items.get(item.id) is not item:
                return
            # The SDK's awaited raw handler has completed before this task is
            # scheduled, so automatic retrievals have already claimed their item.
            if self.intents.get(item.id, 0) or item.pending > 1 or item.complete or item.failed:
                return
            self._request_sequence += 1
            request_id = self._request_prefix + str(self._request_sequence)
            self.requests[request_id] = item
            await owner._send_raw_message(_RawRetrieve(item.id, request_id))
            return
        except asyncio.CancelledError:
            self.requests.pop(request_id, None)
            item.failed = True
            raise
        except Exception:
            item.failed = True
            self.requests.pop(request_id or "", None)
            log.debug("failed to retrieve realtime audio", exc_info=True)
        finally:
            if request_id not in self.requests:
                item.pending = max(0, item.pending - 1)
            self._check(item.entry)

    def finish_session(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in self.tasks:
            task.cancel()
        self.tasks.clear()
        for entry in list(self.entries.values()):
            self._release(entry, force=True)
        for timer in self.transcript_timers.values():
            timer.cancel()
        self.transcript_timers.clear()
        for item_id in list(self.items):
            self._forget_item(item_id)
        self.requests.clear()
        self.application_requests.clear()
        self.input_offsets.clear()
        self.intents.clear()
        self._pending_input.audio.clear()
        self._responses.clear()
        self._awaiting.clear()
        self._playing.clear()
        self._input_transcripts.clear()
        self._tool_call_names.clear()
        self.active = None


class _RawRetrieve:
    """Serialize through the SDK send boundary without importing optional event models."""

    def __init__(self, item_id: str, event_id: str) -> None:
        self.type = "conversation.item.retrieve"
        self.message = {"type": self.type, "other_data": {"item_id": item_id, "event_id": event_id}}
        self.item_id = item_id
        self.event_id = event_id

    def model_dump_json(self, **kwargs: Any) -> str:
        return json.dumps({"type": self.type, "item_id": self.item_id, "event_id": self.event_id})


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


def _usage_snapshot(usage: dict[str, Any]) -> dict[str, Any]:
    # Only the scalar usage fields consumed by the shared extractor are retained.
    allowed = {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "input_token_details",
        "output_token_details",
        "cached_tokens_details",
        "audio_tokens",
        "text_tokens",
        "image_tokens",
        "cached_tokens",
    }
    return {
        k: _usage_snapshot(v) if isinstance(v, dict) else v
        for k, v in usage.items()
        if k in allowed and isinstance(v, (dict, int, float, str, type(None)))
    }


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
            integration = agents._datadog_integration.realtime_integration(openai)
            if integration.llmobs_enabled:
                options = args[0] if args else kwargs.get("options", {})
                settings = options.get("initial_model_settings") or {}
                call_id = options.get("call_id")
                url = options.get("url") or (
                    "wss://api.openai.com/v1/realtime?call_id=" + str(call_id)
                    if call_id
                    else "wss://api.openai.com/v1/realtime"
                )
                state = _AgentsRealtimeState(integration, url, settings.get("model_name") or instance.model, instance)
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


def _finish_session(state: _AgentsRealtimeState) -> None:
    try:
        state.context.copy().run(state.finish_session)
    except Exception:
        log.debug("failed to finalize Agents realtime observation", exc_info=True)


def _after_handler(state: _AgentsRealtimeState) -> None:
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
    item_id = observed.get("item_id") if observed and observed.get("type") == "conversation.item.retrieve" else None
    item = state.items.get(item_id) if state and isinstance(item_id, str) else None
    revision = item.reply_revision if item else None
    application = None
    request_id = observed.get("event_id") if observed else None
    if state and item and not state._is_own_request(request_id) and isinstance(request_id, str):
        application = _ApplicationRetrieval(item)
        while len(state.application_requests) >= _MAX_ITEMS:
            state.application_requests.popitem(last=False)
        state.application_requests[request_id] = application
    if state and isinstance(item_id, str):
        state.intents[item_id] = state.intents.get(item_id, 0) + 1
    successful = False
    try:
        result = await func(*args, **kwargs)
        successful = result is not False
        if state and not state._closed and successful:
            if observed is not None:
                state.context.copy().run(state.on_client_event, observed)
            if (
                item is not None
                and state.items.get(item.id) is item
                and item.reply_revision == revision
                and not state._is_own_request(request_id)
                and (application is None or not application.failed)
            ):
                item.wanted = False
                item.pending += 1
                if application:
                    application.sent = True
        return result
    finally:
        if state and application and not successful and state.application_requests.get(request_id) is application:
            state.application_requests.pop(request_id, None)
        if state and isinstance(item_id, str):
            remaining = state.intents.get(item_id, 1) - 1
            if remaining:
                state.intents[item_id] = remaining
            else:
                state.intents.pop(item_id, None)
            _after_handler(state)


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


def _patch_module(module: ModuleType) -> None:
    if not getattr(agents, "_datadog_patch", False):
        return
    cls = getattr(module, "OpenAIRealtimeWebSocketModel", None)
    if cls is None:
        return
    for name, wrapper in _WRAPS.items():
        if hasattr(cls, name) and not hasattr(getattr(cls, name), "__wrapped__"):
            wrap(module, "OpenAIRealtimeWebSocketModel." + name, wrapper)


def patch_realtime() -> None:
    ModuleWatchdog.register_module_hook(_MODULE, _patch_module)


def unpatch_realtime() -> None:
    ModuleWatchdog.unregister_module_hook(_MODULE, _patch_module)
    module = sys.modules.get(_MODULE)
    if module is not None:
        cls = getattr(module, "OpenAIRealtimeWebSocketModel", None)
        if cls:
            for name in _WRAPS:
                if hasattr(getattr(cls, name, None), "__wrapped__"):
                    unwrap(cls, name)
