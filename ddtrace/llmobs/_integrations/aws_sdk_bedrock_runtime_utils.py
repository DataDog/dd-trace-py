"""Nova 2 Sonic protocol state. Completion IDs are session-scoped, not turn IDs."""

import base64
from collections import deque
import json
import sys
import time
from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native import uuid4
from ddtrace.internal.native._native import Context
from ddtrace.llmobs._constants import AUDIO_FALLBACK_MARKER
from ddtrace.llmobs._context import LLMObsContextProvider
from ddtrace.llmobs._integrations.audio_utils import LLMOBS_AUDIO_INLINE_MAX_BYTES
from ddtrace.llmobs._integrations.audio_utils import format_audio_part_with_guard
from ddtrace.llmobs._integrations.audio_utils import pcm16_to_wav
from ddtrace.llmobs._routing import _ROUTING_CONTEXTVAR
from ddtrace.llmobs._routing import RoutingContextType
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs.types import ToolDefinition


log = get_logger(__name__)
# Retention is bounded independently from cumulative sample counters.
MAX_AUDIO = LLMOBS_AUDIO_INLINE_MAX_BYTES * 3 // 4 - 128
MAX_TEXT = 65536
MAX_BLOCKS = 256
MAX_TOOLS = 16
MAX_TOOL_TEXT = 4096


def pcm_rate(configuration: dict[str, Any]) -> int:
    if (
        configuration.get("mediaType") == "audio/lpcm"
        and configuration.get("sampleSizeBits") == 16
        and configuration.get("channelCount") == 1
        and configuration.get("encoding") == "base64"
        and configuration.get("sampleRateHertz") in (8000, 16000, 24000, 48000)
    ):
        return int(configuration["sampleRateHertz"])
    return 0


class InputAudio:
    def __init__(self) -> None:
        self.rate = 0
        self.valid = True
        self.data = bytearray()
        self.total = 0
        self.anchor_ns: Optional[int] = None

    def configure(self, configuration: dict[str, Any]) -> None:
        rate = pcm_rate(configuration)
        if not rate or (self.total and rate != self.rate):
            self.valid = False
        self.rate = rate

    def append(self, encoded: str, now: int) -> None:
        size = 0
        try:
            # Decode aligned base64 chunks so a large frame cannot duplicate its full
            # size in either the decoded temporary or the retained audio buffer.
            for offset in range(0, len(encoded), 65536):
                chunk = encoded[offset : offset + 65536]
                if offset + 65536 < len(encoded) and ("=" in chunk if isinstance(chunk, str) else b"=" in chunk):
                    raise ValueError("Padding before the end of an audio frame")
                raw = base64.b64decode(chunk, validate=True)
                size += len(raw)
                if len(raw) >= MAX_AUDIO:
                    self.data.clear()
                    self.data.extend(raw[-MAX_AUDIO:])
                else:
                    excess = max(0, len(self.data) + len(raw) - MAX_AUDIO)
                    del self.data[:excess]
                    self.data.extend(raw)
        except ValueError:
            self.valid = False
            raise
        if size % 2:
            self.valid = False
        self.total += size
        if self.rate:
            # Project offsets using the most recently sent sample, as Realtime does.
            self.anchor_ns = now - self.total * 1_000_000_000 // (2 * self.rate)

    def clip(self, start_ms: Optional[float], end_ms: Optional[float]) -> bytes:
        if not self.valid or not self.rate or start_ms is None or end_ms is None or start_ms >= end_ms:
            return b""
        start = int(start_ms * self.rate / 1000) * 2
        end = int(end_ms * self.rate / 1000) * 2
        base = self.total - len(self.data)
        if start < base or end > self.total:
            return b""
        return bytes(self.data[start - base : end - base])


