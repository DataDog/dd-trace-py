"""Instrumentation for the OpenAI Realtime API (bidirectional WebSocket event stream).

The Realtime API is not request/response, so it can't reuse the streaming path. Instead we wrap
the connection's send/parse_event/close methods (all typed sub-resource sends funnel through
RealtimeConnection.send, and recv/iteration/recv_bytes() all funnel through parse_event) and feed
each observed event into a _RealtimeState machine.

Each conversation turn becomes its own trace rooted at a workflow "realtime audio turn" span, with child spans that
separate the phases by owner: a "user speech" workflow span (the human speaking window), an llm span
for the model's work (generation - started at the end of user speech and finished at response.done,
carrying the user/assistant transcripts, audio, and token usage), an "agent speech" workflow span
(the human hearing window). Any tool calls the model makes are captured on the llm span. Splitting
the phases keeps span duration meaningful: the llm span measures model latency, not the time the
human spent talking, and time-to-first-agent-audio falls out of the span boundaries. The user-speech
window is anchored on the VAD speech-onset event rather than on the first audio the client appended,
since a server-VAD client streams the microphone continuously and the buffer is therefore open from
the moment the previous turn was committed; the pre-speech lead-in is trimmed off the captured audio
to match (see `_on_speech_started`). Every span is annotated with a per-connection session_id so
the UI groups all of a connection's turns into one conversation; there is no parent "session" span
across turns, which keeps each trace one turn small (no accumulation toward the per-event size
budget) and renders cleanly. (If the caller wraps the connection in their own LLMObs context, the
turn roots naturally nest under it.)

Barge-in cuts the agent off mid-sentence, and over a WebSocket the client owns playback, so audio we
already received may never be heard. When the client reports how far it got
(conversation.item.truncate), the agent segment is capped there so both the stored audio and the
window derived from it cover what was heard. That report arrives after response.done, so on
connections whose client has been seen to truncate, a finished turn is held while its audio is still
playing rather than submitted immediately (see _park_for_playback); clients that never truncate are
unaffected.

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

import base64
import importlib
import time
from types import ModuleType
from types import SimpleNamespace
from typing import Any
from typing import Callable
from typing import Optional
import uuid
import weakref

import openai

from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
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
    """Collapse SDK naming drift so `response.output_audio.*`/`response.output_text.*` match
    their older `response.audio.*`/`response.text.*` equivalents.
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

# Longest we will hold a finished turn waiting for its audio to finish playing, so a late barge-in
# truncation can still cap it (see `_park_for_playback`). Flushing is event-driven, so this bounds
# both the submission delay and the window in which a connection going idle leaves a turn unflushed.
_PARK_MAX_NS = 5 * 1_000_000_000


