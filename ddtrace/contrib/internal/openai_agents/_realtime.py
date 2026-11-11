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


@dataclass
class _Playback:
    id: str
    turn: _ResponseTurn
    mime: str
    rate: int
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
        self.entries: OrderedDict[str, _Playback] = OrderedDict()
        self.items: OrderedDict[str, _Item] = OrderedDict()
        self.requests: dict[str, _Item] = {}
        self.transcript_timers: dict[int, asyncio.TimerHandle] = {}
        self.tasks: set[asyncio.Task[None]] = set()
        self.retrieve_enabled = asbool(env.get("DD_OPENAI_REALTIME_SIDEBAND_AUDIO_RETRIEVAL_ENABLED", "true"))
        self.active: Optional[_Playback] = None
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
            self.entries[response_id] = _Playback(response_id, turn, self._output_audio_mime, self._output_audio_rate)
            item = self.items.get(turn.input.item_id or "")
            if item:
                item.entry = self.entries[response_id]

    def _finish_response(self, response_id: Optional[str], response: Any) -> None:
        entry = self.entries.get(response_id or "")
        if entry:
            entry.done = True
            if isinstance(response, dict):
                if self.sideband:
                    output = response.get("output") or []
                    for item in output:
                        self._bind_output(entry, item)
                    ordered = [
                        item["id"]
                        for item in output
                        if isinstance(item, dict) and item.get("type") == "message" and item.get("id") in self.items
                    ]
                    if ordered:
                        for item_id in set(entry.items) - set(ordered):
                            self._forget_item(item_id)
                        entry.items = ordered
                        self._rebuild(self.items[ordered[0]])
                # Usage is historical evidence; the SDK/application may mutate the
                # source dictionary after this synchronous observation returns.
                response = dict(response)
                usage = response.get("usage")
                if isinstance(usage, dict):
                    response["usage"] = _usage_snapshot(usage)
        super()._finish_response(response_id, response)
        if entry and entry.id in self.entries:
            if entry.turn in self._awaiting:
                key = id(entry.turn)
                self.transcript_timers[key] = self.loop.call_later(
                    _TRANSCRIPT_WAIT, self._transcript_timeout, entry, context=self.context.copy()
                )
            self._check(entry)

    def _transcript_timeout(self, entry: _Playback) -> None:
        self.transcript_timers.pop(id(entry.turn), None)
        if entry.turn in self._awaiting:
            self._awaiting.remove(entry.turn)
        self._check(entry)

    def _finalize_turn(self, turn: _ResponseTurn, force: bool = False) -> None:
        entry = next((e for e in self.entries.values() if e.turn is turn), None)
        if entry is None:
            return
        if not force and self.sideband:
            self._check(entry)
            return
        self._release(entry, force=force)

    def _park_for_playback(self, turn: _ResponseTurn) -> bool:
        # Agents receives server playback evidence on sidebands. Plain WebSocket
        # mode retains the existing client's duration-based playback convention.
        return False if self.sideband else super()._park_for_playback(turn)

    def _check(self, entry: Optional[_Playback]) -> None:
        if entry is None or entry.id not in self.entries or not entry.done:
            return
        if not self.sideband:
            if entry.turn not in self._awaiting:
                self._release(entry)
            return
        self._derive_window(entry)
        pending = entry.turn in self._awaiting or bool(self.handling)
        if entry.has_audio and (not entry.stopped or (entry.cleared and not entry.truncated)):
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
        if self.sideband and (not entry.stopped or (entry.cleared and not entry.truncated)):
            entry.turn.audio.clear()
            entry.turn.audio.present = entry.has_audio
            if not entry.stopped:
                entry.start_ns = entry.end_ns = None
        # Leave the record available only while tagging its exact server timing.
        self._emitting = entry
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
        return metadata

    def _turn_end_ns(self, turn: _ResponseTurn) -> Optional[int]:
        if not self.sideband:
            return super()._turn_end_ns(turn)
        entry = getattr(self, "_emitting", None)
        ends = [
            v
            for v in (turn.response_done_ns, turn.input.speech_end_ns, entry.end_ns if entry else None)
            if v is not None
        ]
        return max(ends) if ends else None

    def _forget_item(self, item_id: str) -> None:
        item = self.items.pop(item_id, None)
        if item:
            item.parts.clear()
            for request_id in [k for k, v in self.requests.items() if v is item]:
                self.requests.pop(request_id or "", None)
        self.intents.pop(item_id, None)

    def _bind(self, item_id: str, side: Any, entry: Optional[_Playback], mime: str, rate: int) -> _Item:
        item = self.items.get(item_id)
        if item is None:
            while len(self.items) >= _MAX_ITEMS:
                old_id, old = next(iter(self.items.items()))
                if old.entry:
                    self._release(old.entry, force=True)
                else:
                    old.side.audio.clear()
                    self._forget_item(old_id)
            item = _Item(item_id, side, entry, mime, rate)
            self.items[item_id] = item
        return item

    def _bind_output(self, entry: _Playback, data: Any) -> None:
        if not isinstance(data, dict) or data.get("type") != "message":
            return
        item_id = data.get("id")
        if not isinstance(item_id, str) or not item_id:
            return
        item = self._bind(item_id, entry.turn, entry, entry.mime, entry.rate)
        if item_id not in entry.items:
            entry.items.append(item_id)
        for index, part in enumerate(data.get("content") or []):
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("audio", "output_audio"):
                item.expected.add(index)
                entry.has_audio = True
                entry.turn.audio.present = True
            text = part.get("transcript") or part.get("text")
            if isinstance(text, str):
                item.transcripts[index] = text
        if self.sideband and item.expected and not item.complete and not item.failed:
            item.wanted = self.retrieve_enabled
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
        item.pending = 0
        item.wanted = False
        for request_id in [k for k, v in self.requests.items() if v is item]:
            self.requests.pop(request_id or "", None)
        parts: dict[int, str] = {}
        expected: set[int] = set()
        for index, part in enumerate(data.get("content") or []):
            if not isinstance(part, dict):
                continue
            transcript = part.get("transcript") or part.get("text")
            if isinstance(transcript, str):
                item.transcripts[index] = transcript
            if part.get("type") in ("audio", "output_audio", "input_audio"):
                expected.add(index)
                audio = part.get("audio")
                if isinstance(audio, str) and audio:
                    parts[index] = audio
        item.expected.update(expected)
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
        item.complete = bool(relevant) and relevant.issubset(parts) and valid
        if item.complete:
            item.parts = parts
        elif valid and not item.failed and item.attempts < _MAX_ATTEMPTS:
            item.wanted = self.retrieve_enabled
        self._rebuild(item)
        if item.entry is None or item.side is item.entry.turn.input:
            transcript = "".join(item.transcripts[k] for k in sorted(item.transcripts))
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
        for member in window:
            member.window = None
        start, end = window[0].start_ns, window[-1].end_ns
        if start is None or end is None or any(e.truncated or e.cleared for e in window):
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
            item = self.requests.pop(request_id or "", None)
            if item:
                item.pending = max(0, item.pending - 1)
                item.failed = True
                item.wanted = False
                self._check(item.entry)
                return True
        if event_type in ("response.output_item.added", "response.output_item.done"):
            tool = event.get("item")
            if isinstance(tool, dict) and tool.get("type") == "function_call":
                call_id, name = tool.get("call_id"), tool.get("name")
                if isinstance(call_id, str) and isinstance(name, str):
                    self._tool_call_names[call_id] = name
        if self.sideband:
            if event_type in ("response.output_item.added", "response.output_item.done"):
                entry = self.entries.get(event.get("response_id") or "")
                if entry:
                    self._bind_output(entry, event.get("item"))
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
        super().on_server_event(event)
        if event_type == "response.created":
            response = event.get("response") or {}
            entry = self.entries.get(response.get("id", ""))
            audio = response.get("audio") or {}
            output = audio.get("output") or {}
            audio_format = output.get("format") or response.get("output_audio_format")
            if entry and audio_format:
                entry.mime = self._integration.realtime_audio_format(audio_format)
                rate = audio_format.get("rate") if isinstance(audio_format, dict) else None
                if type(rate) is int and rate > 0:
                    entry.rate = rate
        if self.sideband and event_type == "input_audio_buffer.committed":
            input_item_id = event.get("item_id")
            if isinstance(input_item_id, str):
                item = self._bind(
                    input_item_id, self._pending_input, None, self._input_audio_mime, self._input_audio_rate
                )
                item.expected.add(0)
                item.side.audio.present = True
        if self.sideband and event_type in (
            "conversation.item.input_audio_transcription.completed",
            "conversation.item.input_audio_transcription.failed",
        ):
            item = self.items.get(event.get("item_id") or "")
            if item and not item.complete and not item.failed:
                item.wanted = self.retrieve_enabled
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
            if not item.wanted or item.pending or self.intents.get(item.id, 0):
                continue
            if not self.retrieve_enabled or item.failed or item.attempts >= _MAX_ATTEMPTS:
                item.wanted = False
                self._check(item.entry)
                continue
            item.wanted = False
            item.pending += 1
            item.attempts += 1
            task = self.context.copy().run(self.loop.create_task, self._retrieve(item))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        for entry in list(self.entries.values()):
            self._check(entry)

    async def _retrieve(self, item: _Item) -> None:
        owner = self.owner()
        request_id = "ddtrace_realtime_audio_" + uuid4()
        try:
            if owner is None or self._closed or self.items.get(item.id) is not item:
                return
            # The SDK's awaited raw handler has completed before this task is
            # scheduled, so automatic retrievals have already claimed their item.
            if self.intents.get(item.id, 0) or item.pending > 1 or item.complete or item.failed:
                return
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
    if state and item_id:
        state.intents[item_id] = state.intents.get(item_id, 0) + 1
    try:
        result = await func(*args, **kwargs)
        if state and not state._closed and result is not False:
            if observed is not None:
                state.on_client_event(observed)
            if item_id and observed is not None and observed.get("event_id") not in state.requests:
                item = state.items.get(item_id)
                if item:
                    item.wanted = False
                    item.pending += 1
        return result
    finally:
        if state and item_id:
            remaining = state.intents.get(item_id, 1) - 1
            if remaining:
                state.intents[item_id] = remaining
            else:
                state.intents.pop(item_id, None)


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