class Turn:
    def __init__(self) -> None:
        self.windows: list[dict[str, Any]] = []
        self.user_text = ""
        self.final_text = ""
        self.speculative_text = ""
        self.tools: list[dict[str, Any]] = []
        self.tool_results: list[dict[str, Any]] = []
        self.input_pcm = b""
        self.input_rate = 0
        self.input_start_ns: Optional[int] = None
        self.input_end_ns: Optional[int] = None
        self.input_audio_observed = False
        self.output_audio_observed = False
        self.output_pcm = bytearray()
        self.output_rate = 0
        self.output_start_ns: Optional[int] = None
        self.output_end_ns: Optional[int] = None
        self.output_valid = True
        self.output_timing_valid = True
        self.output_audio_omitted_reason: Optional[str] = None
        self.output_bytes = 0
        self.generation_end_ns: Optional[int] = None
        self.interrupted_ns: Optional[int] = None
        self.started_ns: Optional[int] = None
        self.metrics: dict[str, int] = {}
        self.missing_audio_metrics: set[str] = set()
        self.missing_metrics: set[str] = set()
        self.completion_id: Optional[str] = None
        self.partial = False
        self.emitted = False
        self.finish_reason: Optional[str] = None

    @property
    def has_input(self) -> bool:
        return bool(self.windows or self.user_text or self.tool_results)

    def append_audio(self, content: str, configuration: dict[str, Any], now: int) -> None:
        if isinstance(content, str) and content:
            self.output_audio_observed = True
        if self.interrupted_ns is not None:
            # Count decodable generated bytes without touching the retained playback.
            try:
                self.output_bytes += len(base64.b64decode(content, validate=True))
            except (ValueError, TypeError):
                pass
            return
        rate = pcm_rate(configuration)
        try:
            raw = base64.b64decode(content, validate=True)
        except ValueError:
            self.output_valid = False
            self.output_timing_valid = False
            self.output_audio_omitted_reason = "invalid_audio"
            self.output_pcm.clear()
            raise
        self.output_bytes += len(raw)
        if not rate or len(raw) % 2 or (self.output_rate and self.output_rate != rate):
            self.output_valid = False
            self.output_timing_valid = False
            self.output_audio_omitted_reason = "invalid_audio"
        if not self.output_rate:
            self.output_rate = rate
        if self.output_start_ns is None:
            self.output_start_ns = now
            self.output_end_ns = now
        if not rate:
            return
        # Queued chunks must follow earlier audio, never overlap it.
        # Pad underruns so one clip and one speech phase describe the same timeline.
        previous_end = self.output_end_ns if self.output_end_ns is not None else now
        gap = max(0, now - previous_end) * rate // 1_000_000_000 * 2
        end = previous_end + (gap + len(raw)) * 1_000_000_000 // (2 * rate)
        self.output_end_ns = end
        if self.output_valid and len(self.output_pcm) + gap + len(raw) <= MAX_AUDIO:
            self.output_pcm.extend(bytes(gap))
            self.output_pcm.extend(raw)
        else:
            self.output_valid = False
            self.output_audio_omitted_reason = self.output_audio_omitted_reason or "retention_limit"
            self.output_pcm.clear()

    def interrupt(self, now: int) -> None:
        if self.interrupted_ns is not None:
            return
        self.interrupted_ns = now
        self.finish_reason = "INTERRUPTED"
        if self.output_start_ns is not None and self.output_end_ns is not None and self.output_rate:
            frames = max(0, now - self.output_start_ns) * self.output_rate // 1_000_000_000
            del self.output_pcm[frames * 2 :]
            self.output_end_ns = min(
                self.output_end_ns, self.output_start_ns + frames * 1_000_000_000 // self.output_rate
            )


