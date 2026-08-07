"""Audio helpers shared by the LLM Observability integrations.

Building ``AudioPart``s, mapping provider audio formats to MIME types, and turning raw audio
(PCM16, G.711) into playable WAV within the per-span-event size budget. Kept separate from the
larger ``utils.py`` since the audio surface has grown (chat audio, realtime, telephony).
"""

import base64
import io
import struct
from typing import Any
from typing import Optional
from typing import Union
import wave

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs.types import AudioPart


logger = get_logger(__name__)


def format_audio_part(data: Union[bytes, str], mime_type: str) -> AudioPart:
    """Build an ``AudioPart`` from raw audio bytes (base64-encoded) or an existing base64 string."""
    content = base64.b64encode(data).decode("utf-8") if isinstance(data, bytes) else data
    return AudioPart(mime_type=mime_type, content=content)


# OpenAI audio ``format`` values that don't map to ``audio/<format>``.
_OPENAI_AUDIO_MIME_TYPES = {
    "mp3": "audio/mpeg",
}


def audio_mime_type_from_format(fmt: str) -> str:
    """Map an OpenAI audio ``format`` (e.g. "wav", "mp3") to a MIME type."""
    fmt = (fmt or "").strip().lower()
    return _OPENAI_AUDIO_MIME_TYPES.get(fmt, "audio/{}".format(fmt) if fmt else "audio/wav")


# Raw audio formats the UI cannot render as a player. For these we keep the transcript as the
# message content and skip the inline audio_part (raw bytes would only bloat the payload).
_NON_RENDERABLE_AUDIO_MIME_TYPES = frozenset(
    {
        "audio/pcm",
        "audio/pcm16",
        "audio/l16",
        "audio/pcmu",
        "audio/pcma",
        "audio/g711_ulaw",
        "audio/g711_alaw",
        "audio/basic",
    }
)

# Budget for inline audio measured on the *base64-encoded* size (what actually rides the span
# event), so the guard reflects the real payload after encoding. Kept below the 5 MB per-span-event
# limit with headroom for the rest of the event (oversize events have their whole I/O dropped
# backend-side). 4 MiB encoded ≈ 3 MiB of raw audio.
LLMOBS_AUDIO_INLINE_MAX_BYTES = 4 * 1024 * 1024