class _AudioAccumulator:
    """Collects base64 audio chunks with a running decoded-byte cap.

    `present` records that audio was seen at all (so a turn can still surface an `[audio]` marker
    even when the bytes were dropped), and `oversize` marks that the cap was hit.
    """

    def __init__(self) -> None:
        self.chunks: list[str] = []
        self.present: bool = False
        self.oversize: bool = False
        self._bytes: int = 0
        # Decoded byte length of each buffered chunk, parallel to `chunks`, so a leading run of
        # them can be identified by byte offset and trimmed (see `trim_leading`).
        self._chunk_bytes: list[int] = []
        # Wall-clock (unix ns) when the first chunk of this segment was observed. Anchors the segment
        # on the shared session timeline for full-conversation playback. Set even when bytes are later
        # dropped (oversize), since `present` still surfaces a marker.
        self.start_ns: Optional[int] = None
        # Total decoded bytes seen for this segment, never capped, used only to derive playback
        # duration (the speaking window) even when the byte cap dropped the buffered chunks.
        self.total_decoded_bytes: int = 0
        # The audio format in effect when this segment's first chunk arrived. Snapshotted per segment
        # because the session's format is mutable - `session.update` can change it mid-connection -
        # while a turn is finalized later (held for a transcript or for playback). Reading the
        # session's current format at that point would time and WAV-wrap this segment's bytes at
        # another format's rate: PCM16 read as G.711 overstates the window six-fold.
        self.mime: str = ""
        self.rate: int = 0

    def append(self, b64: str, mime: str = "", rate: int = 0) -> None:
        if not b64:
            return
        if self.start_ns is None:
            self.start_ns = time.time_ns()
            self.mime = mime
            self.rate = rate
        self.present = True
        decoded = _decoded_b64_len(b64)
        self.total_decoded_bytes += decoded
        if self.oversize:
            return
        self._bytes += decoded
        if self._bytes > _AUDIO_ACCUM_MAX_BYTES:
            self.oversize = True
            self.chunks = []  # free what we had; the guard would drop the whole thing anyway
            self._chunk_bytes = []
            return
        self.chunks.append(b64)
        self._chunk_bytes.append(decoded)

    def trim_leading(self, decoded_bytes: int) -> None:
        """Drop the buffered chunks that lie entirely within this segment's first `decoded_bytes`.

        Used to cut the pre-speech audio a continuously-streaming client appends (the microphone
        keeps sending while the agent talks) off the front of a user turn, so the captured audio
        covers the same window the span reports. Trimming is whole-chunk - chunks are separately
        encoded base64 and PCM16 samples must stay byte-aligned - so up to one chunk of lead-in
        survives, which is well inside the prefix padding the onset offset already includes.
        """
        if decoded_bytes <= 0:
            return
        if decoded_bytes >= self.total_decoded_bytes:
            # Everything seen so far precedes the onset, so the segment starts empty: reset outright,
            # byte cap included. Otherwise a long silent lead-in (the buffer stays open across the
            # whole previous agent response) could spend the cap before the speech even starts and
            # leave the turn with nothing but an `[audio]` marker.
            self.clear()
            return
        dropped = 0
        count = 0
        for size in self._chunk_bytes:
            if dropped + size > decoded_bytes:
                break
            dropped += size
            count += 1
        if count:
            del self.chunks[:count]
            del self._chunk_bytes[:count]
        # Re-derive the cap from what actually survived, which also reopens a segment the lead-in had
        # closed. On a continuously-streaming client the lead-in is what spends the cap (the buffer
        # stays open across the whole previous agent response), and `append` drops `_chunk_bytes`
        # when it trips - so the loop above finds nothing to subtract and, without this, the cap the
        # trimmed-away audio filled would go on rejecting the user's actual speech for the rest of the
        # turn. That is the outcome trimming exists to prevent, not to cause.
        self._bytes = sum(self._chunk_bytes)
        self.oversize = self._bytes > _AUDIO_ACCUM_MAX_BYTES
        # `total_decoded_bytes` measures the playback window rather than what we kept, so it sheds
        # the whole lead-in (not just the whole chunks), including when the byte cap already dropped
        # the chunks themselves.
        self.total_decoded_bytes = max(0, self.total_decoded_bytes - decoded_bytes)

    def cap_to(self, decoded_bytes: int) -> None:
        """Shrink this segment to its first `decoded_bytes`, dropping whole trailing chunks.

        The mirror of `trim_leading`, for audio that was delivered but never heard: when the
        listener cuts the agent off, everything past that point is generated-but-unplayed. Absolute
        and shrink-only, so a truncation and its server acknowledgement apply once between them.
        """
        if decoded_bytes >= self.total_decoded_bytes:
            return
        if decoded_bytes <= 0:
            # Nothing was heard at all; the segment is empty rather than zero-length-but-present.
            self.clear()
            return
        kept = 0
        chunks: list[str] = []
        sizes: list[int] = []
        for chunk, size in zip(self.chunks, self._chunk_bytes):
            if kept + size <= decoded_bytes:
                chunks.append(chunk)
                sizes.append(size)
                kept += size
                continue
            # The cut lands inside this chunk. Unlike `trim_leading` - where whole-chunk granularity
            # only costs a little extra lead-in - stopping short here would drop heard audio, and a
            # response delivered as one big delta would lose all of it, so split the chunk exactly.
            partial, partial_size = _slice_b64(chunk, decoded_bytes - kept)
            if partial_size:
                chunks.append(partial)
                sizes.append(partial_size)
                kept += partial_size
            break
        self.chunks = chunks
        self._chunk_bytes = sizes
        self._bytes = kept
        self.total_decoded_bytes = decoded_bytes

    def clear(self) -> None:
        self.chunks = []
        self._chunk_bytes = []
        self.present = False
        self.oversize = False
        self._bytes = 0
        self.start_ns = None
        self.total_decoded_bytes = 0
        self.mime = ""
        self.rate = 0


class _InputTurn:
    """Accumulated user input (audio + transcript/text) for a single turn."""

    def __init__(self) -> None:
        self.audio = _AudioAccumulator()
        self.text: str = ""
        self.transcript: str = ""
        self.item_id: Optional[str] = None
        # Wall-clock (unix ns) when the user actually started speaking, from the VAD speech-onset
        # event. This is the start of the user-speech window; the first buffered chunk is not, since
        # a server-VAD client streams the microphone continuously (see `_on_speech_started`).
        self.speech_start_ns: Optional[int] = None
        # Wall-clock (unix ns) when the user's input audio was committed (~ end of user speech). Used
        # to measure response latency from real speech-end rather than the padded buffer end.
        self.speech_end_ns: Optional[int] = None
        # Offset (ms) on the session's input-audio-buffer timeline of the first chunk buffered for
        # this turn, so a VAD offset can be converted into a byte offset into `audio`.
        self.audio_base_ms: Optional[float] = None
        # Tool results the app fed back (function_call_output) before the next response.
        self.tool_results: list[ToolResult] = []

    def discard_audio(self) -> None:
        """The input audio buffer was cleared: drop the buffered audio and the speech onset derived
        from it so neither can be attributed to the next response. The committed speech end is left
        alone - a client that clears the buffer after committing has still ended that speech.
        """
        self.audio.clear()
        self.speech_start_ns = None
        self.audio_base_ms = None