class SonicState:
    def __init__(
        self,
        integration: Any,
        model: str,
        parent: Any,
        context_provider: Optional[LLMObsContextProvider] = None,
        llmobs_context: Optional[Context] = None,
        routing_context: Optional[RoutingContextType] = None,
    ) -> None:
        self.integration = integration
        self.model = model
        self.parent = parent
        self.context_provider = context_provider
        self.llmobs_context = llmobs_context
        self.routing_context = routing_context
        self.session_id = uuid4()
        self.audio = InputAudio()
        self.pending = Turn()
        self.current: Optional[Turn] = None
        self.outbound: dict[str, Any] = {}
        self.blocks: dict[str, Any] = {}
        self.prompt: Optional[str] = None
        self.configuration: dict[str, Any] = {}
        self.output_configuration: dict[str, Any] = {}
        self.history: list[dict[str, Any]] = []
        self.tool_definitions: list[ToolDefinition] = []
        self.closed = False
        self.turn_index = 0
        self.totals = {"input_tokens": 0, "output_tokens": 0}
        self.usage_gaps: dict[str, Optional[Turn]] = {}
        self.audio_totals: dict[str, Optional[int]] = {"input": 0, "output": 0}
        self.completed_ids: deque[str] = deque(maxlen=MAX_BLOCKS)

    def observe(self, event: Any, outbound: bool = False) -> None:
        if self.closed:
            return
        try:
            payload = json.loads(event.value.bytes_)
            for name, data in payload.get("event", {}).items():
                if outbound:
                    self.sent(name, data, time.time_ns())
                else:
                    self.received(name, data, time.time_ns())
        except Exception:
            # Never expose captured text/audio in logs or change application behavior.
            log.debug("Cannot process Nova Sonic telemetry", exc_info=True)

    def sent(self, name: str, data: dict[str, Any], now: int) -> None:
        key = data.get("contentName", "")
        if name == "sessionStart":
            self.configuration = {
                name: {
                    key: value
                    for key, value in data.get(name, {}).items()
                    if key in ("maxTokens", "topP", "temperature", "endpointingSensitivity")
                    and isinstance(value, (int, float, str))
                    and len(str(value)) < 128
                }
                for name in ("inferenceConfiguration", "turnDetectionConfiguration")
            }
        elif name == "promptStart":
            if self.prompt is not None and self.prompt != data.get("promptName"):
                # A second prompt's offset origin is not established by the captures.
                self.finish()
                return
            self.prompt = data.get("promptName")
            self.output_configuration = data.get("audioOutputConfiguration", {})
            self._set_tool_definitions(data.get("toolConfiguration"))
        elif name == "contentStart":
            self.outbound[key] = dict(data, text="")
            if data.get("type") == "AUDIO" and data.get("role") == "USER":
                self.audio.configure(data.get("audioInputConfiguration", {}))
        elif name == "audioInput":
            block = self.outbound.get(key, {})
            if block.get("type") == "AUDIO" and block.get("role") == "USER":
                if isinstance(data.get("content"), str) and data["content"]:
                    self.pending.input_audio_observed = True
                self.audio.append(data.get("content", ""), now)
        elif name == "textInput":
            block = self.outbound.get(key)
            if block is not None:
                block["text"] = (block["text"] + data.get("content", ""))[:MAX_TEXT]
        elif name == "toolResult":
            block = self.outbound.get(key, {})
            target = self.current or self.pending
            if len(target.tool_results) < MAX_TOOLS:
                target.tool_results.append(
                    {
                        "role": "tool",
                        "content": "",
                        "tool_results": [
                            {
                                "result": data.get("content", "")[:MAX_TOOL_TEXT],
                                "tool_id": block.get("toolResultInputConfiguration", {}).get("toolUseId", ""),
                                "type": "tool_result",
                            }
                        ],
                    }
                )
        elif name == "contentEnd":
            block = self.outbound.pop(key, {})
            if block.get("type") == "TEXT":
                if block.get("role") == "USER" and block.get("interactive"):
                    self.pending.user_text = (self.pending.user_text + block["text"])[:MAX_TEXT]
                elif len(self.history) < 16:
                    remaining = MAX_TEXT - sum(len(message["content"]) for message in self.history)
                    block["text"] = block["text"][:remaining]
                    self.history.append({"role": block.get("role", "USER").lower(), "content": block["text"]})
        self._bound_maps()

    def _set_tool_definitions(self, configuration: Any) -> None:
        self.tool_definitions = []
        tools = configuration.get("tools") if isinstance(configuration, dict) else None
        if not isinstance(tools, list):
            return
        remaining = min(MAX_TEXT, max(0, config._llmobs_event_size_limit // 4))
        for tool in tools[:MAX_TOOLS]:
            spec = tool.get("toolSpec") if isinstance(tool, dict) else None
            if not isinstance(spec, dict):
                continue
            name, description, schema = spec.get("name"), spec.get("description", ""), spec.get("inputSchema", {})
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(description, str)
                or not isinstance(schema, dict)
            ):
                continue
            if len(name) > MAX_TOOL_TEXT or len(description) > MAX_TOOL_TEXT:
                continue
            definition = ToolDefinition(name=name, description=description, schema=schema)
            # Stop serialization at the budget and retain a detached snapshot only
            # for complete definitions; never export a truncated JSON schema.
            parts = []
            size = 0
            try:
                for part in json.JSONEncoder(ensure_ascii=True).iterencode(definition):
                    size += len(part)
                    if size > remaining:
                        break
                    parts.append(part)
                else:
                    self.tool_definitions.append(json.loads("".join(parts)))
                    remaining -= size
            except (ValueError, TypeError, RecursionError):
                continue

    def _bound_maps(self) -> None:
        for mapping in (self.outbound, self.blocks):
            while len(mapping) > MAX_BLOCKS:
                mapping.pop(next(iter(mapping)))

    def _start_response(self, data: dict[str, Any], now: int) -> Turn:
        if (
            self.current is not None
            and self.current.interrupted_ns is not None
            and data.get("type") == "AUDIO"
            and self.pending.windows
            and self.pending.windows[-1].get("end_ms") is None
        ):
            # Audio arriving while the interrupting user is still speaking belongs to the
            # interrupted response. Keep its guard and the pending speech window intact.
            return self.current
        if self.current is None or self.pending.has_input:
            if self.current is not None:
                self.current.partial = self.current.generation_end_ns is None
                self._emit(self.current, now)
            turn = self.pending
            self.pending = Turn()
            turn.started_ns = now
            turn.completion_id = data.get("completionId")
            turn.input_rate = self.audio.rate
            if turn.windows:
                turn.input_pcm = self.audio.clip(turn.windows[0]["start_ms"], turn.windows[-1].get("end_ms"))
            self.current = turn
            # Retain pending ASR block ownership, but release previous response maps.
            self.blocks = {key: value for key, value in self.blocks.items() if value["turn"] is turn}
        return self.current

    def received(self, name: str, data: dict[str, Any], now: int) -> None:
        # Prefer provider identity when available; one SDK invocation remains one session.
        if data.get("sessionId"):
            self.session_id = data["sessionId"]
        if name == "userSpeechStart":
            offset = data.get("inputAudioOffsetMs")
            if not isinstance(offset, (int, float)) or offset < 0:
                return
            self.pending.input_audio_observed = True
            if len(self.pending.windows) < MAX_BLOCKS:
                self.pending.windows.append({"start_ms": offset})
            if self.pending.input_start_ns is None:
                anchor = self.audio.anchor_ns
                self.pending.input_start_ns = min(now, anchor + int(offset * 1e6)) if anchor is not None else now
        elif name == "userSpeechEnd":
            if not self.pending.windows:
                return
            window = self.pending.windows[-1]
            end = data.get("inputAudioOffsetMs")
            if not isinstance(end, (int, float)) or end < window["start_ms"]:
                return
            window["end_ms"] = end
            window["detection_ms"] = data.get("inputAudioDetectionOffsetMs")
            self.pending.input_end_ns = now
        elif name == "contentStart":
            key = data.get("contentId", "")
            if key in self.blocks or key in self.completed_ids:
                return
            fields = data.get("additionalModelFields")
            if not isinstance(fields, dict):
                try:
                    fields = json.loads(fields or "{}")
                except (ValueError, TypeError):
                    fields = {}
            stage = fields.get("generationStage") if isinstance(fields, dict) else None
            role = data.get("role")
            if role in ("ASSISTANT", "TOOL"):
                # FINAL text can arrive after the next user's speechStart. It still
                # belongs to the previous output; only new speculative/audio/tool
                # generation consumes pending user input.
                if stage == "FINAL" and self.current is not None:
                    turn = self.current
                else:
                    turn = self._start_response(data, now)
            else:
                turn = (
                    self.pending
                    if self.pending.has_input or self.current is None or self.current.generation_end_ns is not None
                    else self.current
                )
            self.blocks[key] = {"data": data, "stage": stage, "turn": turn, "text": ""}
            self._bound_maps()
        elif name in ("textOutput", "audioOutput", "toolUse", "contentEnd"):
            key = data.get("contentId", "")
            block = self.blocks.get(key)
            if block is None:
                return
            turn = block["turn"]
            if name == "textOutput":
                text = data.get("content", "")
                try:
                    control = json.loads(text)
                except (ValueError, TypeError):
                    control = None
                if (
                    block["data"].get("role") == "ASSISTANT"
                    and isinstance(control, dict)
                    and control.get("interrupted") is True
                ):
                    turn.interrupt(now)
                    block["control"] = True
                else:
                    block["text"] = (block["text"] + text)[:MAX_TEXT]
            elif name == "audioOutput":
                turn.append_audio(
                    data.get("content", ""),
                    block["data"].get("audioOutputConfiguration", self.output_configuration),
                    now,
                )
            elif name == "toolUse":
                if len(turn.tools) < MAX_TOOLS:
                    try:
                        encoded = data.get("content", "{}")
                        arguments = json.loads(encoded) if len(encoded) <= MAX_TOOL_TEXT else {}
                    except (ValueError, TypeError):
                        arguments = {}
                    turn.tools.append(
                        {
                            "name": data.get("toolName", ""),
                            "arguments": arguments,
                            "tool_id": data.get("toolUseId", ""),
                            "type": "function",
                        }
                    )
            elif name == "contentEnd":
                text = block["text"]
                if not block.get("control"):
                    if block["data"].get("role") == "USER":
                        turn.user_text = (turn.user_text + text)[:MAX_TEXT]
                    elif block["stage"] == "FINAL":
                        turn.final_text = (turn.final_text + text)[:MAX_TEXT]
                    else:
                        turn.speculative_text = (turn.speculative_text + text)[:MAX_TEXT]
                reason = data.get("stopReason")
                if block["data"].get("role") in ("ASSISTANT", "TOOL"):
                    if reason == "INTERRUPTED":
                        turn.interrupt(now)
                    elif turn.interrupted_ns is None and reason in ("END_TURN", "PARTIAL_TURN", "TOOL_USE"):
                        # Intermediate blocks cannot replace a terminal turn reason.
                        if turn.finish_reason != "END_TURN" and (
                            reason != "PARTIAL_TURN" or turn.finish_reason is None
                        ):
                            turn.finish_reason = reason
                    if block["data"].get("type") == "AUDIO" and reason == "END_TURN" and turn.interrupted_ns is None:
                        turn.generation_end_ns = now
                self.blocks.pop(key)
                self.completed_ids.append(key)
        elif name == "usageEvent":
            self._usage(data)
        elif name == "completionEnd":
            self.finish()

    def _usage(self, data: dict[str, Any]) -> None:
        # Cumulative completion totals repeat across turns. Difference them once.
        details = data.get("details")
        breakdown = details.get("total") if isinstance(details, dict) else None
        for direction, field in (("input", "totalInputTokens"), ("output", "totalOutputTokens")):
            key = direction + "_tokens"
            target = (
                self.pending
                if self.current is None or (key == "input_tokens" and self.pending.has_input)
                else self.current
            )
            total = data.get(field)
            if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                if key not in self.usage_gaps:
                    self.usage_gaps[key] = target
                elif self.usage_gaps[key] is not target:
                    previous = self.usage_gaps[key]
                    if previous is not None:
                        previous.missing_metrics.add(key)
                    target.missing_metrics.add(key)
                    self.usage_gaps[key] = None
                continue
            if total < self.totals[key]:
                # A valid older cumulative counter is a stale event, not a reset.
                continue
            if key in self.usage_gaps:
                previous = self.usage_gaps.pop(key)
                if previous is not target:
                    if previous is not None:
                        previous.missing_metrics.add(key)
                    target.missing_metrics.add(key)
                    # Neither aggregate nor speech deltas can be attributed across
                    # a gap spanning turns. Recover both baselines without charging it.
                    self.audio_totals[direction] = None
            delta = total - self.totals[key]
            self.totals[key] = total
            if key not in target.missing_metrics:
                target.metrics[key] = target.metrics.get(key, 0) + delta
            # Missing counters are unknown, not zero. Derive a total only when both
            # directions have been reported for this turn, including explicit zeros.
            if "input_tokens" in target.metrics and "output_tokens" in target.metrics:
                target.metrics["total_tokens"] = target.metrics["input_tokens"] + target.metrics["output_tokens"]

            # Speech tokens are a subset of the same aggregate delta and belong
            # to the same turn. Never add them to total_tokens again.
            audio_key = direction + "_audio_tokens"
            usage = breakdown.get(direction) if isinstance(breakdown, dict) else None
            audio_total = usage.get("speechTokens") if isinstance(usage, dict) else None
            previous_audio = self.audio_totals[direction]
            valid_audio = (
                isinstance(audio_total, int)
                and not isinstance(audio_total, bool)
                and 0 <= audio_total <= total
                and (previous_audio is None or audio_total >= previous_audio)
            )
            audio_delta = (
                audio_total - previous_audio
                if valid_audio and audio_total is not None and previous_audio is not None
                else None
            )
            self.audio_totals[direction] = audio_total if valid_audio else (None if delta else previous_audio)
            if audio_delta is None or audio_delta > delta:
                if delta or (audio_delta is not None and audio_delta > delta):
                    # A gap makes this turn's audio split unknown. Re-establish
                    # the cumulative baseline without charging the gap to a later turn.
                    target.missing_audio_metrics.add(audio_key)
                    target.metrics.pop(audio_key, None)
                continue
            if audio_key not in target.missing_audio_metrics:
                target.metrics[audio_key] = target.metrics.get(audio_key, 0) + audio_delta

    def _messages(self, turn: Turn, overhead_bytes: int = 0) -> tuple[list[Any], list[Any]]:
        # Reserve the serialized text/tool size, including JSON escaping of Unicode.
        # The shared 4 MiB limit leaves room for identity, metadata and phase data.
        text_bytes = len(
            json.dumps(
                [
                    self.history,
                    turn.user_text,
                    turn.final_text or turn.speculative_text,
                    turn.tools,
                    turn.tool_results,
                ],
                ensure_ascii=True,
            ).encode("utf-8")
        )
        # A smaller configured event limit still needs room for identity, tags and metadata.
        audio_limit = min(LLMOBS_AUDIO_INLINE_MAX_BYTES, max(0, config._llmobs_event_size_limit - 8192))
        budget = max(0, audio_limit - text_bytes - overhead_bytes - 256)
        messages: list[Any] = []
        for role, text, pcm, rate, observed in (
            ("user", turn.user_text, turn.input_pcm, turn.input_rate, turn.input_audio_observed),
            (
                "assistant",
                turn.final_text or turn.speculative_text,
                bytes(turn.output_pcm) if turn.output_valid else b"",
                turn.output_rate,
                turn.output_audio_observed,
            ),
        ):
            message: dict[str, Any] = {"role": role, "content": text or (AUDIO_FALLBACK_MARKER if observed else "")}
            if pcm and rate:
                part = format_audio_part_with_guard(pcm16_to_wav(pcm, rate), "audio/wav", budget)
                if part:
                    message["audio_parts"] = [part]
                    message["content"] = text
                    budget -= len(part["content"])
                elif role == "assistant":
                    turn.output_audio_omitted_reason = "payload_limit"
            messages.append(message)
        if turn.tools:
            messages[1]["tool_calls"] = turn.tools
        return self.history + [messages[0]] + turn.tool_results, [messages[1]]

    def _emit(self, turn: Turn, now: int, error: Any = None) -> None:
        if turn.emitted:
            return
        turn.emitted = True
        previous_context = self.context_provider.active() if self.context_provider is not None else None
        # An unrouted invocation must not inherit the receiver's destination either.
        routing_token = _ROUTING_CONTEXTVAR.set(self.routing_context)
        try:
            if self.context_provider is not None:
                self.context_provider.activate(self.llmobs_context)
            self._emit_turn(turn, now, error)
        except Exception:
            log.debug("Cannot emit Nova Sonic turn", exc_info=True)
        finally:
            _ROUTING_CONTEXTVAR.reset(routing_token)
            if self.context_provider is not None:
                self.context_provider.activate(previous_context)

    def _emit_turn(self, turn: Turn, now: int, error: Any = None) -> None:
        unknown = turn.missing_metrics | {key for key, owner in self.usage_gaps.items() if owner is turn}
        excluded = unknown | {key.replace("_tokens", "_audio_tokens") for key in unknown}
        if unknown:
            excluded.add("total_tokens")
        metrics = {key: value for key, value in turn.metrics.items() if key not in excluded}
        self.turn_index += 1
        if turn.windows and not turn.input_pcm:
            turn.input_rate = self.audio.rate
            turn.input_pcm = self.audio.clip(turn.windows[0]["start_ms"], turn.windows[-1].get("end_ms"))
        start = turn.input_start_ns or turn.started_ns or now
        llm_start = turn.input_end_ns or turn.started_ns or start
        response_end = max(llm_start, turn.generation_end_ns or turn.interrupted_ns or now)
        end = max(response_end, turn.output_end_ns or 0, turn.input_end_ns or 0)
        root = self.integration.start_span(
            "nova sonic audio turn", "workflow", self.model, self.session_id, self.parent, start
        )
        spans = [(root, end)]
        try:
            if self.context_provider is not None:
                self.context_provider.activate(root)
            response = self.integration.start_span(
                "nova sonic response", "llm", self.model, self.session_id, root, llm_start
            )
            spans.append((response, response_end))
            metadata = {
                "turn_index": self.turn_index,
                "completion_id": turn.completion_id,
                "speech_windows": turn.windows,
                "ttfa_boundary": "speech_end_event_receipt",
                "output_timing": "projected_playback",
                "interrupted": turn.interrupted_ns is not None,
                "partial": turn.partial,
                "transcript_stage": "FINAL" if turn.final_text else "SPECULATIVE",
                "usage_attribution": "event_receipt",
                "generated_output_bytes": turn.output_bytes,
                "session_configuration": self.configuration,
            }
            if turn.finish_reason is not None:
                metadata["finish_reason"] = turn.finish_reason
            if self.tool_definitions:
                _annotate_llmobs_span_data(response, tool_definitions=self.tool_definitions)
            overhead_bytes = len(
                json.dumps([metadata, _get_llmobs_data_metastruct(response)], ensure_ascii=True).encode()
            )
            inputs, outputs = self._messages(turn, overhead_bytes)
            if turn.output_audio_omitted_reason:
                metadata["output_audio_omitted_reason"] = turn.output_audio_omitted_reason
            _annotate_llmobs_span_data(
                response, input_messages=inputs, output_messages=outputs, metadata=metadata, metrics=metrics
            )
            _annotate_llmobs_span_data(
                root, input_value=turn.user_text, output_value=turn.final_text or turn.speculative_text
            )
            self.integration._apply_shadow_metrics(
                response, metrics, "llm", model_name=self.model, model_provider="amazon"
            )
            for name, begin, finish in (
                ("user speech", turn.input_start_ns, turn.input_end_ns),
                ("agent speech", turn.output_start_ns, turn.output_end_ns),
            ):
                if begin is None or finish is None or finish < begin:
                    continue
                if name == "agent speech" and not turn.output_timing_valid:
                    continue
                if self.context_provider is not None:
                    self.context_provider.activate(root)
                phase = self.integration.start_span(name, "workflow", self.model, self.session_id, root, begin)
                spans.append((phase, finish))
        finally:
            for span, finish in reversed(spans):
                if error is not None:
                    span.set_exc_info(*error)
                span.finish(finish_time=max(span.start_ns, finish) / 1e9)

    def finish_error(self, error: Any = None) -> None:
        self.finish(error=error or sys.exc_info())

    def finish(self, error: Any = None) -> None:
        if self.closed:
            return
        self.closed = True
        now = time.time_ns()
        try:
            # Recover incomplete text blocks on shutdown without duplicating completed ones.
            for block in self.blocks.values():
                if not block.get("control"):
                    turn = block["turn"]
                    text = block["text"]
                    if block["data"].get("role") == "USER":
                        turn.user_text = (turn.user_text + text)[:MAX_TEXT]
                    elif block["stage"] == "FINAL":
                        turn.final_text = (turn.final_text + text)[:MAX_TEXT]
                    else:
                        turn.speculative_text = (turn.speculative_text + text)[:MAX_TEXT]
            if self.current is not None:
                self.current.partial = self.current.generation_end_ns is None
                self._emit(self.current, now, error)
            if self.pending.has_input or self.pending.metrics or (error and self.current is None):
                self.pending.partial = True
                self._emit(self.pending, now, error)
        except Exception:
            log.debug("Cannot finalize Nova Sonic telemetry", exc_info=True)
        finally:
            self.current = None
            self.pending = Turn()
            self.blocks.clear()
            self.outbound.clear()
            self.history.clear()
            self.tool_definitions = []
            self.usage_gaps.clear()
            self.audio.data.clear()