def _base64_encoded_len(num_bytes: int) -> int:
    """Length of ``num_bytes`` after standard base64 encoding (4 chars per 3 bytes, padded)."""
    return ((num_bytes + 2) // 3) * 4


# OpenAI Realtime audio ``format`` values (legacy string form) that don't map to ``audio/<format>``.
_REALTIME_AUDIO_FORMAT_MIME_TYPES = {
    "pcm16": "audio/pcm",
    "pcm": "audio/pcm",
    "g711_ulaw": "audio/pcmu",
    "g711_alaw": "audio/pcma",
}


def realtime_audio_format_to_mime(fmt: Any) -> str:
    """Map an OpenAI Realtime audio format to a MIME type.

    Handles both the legacy string form (e.g. "pcm16", "g711_ulaw") and the newer
    discriminated-union object whose ``type`` is already a MIME type (e.g. "audio/pcm").
    """
    if fmt is None:
        return ""
    type_attr = _get_attr(fmt, "type", None)
    if type_attr:
        fmt = type_attr
    normalized = str(fmt).strip().lower()
    if not normalized:
        return ""
    if normalized.startswith("audio/"):
        return normalized
    return _REALTIME_AUDIO_FORMAT_MIME_TYPES.get(normalized, "audio/{}".format(normalized))


def is_renderable_audio_mime(mime_type: str) -> bool:
    """Whether a MIME type can be rendered as an audio player in the UI (raw PCM cannot)."""
    return bool(mime_type) and mime_type.strip().lower() not in _NON_RENDERABLE_AUDIO_MIME_TYPES


def concat_base64_audio(b64_chunks: "list[str]") -> bytes:
    """Decode and concatenate a list of base64 audio chunks into raw bytes.

    Chunks must be decoded before concatenation: directly joining base64 strings is invalid
    unless each chunk is aligned on a 3-byte boundary.
    """
    buf = bytearray()
    for chunk in b64_chunks:
        if not chunk:
            continue
        try:
            buf.extend(base64.b64decode(chunk))
        except (ValueError, TypeError):
            continue
    return bytes(buf)


# Raw little-endian PCM16 mime types. These aren't renderable on their own, but can be losslessly
# wrapped in a WAV container (just a header) to produce a playable ``audio/wav`` part.
_PCM16_MIME_TYPES = frozenset({"audio/pcm", "audio/pcm16", "audio/l16"})


def is_pcm16_audio_mime(mime_type: str) -> bool:
    return bool(mime_type) and mime_type.strip().lower() in _PCM16_MIME_TYPES


def pcm16_to_wav(pcm_bytes: bytes, sample_rate: int = 24000, channels: int = 1) -> bytes:
    """Wrap raw little-endian PCM16 audio in a WAV container.

    This is lossless and cheap (it only prepends a header), and turns raw PCM — which the UI can't
    render — into a playable ``audio/wav`` payload.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)  # PCM16 = 2 bytes/sample
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return buf.getvalue()


# G.711 telephony audio (8kHz, 8-bit companded). Realtime uses these for phone-call integrations
# (Twilio/SIP). We decode to PCM16 so it can be WAV-wrapped and played in the UI; the stdlib `wave`
# module can't emit G.711-tagged WAV, and `audioop` (ulaw2lin/alaw2lin) was removed in Python 3.13.
_G711_MIME_TO_VARIANT = {
    "audio/pcmu": "ulaw",
    "audio/g711_ulaw": "ulaw",
    "audio/pcma": "alaw",
    "audio/g711_alaw": "alaw",
}
G711_SAMPLE_RATE = 8000  # G.711 is always 8kHz mono.


def g711_variant(mime_type: str) -> Optional[str]:
    """Return "ulaw"/"alaw" for a G.711 MIME type, else ``None``."""
    return _G711_MIME_TO_VARIANT.get((mime_type or "").strip().lower())


def _ulaw_decode_sample(byte: int) -> int:
    """Decode one G.711 μ-law byte to a signed 16-bit linear PCM sample (CCITT G.711)."""
    byte = ~byte & 0xFF
    sample = ((byte & 0x0F) << 3) + 0x84
    sample <<= (byte & 0x70) >> 4
    return (0x84 - sample) if (byte & 0x80) else (sample - 0x84)


def _alaw_decode_sample(byte: int) -> int:
    """Decode one G.711 A-law byte to a signed 16-bit linear PCM sample (CCITT G.711)."""
    byte ^= 0x55
    sample = (byte & 0x0F) << 4
    seg = (byte & 0x70) >> 4
    if seg == 0:
        sample += 8
    elif seg == 1:
        sample += 0x108
    else:
        sample = (sample + 0x108) << (seg - 1)
    return sample if (byte & 0x80) else -sample


# Precompute 256-entry decode tables (the input domain is a single byte).
_ULAW_TABLE = [_ulaw_decode_sample(i) for i in range(256)]
_ALAW_TABLE = [_alaw_decode_sample(i) for i in range(256)]


def g711_to_pcm16(data: bytes, variant: str) -> bytes:
    """Decode G.711 ("ulaw"/"alaw") bytes to raw little-endian PCM16."""
    table = _ALAW_TABLE if variant == "alaw" else _ULAW_TABLE
    return b"".join(struct.pack("<h", table[b]) for b in data)


def format_audio_part_with_guard(
    audio_bytes: bytes, mime_type: str, max_bytes: int = LLMOBS_AUDIO_INLINE_MAX_BYTES
) -> Optional[AudioPart]:
    """Build a playable ``AudioPart`` only for renderable formats within the size budget.

    Returns ``None`` for non-renderable formats (e.g. raw PCM) or oversize audio; callers should
    fall back to the transcript as the message content in that case.
    """
    if not audio_bytes or not is_renderable_audio_mime(mime_type):
        return None
    # Compare the *encoded* size: format_audio_part base64-encodes the bytes (~4/3 expansion), and
    # that encoded content is what counts against the per-span-event limit.
    encoded_len = _base64_encoded_len(len(audio_bytes))
    if encoded_len > max_bytes:
        logger.debug(
            "Audio (%d encoded bytes) exceeds inline budget %d; omitting inline audio content", encoded_len, max_bytes
        )
        return None
    return format_audio_part(audio_bytes, mime_type)