class _ResponseTurn:
    """Accumulated assistant output for a single `response.*` lifecycle."""

    def __init__(self, input_turn: _InputTurn) -> None:
        self.input = input_turn
        self.audio = _AudioAccumulator()
        self.transcript: str = ""
        self.text: str = ""
        self.usage: Any = None
        self.model: Optional[str] = None
        self.status: Optional[str] = None
        # `root_span` is the turn's workflow root (parents the user-speech, llm, and agent-speech
        # spans); `span` is the llm (generation) span, kept as the tool-span parent.
        self.root_span: Any = None
        self.span: Any = None
        # Wall-clock (unix ns) when response.done arrived; the llm span ends here (generation
        # complete), not when the agent finishes speaking.
        self.response_done_ns: Optional[int] = None
        # Byte offset into `audio` at which each output item's audio begins, so a truncation (which
        # is reported per item) maps onto this turn's segment.
        self.audio_item_starts: dict[str, int] = {}
        # Wall-clock (unix ns) the agent's audio would finish playing; set while the turn is held open
        # waiting for playback to end (see `_park_for_playback`).
        self.playback_end_ns: Optional[int] = None
        # Function/MCP calls the model made this turn (+ inline MCP results).
        self.tool_calls: list[ToolCall] = []
        self.tool_results: list[ToolResult] = []


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
        # Offset (ms) of the end of all input audio appended to the buffer this session, and the wall
        # clock (unix ns) when we reached it. Together they place the VAD events' buffer offsets on
        # the wall clock (see `_buffer_offset_to_wall_ns`). `_input_buffer_ms_at_ns` is None until
        # the first append we could rate, and that is what marks the projection unavailable.
        self._input_buffer_ms: float = 0.0
        self._input_buffer_ms_at_ns: Optional[int] = None
        # Decoded input bytes seen before the session's audio format was known, held until a rate
        # arrives to convert them (see `_advance_input_buffer_clock`).
        self._pending_input_bytes: int = 0
        self._pending_input = _InputTurn()
        self._responses: dict[str, Any] = {}
        self._input_transcripts: dict[str, str] = {}
        # call_id -> function name, so a later function_call_output can be labeled with its tool name.
        self._tool_call_names: dict[str, str] = {}
        # Turns whose response is done but whose input transcription hasn't arrived yet.
        self._awaiting: list[Any] = []
        # Finished turns held open while their audio is still playing, so a barge-in truncation can
        # still cap them (see `_park_for_playback`), and whether this connection's client has ever
        # truncated - which is what makes holding worth its cost.
        self._playing: list[Any] = []
        self._client_truncates = False
        self._closed = False

    # -- event entry points -------------------------------------------------

    def on_client_event(self, event: Any) -> None:
        try:
            self._flush_playing()
            event_type = _event_type(event)
            if event_type == "conversation.item.truncate":
                # Client -> server: "the listener only got this far into that item."
                self._on_truncate(_get_attr(event, "item_id", None), _get_attr(event, "audio_end_ms", None))
            elif event_type == "session.update":
                self._update_session_config(_get_attr(event, "session", None))
            elif event_type == "input_audio_buffer.append":
                audio = _get_attr(event, "audio", None)
                if audio:
                    self._append_input_audio(audio)
            elif event_type == "input_audio_buffer.clear":
                # Discarded input audio must not be attributed to the next response.
                self._pending_input.discard_audio()
            elif event_type == "conversation.item.create":
                self._absorb_input_item(_get_attr(event, "item", None))
        except Exception:
            log.debug("error handling realtime client event", exc_info=True)

    def on_server_event(self, event: Any) -> None:
        try:
            self._flush_playing()
            event_type = _event_type(event)
            if event_type in ("session.created", "session.updated"):
                self._update_session_config(_get_attr(event, "session", None))
                return
            if event_type == "conversation.item.truncated":
                # The server's acknowledgement of a client truncation; `cap_to` is absolute, so
                # handling both it and the client event applies the cap once.
                self._on_truncate(_get_attr(event, "item_id", None), _get_attr(event, "audio_end_ms", None))
                return
            if event_type == "input_audio_buffer.speech_started":
                self._on_speech_started(_get_attr(event, "audio_start_ms", None))
                return
            if event_type == "input_audio_buffer.speech_stopped":
                # The commit that follows is the authoritative end of user speech (and overwrites
                # this); recording it here only covers a session that never commits, so the window
                # still gets an end.
                if self._pending_input.speech_end_ns is None:
                    self._pending_input.speech_end_ns = time.time_ns()
                return
            if event_type == "input_audio_buffer.committed":
                self._pending_input.item_id = _get_attr(event, "item_id", None)
                self._pending_input.speech_end_ns = time.time_ns()
                return
            if event_type == "input_audio_buffer.cleared":
                self._pending_input.discard_audio()
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
        if normalized == "response.audio.delta":
            delta = _get_attr(event, "delta", None)
            if delta:
                item_id = _get_attr(event, "item_id", None)
                if item_id is not None:
                    # Remember where this item's audio starts in the turn's segment, before appending.
                    turn.audio_item_starts.setdefault(str(item_id), turn.audio.total_decoded_bytes)
                turn.audio.append(delta, self._output_audio_mime, self._output_audio_rate)
        elif normalized == "response.audio_transcript.delta":
            turn.transcript += str(_get_attr(event, "delta", "") or "")
        elif normalized == "response.audio_transcript.done":
            turn.transcript = str(_get_attr(event, "transcript", turn.transcript) or "")
        elif normalized == "response.text.delta":
            turn.text += str(_get_attr(event, "delta", "") or "")
        elif normalized == "response.text.done":
            turn.text = str(_get_attr(event, "text", turn.text) or "")

    # -- input audio + speech window ----------------------------------------

    def _append_input_audio(self, b64: str) -> None:
        """Buffer a client audio append for the pending turn and advance the input-buffer clock."""
        pending = self._pending_input
        # Fold in anything buffered before the format was known first, so the base offset captured
        # below sits on the same timeline the VAD offsets are later projected against.
        self._advance_input_buffer_clock(0)
        if pending.audio.start_ns is None and self._input_buffer_ms_at_ns is not None:
            # First chunk of this turn and the clock is live: remember where it sits on the session's
            # input-buffer timeline, so a VAD offset can be turned into a byte offset into what we
            # buffer here. Left at None while the clock is dead, since a base of 0 would read as
            # "this turn starts at the session origin" and over-trim the front of the segment.
            pending.audio_base_ms = self._input_buffer_ms
        pending.audio.append(b64, self._input_audio_mime, self._input_audio_rate)
        self._advance_input_buffer_clock(_decoded_b64_len(b64))

    def _advance_input_buffer_clock(self, decoded_bytes: int) -> None:
        """Track how far into the session's input audio the buffer now extends, and when we got there.

        Audio we cannot yet rate is held as a raw byte count and converted the moment a rate is known,
        rather than written off. The session's format arrives on session.created/updated, which is
        observed only when the app parses it - a client streaming from its own thread routinely beats
        it - and losing the origin to that race disabled the projection, and with it the lead-in
        trimming, for the whole session. A format we can never rate (an unknown codec) still leaves
        the projection unavailable: the backlog simply never converts and `_input_buffer_ms_at_ns`
        stays None, which is what marks the clock dead.
        """
        self._pending_input_bytes += decoded_bytes
        bytes_per_second = _bytes_per_second(self._input_audio_mime, self._input_audio_rate)
        if not bytes_per_second:
            return
        self._input_buffer_ms += self._pending_input_bytes / bytes_per_second * 1000
        self._pending_input_bytes = 0
        self._input_buffer_ms_at_ns = time.time_ns()

    def _on_speech_started(self, audio_start_ms: Any) -> None:
        """Anchor the pending turn's user-speech window on the VAD speech onset.

        A server-VAD client streams the microphone continuously, so the first buffer append of a turn
        lands the instant the *previous* turn was committed: it marks when we started listening, not
        when the human started speaking. Left at that, every user-speech window swallows the whole
        preceding agent response and consecutive turns overlap on the session timeline.
        `input_audio_buffer.speech_started` is the real onset, and the audio it points at
        (`audio_start_ms`, which already includes the session's `prefix_padding_ms`) is the audio
        worth keeping, so the buffered lead-in is trimmed off the front to keep the captured audio and
        the reported window in step.

        Only the first onset of a turn counts: a turn that VAD splits into several speech runs before
        a single commit is still one committed item, which began at the first run.
        """
        pending = self._pending_input
        if pending.speech_start_ns is not None:
            return
        onset_ns = self._buffer_offset_to_wall_ns(audio_start_ms, time.time_ns())
        pending.speech_start_ns = onset_ns
        had_audio = pending.audio.start_ns is not None
        pending.audio.trim_leading(self._pre_onset_bytes(audio_start_ms))
        if had_audio:
            # Re-anchor the segment on the onset: whatever survived the trim starts there.
            pending.audio.start_ns = onset_ns

    def _pre_onset_bytes(self, audio_start_ms: Any) -> int:
        """Decoded byte count of the audio buffered for this turn ahead of the speech onset."""
        base_ms = self._pending_input.audio_base_ms
        onset_ms = _as_float(audio_start_ms)
        bytes_per_second = _bytes_per_second(self._input_audio_mime, self._input_audio_rate)
        if base_ms is None or onset_ms is None or not bytes_per_second:
            return 0
        return max(0, int((onset_ms - base_ms) / 1000 * bytes_per_second))

    def _buffer_offset_to_wall_ns(self, offset_ms: Any, observed_ns: int) -> int:
        """Project an input-buffer offset (the VAD events' `audio_start_ms`/`audio_end_ms`,
        measured from the start of all audio written to the buffer this session) onto the wall clock.

        AIDEV-NOTE: this assumes the client appends audio roughly in real time - true for a live
        microphone, which is the only case where a wall-clock speech window means anything. Knowing
        how much audio we had appended at a known instant, the offset is that instant minus the audio
        still ahead of it. The result is clamped to a window we can defend (no earlier than this
        turn's first buffered chunk, no later than when we observed the event) so a bursty or
        pre-recorded sender degrades to a sane bound instead of a wild timestamp, and falls back to
        the observation time when the projection is unavailable.
        """
        offset_ms = _as_float(offset_ms)
        if offset_ms is None or self._input_buffer_ms_at_ns is None:
            return observed_ns
        projected = self._input_buffer_ms_at_ns - int((self._input_buffer_ms - offset_ms) * 1_000_000)
        earliest = self._pending_input.audio.start_ns
        if earliest is not None:
            projected = max(projected, earliest)
        return min(projected, observed_ns)

    # -- barge-in (agent playback cut short) --------------------------------

    def _open_turns(self) -> list[Any]:
        """Every turn we could still amend: in flight, awaiting a transcript, or awaiting playback."""
        return list(self._responses.values()) + self._awaiting + self._playing

    def _on_truncate(self, item_id: Any, audio_end_ms: Any) -> None:
        """Cap an assistant audio segment at what the listener actually heard.

        Over a WebSocket the client owns playback, and the model streams audio faster than it plays,
        so on a barge-in the client stops its speaker and reports how far it got
        (`conversation.item.truncate`'s `audio_end_ms`). Audio delivered past that point was never
        heard. Without this the stored agent audio - and the agent-speech window derived from it -
        covers the whole generated response and runs past the interruption into the next user turn.

        Seeing a truncation also marks this connection's client as one that cuts playback short, which
        is what makes holding its turns open worthwhile (see `_park_for_playback`).
        """
        self._client_truncates = True
        item = str(item_id) if item_id is not None else None
        end_ms = _as_float(audio_end_ms)
        if item is None or end_ms is None:
            return
        for turn in self._open_turns():
            item_start = turn.audio_item_starts.get(item)
            if item_start is None:
                continue
            # Per turn: an older held turn's audio may have arrived under a format the session has
            # since changed, and the cut point is a byte offset into that turn's own bytes.
            mime, rate = _segment_format(turn.audio, self._output_audio_mime, self._output_audio_rate)
            bytes_per_second = _bytes_per_second(mime, rate)
            if not bytes_per_second:
                return
            cap = item_start + int(end_ms / 1000 * bytes_per_second)
            turn.audio.cap_to(cap - cap % 2)  # keep PCM16 samples whole; a byte is nothing for G.711
            if turn in self._playing:
                # Playback ended when the listener cut it off, so stop waiting on it.
                self._playing.remove(turn)
                self._finalize_turn(turn, force=True)
            return

    def _park_for_playback(self, turn: _ResponseTurn) -> bool:
        """Hold a finished turn while its audio is still playing, so a late truncation can still cap
        it. Returns whether the turn was parked.

        `response.done` normally lands mid-playback (generation outruns playback), and a barge-in
        truncation arrives after that - too late for a turn we already submitted, and the audio bytes
        ride on the llm span. Holding costs submission latency and a wider window to lose the turn if
        the process dies, so we only hold on connections whose client has actually truncated before: a
        client either implements barge-in or it doesn't, so one observed truncation predicts the rest.
        Clients that never truncate hear every byte we captured and finalize at `response.done`
        exactly as before, paying nothing. The cost of that trade is that the first interruption on a
        connection is still reported untruncated.
        """
        if not self._client_truncates or turn.audio.start_ns is None:
            return False
        playback_ns = _segment_duration_ns(
            turn.audio.total_decoded_bytes,
            *_segment_format(turn.audio, self._output_audio_mime, self._output_audio_rate),
        )
        if playback_ns is None:
            return False
        now = time.time_ns()
        end_ns = turn.audio.start_ns + playback_ns
        if end_ns <= now:
            return False
        # Wait for playback to end, but never longer than `_PARK_MAX_NS`. A listener who interrupts
        # does it early - that is what interrupting is - so the cap costs almost no real truncations,
        # while holding a turn for the full playback of a long answer would delay submission by that
        # whole time and widen the window where an idle connection leaves the turn unflushed (nothing
        # is timed; see `_flush_playing`).
        turn.playback_end_ns = min(end_ns, now + _PARK_MAX_NS)
        self._playing.append(turn)
        return True

    def _flush_playing(self, force: bool = False) -> None:
        """Finalize held turns whose audio has finished playing (or all of them, when forced).

        Event-driven rather than timed: a realtime connection is chatty - a streaming client appends
        microphone audio continuously - so this runs often enough to submit a turn shortly after its
        playback ends, and the next turn and connection close both force it.

        A connection that goes idle immediately after a response is the gap: nothing fires, so the
        held turn waits for the next event, for close, or for the connection to be dropped
        (`_finish_session_on_gc`). `_PARK_MAX_NS` bounds how long that can be, and a timed flush is
        deliberately avoided - it would finalize turns off-thread, and this state machine is only
        safe because every path runs on the caller's own thread.
        """
        if not self._playing:
            return
        now = time.time_ns()
        for turn in [t for t in self._playing if force or t.playback_end_ns is None or t.playback_end_ns <= now]:
            self._playing.remove(turn)
            self._finalize_turn(turn, force=True)

    # -- span lifecycle -----------------------------------------------------

    def _start_response(self, response_id: Optional[str]) -> None:
        if response_id is None:
            return
        # A new turn starting means a prior turn's input transcription is almost certainly not coming
        # anymore, and that any held playback is over (or was cut off without a truncation reaching
        # us), so flush both rather than let a span hang.
        self._flush_awaiting()
        self._flush_playing(force=True)
        turn = _ResponseTurn(self._pending_input)
        self._pending_input = _InputTurn()
        turn.model = self._model
        # Turn root (workflow): the whole perceived turn and the root of this turn's trace, grouped by
        # session_id (or nested under the caller's own LLMObs context if there is one). Back-dated to
        # when the user started speaking (or spoke-end) when known.
        try:
            turn.root_span = self._integration.trace(
                "createRealtimeTurn",
                submit_to_llmobs=True,
                instance=SimpleNamespace(_client=self._client),
                activate=False,
            )
            turn_start = turn.input.speech_start_ns or turn.input.audio.start_ns or turn.input.speech_end_ns
            if turn_start is not None:
                turn.root_span.start_ns = int(turn_start)
        except Exception:
            log.debug("error starting realtime turn span", exc_info=True)
            turn.root_span = None
        # LLM span (generation): child of the turn root, back-dated to the end of user speech (buffer
        # commit) so its duration is model work rather than the human's speaking time. Kept as
        # `turn.span` so any child spans nest under it.
        try:
            turn.span = self._integration.trace(
                "createRealtimeResponse",
                instance=SimpleNamespace(_client=self._client),
                activate=False,
                parent_context=turn.root_span,
            )
            llm_start = turn.input.speech_end_ns or turn.input.audio.start_ns
            if llm_start is not None:
                turn.span.start_ns = int(llm_start)
        except Exception:
            log.debug("error starting realtime response span", exc_info=True)
        self._responses[response_id] = turn

    def _finish_response(self, response_id: Optional[str], response: Any) -> None:
        if response_id is None:
            return
        turn = self._responses.pop(response_id, None)
        if turn is None:
            return
        turn.response_done_ns = time.time_ns()
        turn.usage = _get_attr(response, "usage", None)
        turn.model = _get_attr(response, "model", None) or turn.model or self._model
        turn.status = _get_attr(response, "status", None)
        turn.tool_calls, turn.tool_results = _extract_response_tools(response)
        # Remember each function call's name so the function_call_output the app returns later can be
        # labeled with it (the output event itself only carries the call_id).
        for tool_call in turn.tool_calls:
            call_id = tool_call.get("tool_id")
            if call_id and tool_call.get("type") == "function":
                self._tool_call_names[call_id] = tool_call.get("name", "")
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
            self._finalize_turn(turn, force=True)
        self._awaiting = []

    def finish_session(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Finalize anything still open: turns awaiting a transcription or playback, plus in-flight
        # turns that never saw `response.done` (closed mid-turn). Whatever partial data we have is
        # submitted.
        self._flush_awaiting()
        self._flush_playing(force=True)
        for turn in list(self._responses.values()):
            if not turn.input.transcript and turn.input.item_id is not None:
                turn.input.transcript = self._input_transcripts.get(turn.input.item_id, "")
            self._finalize_turn(turn, force=True)
        self._responses.clear()
        self._input_transcripts.clear()
        self._tool_call_names.clear()

    # -- tagging helpers ----------------------------------------------------

    def _finalize_turn(self, turn: _ResponseTurn, force: bool = False) -> None:
        if turn.span is None and turn.root_span is None:
            return
        # The turn's data is complete, but on a barge-in-capable client the agent's audio may still be
        # playing and a truncation may yet cut it short - hold the turn rather than submit audio the
        # listener might never hear. `force` is for the paths that must not wait (close, next turn).
        if not force and self._park_for_playback(turn):
            return
        # Drop the cached transcript for this turn's input item so the map can't grow across a long
        # session (every finalize path goes through here).
        if turn.input.item_id is not None:
            self._input_transcripts.pop(turn.input.item_id, None)
        if turn.status == "failed":
            # Flag the whole turn, not just the generation: the root is the trace root, so leaving it
            # clean surfaces a successful turn containing a failed llm span, and error filters and
            # error-rate metrics that key on the root miss the failure entirely. Outside the tagging
            # guards below so neither one can skip it.
            for span in (turn.span, turn.root_span):
                if span is not None:
                    span.error = 1
        try:
            self._tag_response(turn)
        except Exception:
            log.debug("error tagging realtime response span", exc_info=True)
        # Root and phase spans are additive; a failure tagging them must never drop the llm span, so
        # they get their own guard rather than sharing the llm span's.
        try:
            self._tag_turn_root(turn)
            self._emit_phase_spans(turn)
        except Exception:
            log.debug("error tagging realtime turn/phase spans", exc_info=True)
        finally:
            # LLM span ends when generation completes (response.done); the turn root ends at the
            # latest child end (agent playback end when the turn produced output audio).
            if turn.span is not None:
                self._finish_span_at(turn.span, turn.response_done_ns)
            if turn.root_span is not None:
                self._finish_span_at(turn.root_span, self._turn_end_ns(turn))

    def _tag_turn_root(self, turn: _ResponseTurn) -> None:
        """Tag the turn's workflow root (no parent; it is the root of this turn's trace). Carries the
        turn's user/assistant transcripts as its input/output for a readable waterfall row.

        The name carries the `audio turn` marker (provider-agnostic: `realtime audio turn` here,
        `<provider> audio turn` for future integrations) so the web-ui player and the backend TTFA
        metric can gate on "is this a voice turn?" by name. This is a FE/BE consumer contract - keep
        it in lockstep with them.
        """
        if turn.root_span is None:
            return
        self._integration._llmobs_set_tags_from_realtime_workflow(
            turn.root_span,
            name="realtime audio turn",
            session_id=self._session_id,
            input_value=turn.input.transcript or turn.input.text or None,
            output_value=turn.transcript or turn.text or None,
        )

    def _emit_phase_spans(self, turn: _ResponseTurn) -> None:
        """Emit the user-speech and agent-speech workflow spans that bracket the llm span.

        These are timing regions (the human speaking window and the human hearing window) nested under
        the turn root; the audio bytes themselves ride on the llm span. Each is emitted only when that
        side produced audio (or, for the user, VAD reported speech), so a text-only turn skips
        user-speech and a tool-only turn skips agent-speech.
        """
        root = turn.root_span
        if root is None:
            return
        # VAD speech onset when we have it; the first buffered chunk only approximates the onset for a
        # client that appends audio solely while the user talks (client-side turn detection).
        in_start = turn.input.speech_start_ns or turn.input.audio.start_ns
        if in_start is not None:
            in_end = turn.input.speech_end_ns
            if in_end is None:
                dur = _segment_duration_ns(
                    turn.input.audio.total_decoded_bytes,
                    *_segment_format(turn.input.audio, self._input_audio_mime, self._input_audio_rate),
                )
                in_end = in_start + dur if dur else None
            self._emit_workflow_span(
                "createRealtimeUserSpeech",
                "user speech",
                root,
                in_start,
                in_end,
                output_value=turn.input.transcript or turn.input.text or None,
            )
        out_start = turn.audio.start_ns
        if out_start is not None:
            dur = _segment_duration_ns(
                turn.audio.total_decoded_bytes,
                *_segment_format(turn.audio, self._output_audio_mime, self._output_audio_rate),
            )
            out_end = out_start + dur if dur else turn.response_done_ns
            self._emit_workflow_span(
                "createRealtimeAgentSpeech",
                "agent speech",
                root,
                out_start,
                out_end,
                output_value=turn.transcript or turn.text or None,
            )

    def _emit_workflow_span(
        self,
        operation: str,
        name: str,
        parent_span: Any,
        start_ns: Optional[int],
        end_ns: Optional[int],
        output_value: Any = None,
    ) -> None:
        try:
            span = self._integration.trace(
                operation,
                submit_to_llmobs=True,
                instance=SimpleNamespace(_client=self._client),
                activate=False,
                parent_context=parent_span,
            )
            if start_ns is not None:
                span.start_ns = int(start_ns)
        except Exception:
            log.debug("error starting realtime %s span", name, exc_info=True)
            return
        try:
            self._integration._llmobs_set_tags_from_realtime_workflow(
                span,
                name=name,
                session_id=self._session_id,
                parent_span=parent_span,
                output_value=output_value,
            )
        except Exception:
            log.debug("error tagging realtime %s span", name, exc_info=True)
        finally:
            self._finish_span_at(span, end_ns)

    def _finish_span_at(self, span: Any, end_ns: Optional[int]) -> None:
        """Finish `span` at an absolute unix-ns time when it is a valid end (after the span's start),
        else finish at now. Never lets a bad timestamp raise out of finalize.
        """
        try:
            start_ns = getattr(span, "start_ns", None)
            if end_ns is not None and start_ns is not None and end_ns > start_ns:
                span.finish(finish_time=end_ns / 1e9)
            else:
                span.finish()
        except Exception:
            log.debug("error finishing realtime span", exc_info=True)
            try:
                span.finish()
            except Exception:  # nosec B110 - already logged above; a span we cannot finish is dropped
                pass

    def _turn_end_ns(self, turn: _ResponseTurn) -> Optional[int]:
        """Latest child end for the turn root: agent playback end when there was output audio, else
        response.done, else the user speech end.
        """
        candidates: list[int] = []
        if turn.response_done_ns is not None:
            candidates.append(turn.response_done_ns)
        if turn.audio.start_ns is not None:
            dur = _segment_duration_ns(
                turn.audio.total_decoded_bytes,
                *_segment_format(turn.audio, self._output_audio_mime, self._output_audio_rate),
            )
            if dur:
                candidates.append(turn.audio.start_ns + dur)
        if turn.input.speech_end_ns is not None:
            candidates.append(turn.input.speech_end_ns)
        return max(candidates) if candidates else None

    def _tag_response(self, turn: _ResponseTurn) -> None:
        if turn.span is None:
            return
        input_message = self._build_message(
            "user",
            turn.input.transcript or turn.input.text,
            turn.input.audio,
            *_segment_format(turn.input.audio, self._input_audio_mime, self._input_audio_rate),
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
            *_segment_format(turn.audio, self._output_audio_mime, self._output_audio_rate),
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
            parent_span=turn.root_span,
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
                    self._pending_input.audio.append(audio, self._input_audio_mime, self._input_audio_rate)
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


def _decoded_b64_len(b64: str) -> int:
    """Decoded byte count of a base64 string, without decoding it.

    Three quarters of the encoded length, less one byte per trailing "=". The padding has to come off:
    these counts now drive the speech windows, the per-item offsets and the barge-in cut point, and
    PCM16 chunks are even-length so most of them encode with padding - counted as data it would drift
    the timeline and land the cut on an odd byte, splitting a sample.
    """
    length = len(b64)
    if not length:
        return 0
    padding = 2 if b64.endswith("==") else 1 if b64.endswith("=") else 0
    return (length * 3) // 4 - padding


def _slice_b64(b64: str, decoded_bytes: int) -> tuple[str, int]:
    """Re-encode the first `decoded_bytes` of a base64 chunk, with the size actually taken.

    Base64 can only be sliced directly on 3-byte boundaries, so cutting at an arbitrary offset means
    decoding and re-encoding. Only runs when a truncation lands inside a chunk.
    """
    try:
        raw = base64.b64decode(b64)[:decoded_bytes]
    except (ValueError, TypeError):
        return "", 0
    return base64.b64encode(raw).decode("utf-8"), len(raw)


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _segment_format(audio: "_AudioAccumulator", mime: str, rate: int) -> tuple[str, int]:
    """The audio format to interpret `audio` with: the one recorded when its first chunk arrived,
    falling back to the session's current format.

    The fallback covers a segment that never recorded one - it holds no audio, or the session had not
    announced a format yet - and is not the mutable-format case: a segment that did record a format
    keeps it, so a later `session.update` cannot retime bytes that arrived under the old one.
    """
    return audio.mime or mime, audio.rate or rate


def _bytes_per_second(mime: str, sample_rate: int) -> Optional[int]:
    """Byte rate of a raw audio format: PCM16 is 2 bytes per sample at the session's rate, G.711 is
    1 byte per sample at the fixed 8 kHz rate. Any other or unknown format returns None (we do not
    guess a rate, so durations and offsets derived from it are simply unavailable).
    """
    if is_pcm16_audio_mime(mime) and sample_rate > 0:
        return sample_rate * 2
    if g711_variant(mime) is not None:
        return G711_SAMPLE_RATE
    return None


def _segment_duration_ns(decoded_bytes: int, mime: str, sample_rate: int) -> Optional[int]:
    """Approximate playback duration (in ns) of an audio segment from its decoded byte count. Used to
    size the user-speech and agent-speech workflow spans and the turn root's end.
    """
    if not decoded_bytes or decoded_bytes <= 0:
        return None
    bytes_per_second = _bytes_per_second(mime, sample_rate)
    if not bytes_per_second:
        return None
    return int(decoded_bytes / bytes_per_second * 1_000_000_000)


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


def _finish_session_on_gc(state: "_RealtimeState") -> None:
    """Last-resort finalizer: submit whatever the session still holds when the connection is dropped.

    Runs from a `weakref.finalize` callback (garbage collection, or interpreter exit), so it must
    never raise - by then there is no caller left to handle it.
    """
    try:
        state.finish_session()
    except Exception:
        log.debug("error finalizing realtime session on connection drop", exc_info=True)


def _attach_session(instance: Any, connection: Any) -> None:
    integration = _integration()
    if integration is None:
        return
    try:
        state = _start_realtime_state(
            integration, getattr(instance, "_dd_client", None), getattr(instance, "_dd_model", None)
        )
        connection._dd_realtime_state = state
        # A turn held for playback (see `_park_for_playback`) is only submitted by a later event or
        # by `finish_session`, so a caller that drops the connection without `close()` - and
        # without `recv()` raising ConnectionClosed - would lose that turn entirely. `with` blocks
        # are already covered (the SDK's `__exit__` closes), so this catches the un-managed case and
        # process exit. `finish_session` is idempotent, so it is safe alongside the close paths.
        # The state does not reference the connection, so this creates no cycle that would keep the
        # connection alive.
        weakref.finalize(connection, _finish_session_on_gc, state)
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
