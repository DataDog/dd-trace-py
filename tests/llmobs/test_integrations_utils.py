import base64
from types import SimpleNamespace

import pytest

from ddtrace.ext import SpanTypes
from ddtrace.internal.evp_proxy.constants import DEFAULT_EVP_EVENT_SIZE_LIMIT
from ddtrace.llmobs._constants import IMAGE_FALLBACK_MARKER
from ddtrace.llmobs._constants import IMAGE_TOO_LARGE_MARKER
from ddtrace.llmobs._constants import PROMPT_MULTIMODAL
from ddtrace.llmobs._integrations.agent_manifest import MAX_WIRE_DEPTH
from ddtrace.llmobs._integrations.agent_manifest import is_number
from ddtrace.llmobs._integrations.agent_manifest import prune_empty
from ddtrace.llmobs._integrations.agent_manifest import wire_value
from ddtrace.llmobs._integrations.audio_utils import audio_mime_type_from_format
from ddtrace.llmobs._integrations.audio_utils import concat_base64_audio
from ddtrace.llmobs._integrations.audio_utils import format_audio_part
from ddtrace.llmobs._integrations.audio_utils import format_audio_part_with_guard
from ddtrace.llmobs._integrations.audio_utils import g711_to_pcm16
from ddtrace.llmobs._integrations.audio_utils import g711_variant
from ddtrace.llmobs._integrations.audio_utils import is_pcm16_audio_mime
from ddtrace.llmobs._integrations.audio_utils import is_renderable_audio_mime
from ddtrace.llmobs._integrations.audio_utils import pcm16_to_wav
from ddtrace.llmobs._integrations.audio_utils import realtime_audio_format_to_mime
from ddtrace.llmobs._integrations.utils import _capture_inline_image
from ddtrace.llmobs._integrations.utils import _encoded_image_len
from ddtrace.llmobs._integrations.utils import _extract_chat_template_from_instructions
from ddtrace.llmobs._integrations.utils import _extract_content_parts
from ddtrace.llmobs._integrations.utils import _inline_image_budget
from ddtrace.llmobs._integrations.utils import _normalize_prompt_variables
from ddtrace.llmobs._integrations.utils import _openai_parse_input_response_messages
from ddtrace.llmobs._integrations.utils import _openai_parse_output_response_messages
from ddtrace.llmobs._integrations.utils import format_image_part
from ddtrace.llmobs._integrations.utils import format_image_part_with_guard
from ddtrace.llmobs._integrations.utils import get_messages_from_anthropic_content
from ddtrace.llmobs._integrations.utils import get_tool_definitions_from_anthropic_tools
from ddtrace.llmobs._integrations.utils import is_renderable_image_mime
from ddtrace.llmobs._integrations.utils import openai_construct_message_from_streamed_chunks
from ddtrace.llmobs._integrations.utils import openai_construct_tool_call_from_streamed_chunk
from ddtrace.llmobs._integrations.utils import openai_set_meta_tags_from_chat
from ddtrace.llmobs._integrations.utils import openai_set_meta_tags_from_response
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import get_llmobs_input_messages
from ddtrace.llmobs._utils import get_llmobs_input_prompt
from ddtrace.llmobs._utils import get_llmobs_tags
from ddtrace.llmobs._utils import safe_json
from tests.utils import override_global_config


def test_format_audio_part_from_bytes():
    """Raw bytes are base64-encoded into an AudioPart with the given mime type."""
    raw = b"\x00\x01\x02\x03"
    part = format_audio_part(raw, "audio/wav")
    assert part == {"mime_type": "audio/wav", "content": base64.b64encode(raw).decode("utf-8")}


def test_format_audio_part_from_base64_string():
    """An already-encoded base64 string is passed through unchanged."""
    part = format_audio_part("AAECAw==", "audio/mp3")
    assert part == {"mime_type": "audio/mp3", "content": "AAECAw=="}


def test_format_image_part_from_bytes():
    """Raw bytes are base64-encoded into an ImagePart with the given mime type."""
    raw = b"\x00\x01\x02\x03"
    part = format_image_part(raw, "image/png")
    assert part == {"mime_type": "image/png", "content": base64.b64encode(raw).decode("utf-8")}


def test_format_image_part_from_base64_string():
    """An already-encoded base64 string is passed through unchanged."""
    part = format_image_part("AAECAw==", "image/jpeg")
    assert part == {"mime_type": "image/jpeg", "content": "AAECAw=="}


def _data_url(b64, mime_type="image/png"):
    return "data:{};base64,{}".format(mime_type, b64)


# Just over the live budget, so the oversize tests exercise the configured value, not a literal.
_OVERSIZE_B64 = "A" * (_inline_image_budget() + 4)


def test_audio_mime_type_from_format():
    """OpenAI audio formats map to MIME types, falling back to audio/<format>."""
    assert audio_mime_type_from_format("wav") == "audio/wav"
    assert audio_mime_type_from_format("mp3") == "audio/mpeg"
    assert audio_mime_type_from_format("FLAC") == "audio/flac"
    assert audio_mime_type_from_format("opus") == "audio/opus"
    assert audio_mime_type_from_format("  MP3 ") == "audio/mpeg"  # whitespace + case insensitive
    assert audio_mime_type_from_format("") == "audio/wav"


def test_extract_content_parts_collects_audio():
    """Captured input_audio becomes an AudioPart and leaves no '[audio]' text marker behind."""
    text, audio_parts, _ = _extract_content_parts(
        [
            {"type": "text", "text": "what is said here?"},
            {"type": "input_audio", "input_audio": {"data": "AAECAw==", "format": "mp3"}},
        ]
    )
    assert text == "what is said here?"
    assert audio_parts == [{"mime_type": "audio/mpeg", "content": "AAECAw=="}]


def test_extract_content_parts_multiple_audio_only():
    """A message with only input_audio parts captures each as an AudioPart and has empty text."""
    text, audio_parts, _ = _extract_content_parts(
        [
            {"type": "input_audio", "input_audio": {"data": "AAA=", "format": "wav"}},
            {"type": "input_audio", "input_audio": {"data": "BBB=", "format": "mp3"}},
        ]
    )
    assert text == ""
    assert audio_parts == [
        {"mime_type": "audio/wav", "content": "AAA="},
        {"mime_type": "audio/mpeg", "content": "BBB="},
    ]


def test_extract_content_parts_audio_marker_fallback_when_no_data():
    """When an input_audio part carries no data, fall back to the '[audio]' text marker."""
    text, audio_parts, _ = _extract_content_parts(
        [
            {"type": "text", "text": "listen:"},
            {"type": "input_audio", "input_audio": {"format": "wav"}},
        ]
    )
    assert text == "listen:\n[audio]"
    assert audio_parts == []


def test_format_image_part_with_guard_within_budget():
    """A renderable inline image within the cap is captured; a base64 string passes through unchanged."""
    assert format_image_part_with_guard("iVBORw0KGgo=", "image/png") == {
        "mime_type": "image/png",
        "content": "iVBORw0KGgo=",
    }
    # Inclusive at the cap, and the payload is captured whole (not truncated).
    part = format_image_part_with_guard("A" * 10, "image/png", max_bytes=10)
    assert part == {"mime_type": "image/png", "content": "A" * 10}


def test_format_image_part_with_guard_rejects_oversized():
    """Over the cap returns None, for an already-encoded string and for raw bytes alike."""
    assert format_image_part_with_guard("A" * 11, "image/png", max_bytes=10) is None
    # Raw bytes are sized once encoded (~4/3), so 9 raw bytes -> 12 encoded, over a 10-byte cap.
    assert format_image_part_with_guard(b"\x00" * 9, "image/png", max_bytes=10) is None
    assert format_image_part_with_guard(b"\x00" * 6, "image/png", max_bytes=10) is not None


def test_format_image_part_with_guard_sizes_non_ascii_by_bytes():
    """A non-ASCII str is sized by its UTF-8 bytes, not its character count.

    Real base64 is ASCII so the two agree, but sizing by len() would let a caller passing non-ASCII
    text through the guard understate its wire cost up to 4x and blow the per-event limit anyway.
    """
    four_byte_chars = "\U0001f600" * 3  # 3 chars, 12 UTF-8 bytes
    assert _encoded_image_len(four_byte_chars) == 12
    assert format_image_part_with_guard(four_byte_chars, "image/png", max_bytes=11) is None
    assert format_image_part_with_guard(four_byte_chars, "image/png", max_bytes=12) is not None


def test_format_image_part_with_guard_rejects_unrenderable_mime():
    """A non-image or unrenderable MIME type is not captured -- inline bytes the UI can't draw are waste,
    and this keeps caller-supplied mime strings from riding the span. Mirrors the audio guard.
    """
    assert format_image_part_with_guard("PHNjcmlwdD4=", "text/html") is None
    assert format_image_part_with_guard("PHN2Zz4=", "image/svg+xml") is None
    assert format_image_part_with_guard("QUJD", "") is None
    # The four renderable types (and Anthropic's whole media_type Literal) are accepted, case/space loose.
    for mime in ("image/png", "image/jpeg", "image/gif", "image/webp", "  IMAGE/PNG "):
        assert is_renderable_image_mime(mime), mime


def test_extract_content_parts_no_audio():
    """Text/image-only content yields no audio parts."""
    text, audio_parts, _ = _extract_content_parts(
        [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": "http://example.com/x.png"},
        ]
    )
    assert text == "hello\n[image]"
    assert audio_parts == []


def test_extract_content_parts_captures_inline_image():
    """An inline base64 data URL becomes an ImagePart and leaves no '[image]' text marker."""
    text, _, image_parts = _extract_content_parts(
        [
            {"type": "text", "text": "what is in this image?"},
            {"type": "image_url", "image_url": {"url": _data_url("AAECAw==")}},
        ]
    )
    assert text == "what is in this image?"
    assert image_parts == [{"mime_type": "image/png", "content": "AAECAw=="}]


def test_extract_content_parts_captures_inline_image_bare_string():
    """The URL may arrive as a bare string rather than the nested image_url.url object."""
    _, _, image_parts = _extract_content_parts(
        [{"type": "image_url", "image_url": _data_url("BBBB", mime_type="image/webp")}]
    )
    assert image_parts == [{"mime_type": "image/webp", "content": "BBBB"}]


def test_extract_content_parts_multiple_inline_images():
    """Each inline image is captured as its own part, preserving order and per-part mime type."""
    _, _, image_parts = _extract_content_parts(
        [
            {"type": "image_url", "image_url": {"url": _data_url("AAA=", mime_type="image/png")}},
            {"type": "image_url", "image_url": {"url": _data_url("BBB=", mime_type="image/jpeg")}},
        ]
    )
    assert image_parts == [
        {"mime_type": "image/png", "content": "AAA="},
        {"mime_type": "image/jpeg", "content": "BBB="},
    ]


def test_extract_content_parts_oversize_inline_image_keeps_marker_and_text():
    """An oversize inline image is dropped to a distinct marker; surrounding text is untouched."""
    text, _, image_parts = _extract_content_parts(
        [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": _data_url(_OVERSIZE_B64)}},
            {"type": "text", "text": "in one word"},
        ]
    )
    assert text == "describe this\n{}\nin one word".format(IMAGE_TOO_LARGE_MARKER)
    assert image_parts == []


def test_extract_content_parts_non_inline_image_keeps_generic_marker():
    """Remote URLs, missing URLs and malformed/non-image data URLs are not captured."""
    for image_url in (
        "https://example.com/x.png",
        {"url": "https://example.com/x.png"},
        {"url": "data:image/png;base64,"},  # no payload
        {"url": "data:image/png;base64,   \n "},  # whitespace-only payload
        {"url": "data:image/png;base64,not base64!!!<svg/>"},  # payload isn't base64
        {"url": "data:application/pdf;base64,AAA="},  # not an image
        {"url": "data:image/png,AAA="},  # not base64
        {"url": ""},
        {},
        None,
    ):
        text, _, image_parts = _extract_content_parts([{"type": "image_url", "image_url": image_url}])
        assert text == "[image]", image_url
        assert image_parts == [], image_url


def test_extract_content_parts_wrapped_base64_payload_is_normalized():
    """Whitespace used to wrap base64 across lines is stripped, not carried into the part."""
    _, _, image_parts = _extract_content_parts(
        [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA\nEC\nAw=="}}]
    )
    assert image_parts == [{"mime_type": "image/png", "content": "AAECAw=="}]


def test_extract_content_parts_data_url_variants_captured():
    """Extra media-type params, uppercase scheme and svg+xml are all still inline base64."""
    for url, expected_mime in (
        ("data:image/png;charset=utf-8;base64,AAA=", "image/png"),
        ("DATA:IMAGE/PNG;BASE64,AAA=", "image/png"),
        ("data:image/svg+xml;base64,AAA=", "image/svg+xml"),
    ):
        _, _, image_parts = _extract_content_parts([{"type": "image_url", "image_url": {"url": url}}])
        assert image_parts == [{"mime_type": expected_mime, "content": "AAA="}], url


def test_multiple_in_budget_images_can_still_exceed_the_event_size_limit():
    """Pins the known limit of a PER-IMAGE guard: it does not bound the event.

    Two images that each pass the guard still serialize past the per-event limit, at which point
    the writer drops the span's whole input and output. A cumulative per-request budget is the
    deliberate follow-up; this test exists so that gap cannot be mistaken for a guarantee.
    """
    half_budget_b64 = "A" * (_inline_image_budget() // 2)

    _, _, image_parts = _extract_content_parts(
        [
            {"type": "text", "text": "compare these"},
            {"type": "image_url", "image_url": {"url": _data_url(half_budget_b64)}},
            {"type": "image_url", "image_url": {"url": _data_url(half_budget_b64)}},
            {"type": "image_url", "image_url": {"url": _data_url(half_budget_b64)}},
        ]
    )
    assert len(image_parts) == 3
    assert len(safe_json(image_parts)) > DEFAULT_EVP_EVENT_SIZE_LIMIT


def test_capture_inline_image_rejects_oversize_at_every_scale():
    """Oversize inline images degrade to the marker whether barely or hugely over budget.

    Line-wrapped payloads must be measured after whitespace is stripped, so a wrapped image that
    fits once normalized is still captured.
    """
    assert _capture_inline_image(_data_url(_OVERSIZE_B64)) == (None, IMAGE_TOO_LARGE_MARKER)
    huge = _data_url("A" * (16 * _inline_image_budget()))
    assert _capture_inline_image(huge) == (None, IMAGE_TOO_LARGE_MARKER)

    wrapped = "\n".join(["A" * 76] * 8)  # MIME-style line wrapping, comfortably in budget
    part, marker = _capture_inline_image(_data_url(wrapped))
    assert marker is None
    assert part == {"mime_type": "image/png", "content": "A" * (76 * 8)}


def test_is_data_url_detects_scheme_after_long_leading_whitespace():
    """Leading whitespace of any length must not hide the scheme and let the payload reach text."""
    payload = "A" * 4096
    assert _capture_inline_image(_data_url(payload))[0] is not None  # unpadded still captures

    for pad in (" ", "\n" * 40, " \t\n" * 30):
        url = pad + _data_url(payload)
        # Whitespace defeats the regex's ^data: anchor, so this degrades to a marker rather than
        # being captured -- what matters is that the payload never reaches the text.
        assert _capture_inline_image(url) == (None, IMAGE_FALLBACK_MARKER), len(pad)
        text, _, _ = _extract_content_parts([{"type": "image_url", "image_url": {"url": url}}])
        assert payload not in text, len(pad)


def test_inline_image_budget_follows_configured_event_size_limit():
    """The guard must track DD_LLMOBS_EVENT_SIZE_BYTES, not a fixed 4 MiB.

    A lower configured limit means an image the fixed budget would admit is larger than the whole
    event allowance, so the writer would drop the span's entire input and output.
    """
    small = "A" * 200_000
    part, marker = _capture_inline_image(_data_url(small))
    assert marker is None and part is not None

    with override_global_config(dict(_llmobs_event_size_limit=100_000)):
        part, marker = _capture_inline_image(_data_url(small))
        assert (part, marker) == (None, IMAGE_TOO_LARGE_MARKER)


def test_oversize_marker_is_only_used_for_size():
    """The too-large marker must never stand in for another rejection reason.

    The shared guard also returns None for a bad mime or empty data; if the caller read size off
    that None, a customer would be told an image was too large when it never was.
    """
    for url in ("data:image/png;base64,", "data:application/pdf;base64,AAA=", "data:image/png,AAA="):
        assert _capture_inline_image(url)[1] == IMAGE_FALLBACK_MARKER, url


def test_capture_inline_image_never_leaks_unparsed_data_url_as_text():
    """Invariant: a data URL we cannot parse degrades to a marker, never to the raw payload.

    Leaking it would put the whole base64 blob in the message content — the bug this guards.
    """
    unparsable = "data:image/png;base64" + "A" * 64  # no "," separator
    part, marker = _capture_inline_image(unparsable)
    assert part is None
    assert marker == "[image]"
    _, _, image_parts = _extract_content_parts([{"type": "image_url", "image_url": {"url": unparsable}}])
    assert image_parts == []


def test_realtime_audio_format_to_mime_legacy_strings():
    """Legacy string realtime formats map to MIME types."""
    assert realtime_audio_format_to_mime("pcm16") == "audio/pcm"
    assert realtime_audio_format_to_mime("pcm") == "audio/pcm"
    assert realtime_audio_format_to_mime("g711_ulaw") == "audio/pcmu"
    assert realtime_audio_format_to_mime("g711_alaw") == "audio/pcma"
    assert realtime_audio_format_to_mime("wav") == "audio/wav"
    assert realtime_audio_format_to_mime("") == ""
    assert realtime_audio_format_to_mime(None) == ""


def test_realtime_audio_format_to_mime_object_form():
    """The newer discriminated-union object form carries a MIME type in its ``type`` field."""
    assert realtime_audio_format_to_mime(SimpleNamespace(type="audio/pcm")) == "audio/pcm"
    assert realtime_audio_format_to_mime({"type": "audio/pcmu"}) == "audio/pcmu"
    assert realtime_audio_format_to_mime({"type": "AUDIO/WAV"}) == "audio/wav"


def test_is_renderable_audio_mime():
    """Raw PCM family formats are not renderable; common encoded formats are."""
    assert is_renderable_audio_mime("audio/wav")
    assert is_renderable_audio_mime("audio/mpeg")
    assert not is_renderable_audio_mime("audio/pcm")
    assert not is_renderable_audio_mime("audio/pcmu")
    assert not is_renderable_audio_mime("audio/pcma")
    assert not is_renderable_audio_mime("")


def test_concat_base64_audio():
    """Base64 chunks are decoded then concatenated at the byte level."""
    chunk1 = base64.b64encode(b"\x00\x01").decode("utf-8")
    chunk2 = base64.b64encode(b"\x02\x03\x04").decode("utf-8")
    assert concat_base64_audio([chunk1, chunk2]) == b"\x00\x01\x02\x03\x04"
    assert concat_base64_audio([]) == b""
    # Invalid chunks are skipped rather than raising.
    assert concat_base64_audio([chunk1, "!!!notb64", chunk2]) == b"\x00\x01\x02\x03\x04"


def test_format_audio_part_with_guard_renderable():
    """A renderable format within budget yields an inline AudioPart."""
    raw = b"\x00\x01\x02\x03"
    part = format_audio_part_with_guard(raw, "audio/wav")
    assert part == {"mime_type": "audio/wav", "content": base64.b64encode(raw).decode("utf-8")}


def test_format_audio_part_with_guard_non_renderable():
    """Raw PCM is not renderable, so no inline AudioPart is emitted."""
    assert format_audio_part_with_guard(b"\x00\x01\x02\x03", "audio/pcm") is None


def test_format_audio_part_with_guard_oversize():
    """Audio over the byte budget is dropped to respect the per-span-event size limit."""
    assert format_audio_part_with_guard(b"\x00" * 100, "audio/wav", max_bytes=10) is None


def test_format_audio_part_with_guard_uses_encoded_size():
    """The guard measures base64-encoded size: 8 raw bytes -> 12 encoded, over a 10-byte budget."""
    # Under the budget by raw size (8 <= 10) but over once base64-encoded (12 > 10) -> dropped.
    assert format_audio_part_with_guard(b"\x00" * 8, "audio/wav", max_bytes=10) is None


def test_g711_variant():
    """G.711 MIME types resolve to their companding variant; others are None."""
    assert g711_variant("audio/pcmu") == "ulaw"
    assert g711_variant("audio/g711_ulaw") == "ulaw"
    assert g711_variant("audio/pcma") == "alaw"
    assert g711_variant("audio/g711_alaw") == "alaw"
    assert g711_variant("AUDIO/PCMU") == "ulaw"
    assert g711_variant("audio/pcm") is None
    assert g711_variant("") is None


def test_g711_to_pcm16_decodes():
    """G.711 bytes decode to little-endian PCM16 (2 bytes/sample) with the standard zero values."""
    import struct

    # μ-law 0xFF and A-law 0xD5 are the encodings of (near-)silence.
    assert struct.unpack("<h", g711_to_pcm16(b"\xff", "ulaw"))[0] == 0
    assert struct.unpack("<h", g711_to_pcm16(b"\xd5", "alaw"))[0] == 8
    # One output sample (2 bytes) per input byte; μ-law and A-law differ for the same byte.
    assert len(g711_to_pcm16(b"\x01\x02\x03", "ulaw")) == 6
    assert g711_to_pcm16(b"\x12\x34", "ulaw") != g711_to_pcm16(b"\x12\x34", "alaw")


def test_format_audio_part_with_guard_empty():
    """No audio bytes yields no AudioPart."""
    assert format_audio_part_with_guard(b"", "audio/wav") is None


def test_is_pcm16_audio_mime():
    """PCM16 mime types are recognized; G.711 and encoded formats are not."""
    assert is_pcm16_audio_mime("audio/pcm")
    assert is_pcm16_audio_mime("audio/pcm16")
    assert is_pcm16_audio_mime("audio/l16")
    assert is_pcm16_audio_mime("AUDIO/PCM")
    assert not is_pcm16_audio_mime("audio/pcmu")
    assert not is_pcm16_audio_mime("audio/wav")
    assert not is_pcm16_audio_mime("")


def test_pcm16_to_wav_wraps_in_container():
    """pcm16_to_wav prepends a valid RIFF/WAVE header and preserves the PCM payload losslessly."""
    import io
    import wave

    pcm = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    wav_bytes = pcm16_to_wav(pcm, sample_rate=24000, channels=1)
    assert wav_bytes[:4] == b"RIFF"
    assert wav_bytes[8:12] == b"WAVE"
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 24000
        assert wav_file.readframes(wav_file.getnframes()) == pcm


def test_chat_streamed_output_does_not_leak_tool_results_into_input(tracer):
    """Regression: the streamed-output branch must use its own tool_results, not the input loop's.

    ReAct content in a streamed output previously appended to the last input message's tool_results
    list (the variable was discarded with ``_`` and a stale value leaked through), corrupting input.
    """
    react = "Action: search\nAction Input: weather\nObservation: {}"
    kwargs = {"messages": [{"role": "user", "content": react.format("from-input")}]}
    streamed_output = [{"role": "assistant", "content": react.format("from-output")}]
    with tracer.trace("openai.request", span_type=SpanTypes.LLM) as span:
        _annotate_llmobs_span_data(span, kind="llm")  # route input/output as messages, as the integration does
        openai_set_meta_tags_from_chat(span, kwargs, streamed_output)
        input_messages = get_llmobs_input_messages(span)

    assert len(input_messages) == 1
    tool_results = input_messages[0].get("tool_results", [])
    assert len(tool_results) == 1
    assert tool_results[0]["result"] == "from-input"


def test_basic_functionality():
    """Test basic variable replacement with multiple instructions and roles."""
    instructions = [
        {
            "role": "developer",
            "content": [{"text": "Be helpful"}],
        },
        {
            "role": "user",
            "content": [{"text": "Hello John, your email is john@example.com"}],
        },
    ]
    variables = {
        "name": "John",
        "email": "john@example.com",
    }

    result = _extract_chat_template_from_instructions(instructions, variables)

    assert len(result) == 2
    assert result[0]["role"] == "developer"
    assert result[0]["content"] == "Be helpful"
    assert result[1]["role"] == "user"
    assert result[1]["content"] == "Hello {{name}}, your email is {{email}}"


def test_overlapping_values_and_partial_matches():
    """Test longest-first matching for overlaps and partial word matches."""
    # Test 1: Overlapping values - longest should win
    instructions = [
        {
            "role": "user",
            "content": [{"text": "The phrase is: AI is cool"}],
        }
    ]
    variables = {"short": "AI", "long": "AI is cool"}
    result = _extract_chat_template_from_instructions(instructions, variables)
    assert result[0]["content"] == "The phrase is: {{long}}"

    # Test 2: Partial word matches should work (e.g., "test" inside "testing")
    instructions = [
        {
            "role": "user",
            "content": [{"text": "We are testing the feature"}],
        }
    ]
    variables = {"action": "test"}
    result = _extract_chat_template_from_instructions(instructions, variables)
    assert result[0]["content"] == "We are {{action}}ing the feature"


def test_special_characters_and_escaping():
    """Test that special characters are handled correctly."""
    instructions = [
        {
            "role": "user",
            "content": [{"text": "The price is $99.99 (plus $5.00 tax)"}],
        }
    ]
    variables = {"price": "$99.99", "tax": "$5.00"}

    result = _extract_chat_template_from_instructions(instructions, variables)

    assert result[0]["content"] == "The price is {{price}} (plus {{tax}} tax)"


def test_empty_and_edge_cases():
    """Test empty variables, empty values, and malformed instructions."""
    # Empty variables dict
    instructions = [{"role": "user", "content": [{"text": "No variables"}]}]
    result = _extract_chat_template_from_instructions(instructions, {})
    assert result[0]["content"] == "No variables"

    # Empty variable values are skipped
    instructions = [{"role": "user", "content": [{"text": "Hello world"}]}]
    result = _extract_chat_template_from_instructions(instructions, {"empty": "", "greeting": "Hello"})
    assert result[0]["content"] == "{{greeting}} world"

    # Instructions without role or content are skipped
    instructions = [
        {"content": [{"text": "No role"}]},
        {"role": "developer", "content": []},
        {"role": "user", "content": [{"text": "Valid"}]},
    ]
    result = _extract_chat_template_from_instructions(instructions, {})
    assert len(result) == 1
    assert result[0]["role"] == "user"


def test_response_input_text_objects():
    """Test handling of ResponseInputText objects with .text attribute."""

    class ResponseInputText:
        def __init__(self, text):
            self.text = text

    instructions = [
        {
            "role": "user",
            "content": [
                {"text": "Part one "},
                {"text": "Question: What is AI?"},
            ],
        }
    ]
    variables = {"question": ResponseInputText("What is AI?")}

    # Normalize variables before extraction (as done in openai_set_meta_tags_from_response)
    normalized_vars = _normalize_prompt_variables(variables)
    result = _extract_chat_template_from_instructions(instructions, normalized_vars)

    # Also tests that multiple content items are concatenated
    assert result[0]["content"] == "Part one Question: {{question}}"


def test_normalize_prompt_variables():
    """Test normalization of complex variable types."""

    class ResponseInputText:
        def __init__(self, text):
            self.text = text

    class ResponseInputImage:
        def __init__(self, image_url=None, file_id=None):
            self.type = "input_image"
            self.image_url = image_url
            self.file_id = file_id

    class ResponseInputFile:
        def __init__(self, file_url=None, file_id=None, filename=None, file_data=None):
            self.type = "input_file"
            self.file_url = file_url
            self.file_id = file_id
            self.filename = filename
            self.file_data = file_data

    variables = {
        "plain_string": "hello",
        "text_obj": ResponseInputText("world"),
        "image_url": ResponseInputImage(image_url="https://example.com/img.png"),
        "image_file": ResponseInputImage(file_id="file-123"),
        "image_fallback": ResponseInputImage(),
        "file_url": ResponseInputFile(file_url="https://example.com/doc.pdf"),
        "file_name": ResponseInputFile(filename="report.pdf"),
        "file_data": ResponseInputFile(file_data="Some content"),
        "file_fallback": ResponseInputFile(),
    }

    result = _normalize_prompt_variables(variables)

    assert result["plain_string"] == "hello"
    assert result["text_obj"] == "world"
    assert result["image_url"] == "https://example.com/img.png"
    assert result["image_file"] == "file-123"
    assert result["image_fallback"] == "[image]"
    assert result["file_url"] == "https://example.com/doc.pdf"
    assert result["file_name"] == "report.pdf"
    assert result["file_data"] == "[file]"
    assert result["file_fallback"] == "[file]"


def test_output_image_generation_call_does_not_leak_base64():
    """A generated image's base64 result must not be stringified into the output message.

    Unhandled output item types fall back to str(item), which on the SDK's pydantic model
    renders every field value -- including a multi-megabyte result.
    """

    from openai.types.responses.response_output_item import ImageGenerationCall

    item = ImageGenerationCall(id="ig_1", type="image_generation_call", status="completed", result="A" * 8192)
    assert "A" * 64 in str(item)  # the leak this guards: pydantic str() renders every field value

    processed, _, _ = _openai_parse_output_response_messages([item])
    assert processed == [{"content": "[image]", "role": "assistant"}]


def test_output_computer_call_screenshot_does_not_leak_base64():
    """A computer-use screenshot keeps a remote reference but never an inline data URL."""

    class Screenshot:
        def __init__(self, image_url=None, file_id=None):
            self.type = "computer_screenshot"
            self.image_url = image_url
            self.file_id = file_id

    class ComputerCallOutput:
        def __init__(self, output):
            self.type = "computer_call_output"
            self.output = output

    inline, _, _ = _openai_parse_output_response_messages([ComputerCallOutput(Screenshot(_data_url("A" * 8192)))])
    # role=user mirrors function_call_output: a tool result supplied to the model, not its own output.
    assert inline == [{"content": "[image]", "role": "user"}]

    remote, _, _ = _openai_parse_output_response_messages(
        [ComputerCallOutput(Screenshot(image_url="https://example.com/shot.png"))]
    )
    assert remote == [{"content": "https://example.com/shot.png", "role": "user"}]


def test_normalize_prompt_variables_inline_image_degrades_to_marker():
    """A prompt variable holding an inline data URL must not put base64 on the span.

    Prompt variables are a plain string map, so there is nowhere to attach an ImagePart; the marker
    keeps the payload off the event. Remote URLs and file_ids still keep their reference.
    """

    class ResponseInputImage:
        def __init__(self, image_url=None, file_id=None):
            self.type = "input_image"
            self.image_url = image_url
            self.file_id = file_id

    result = _normalize_prompt_variables(
        {
            "inline": ResponseInputImage(image_url=_data_url("A" * 4096)),
            "remote": ResponseInputImage(image_url="https://example.com/img.png"),
            "by_id": ResponseInputImage(file_id="file-123"),
        }
    )
    assert result["inline"] == "[image]"
    assert result["remote"] == "https://example.com/img.png"
    assert result["by_id"] == "file-123"


class _ResponseInputImage:
    def __init__(self, image_url=None, file_id=None):
        self.type = "input_image"
        self.image_url = image_url
        self.file_id = file_id


@pytest.mark.parametrize(
    "response",
    [
        None,
        SimpleNamespace(instructions=None, output=[]),
        SimpleNamespace(instructions=[], output=[]),
    ],
    ids=["no_response", "instructions_none", "instructions_empty"],
)
def test_prompt_variable_image_is_normalized_off_the_chat_template_path(tracer, response):
    """Regression: normalization must not depend on the chat_template path being taken.

    It used to run only when a response echoed instructions back AND a template could be built from
    them, so a failed request or an unechoed instruction left the raw data URL on the span.
    """
    payload = "A" * 4096
    kwargs = {"prompt": {"id": "pmpt-1", "variables": {"pic": _ResponseInputImage(image_url=_data_url(payload))}}}
    with tracer.trace("openai.request", span_type=SpanTypes.LLM) as span:
        _annotate_llmobs_span_data(span, kind="llm")
        openai_set_meta_tags_from_response(span, kwargs, response)
        serialized = safe_json(get_llmobs_input_prompt(span))

    assert payload not in serialized
    assert IMAGE_FALLBACK_MARKER in serialized


def test_prompt_variable_image_is_normalized_when_caller_supplies_template(tracer):
    """A caller-supplied template skips the extraction branch entirely.

    This is ordinary usage rather than an error path, so it was the widest instance of the leak:
    the branch that normalized variables was never reached when template was already set.
    """
    payload = "B" * 4096
    kwargs = {
        "prompt": {
            "id": "pmpt-1",
            "template": "describe {{pic}}",
            "variables": {"pic": _ResponseInputImage(image_url=_data_url(payload))},
        }
    }
    response = SimpleNamespace(instructions=[SimpleNamespace(role="user", content="describe")], output=[])
    with tracer.trace("openai.request", span_type=SpanTypes.LLM) as span:
        _annotate_llmobs_span_data(span, kind="llm")
        openai_set_meta_tags_from_response(span, kwargs, response)
        serialized = safe_json(get_llmobs_input_prompt(span))

    assert payload not in serialized
    assert IMAGE_FALLBACK_MARKER in serialized


def test_prompt_multimodal_tag_survives_normalization(tracer):
    """The multimodal tag reads the SDK object's type attr, which normalizing to strings discards.

    Pins the ordering: normalize after the check, never before.
    """
    kwargs = {"prompt": {"id": "pmpt-1", "variables": {"pic": _ResponseInputImage(image_url=_data_url("C" * 32))}}}
    with tracer.trace("openai.request", span_type=SpanTypes.LLM) as span:
        _annotate_llmobs_span_data(span, kind="llm")
        openai_set_meta_tags_from_response(span, kwargs, None)
        tags = get_llmobs_tags(span) or {}

    assert tags.get(PROMPT_MULTIMODAL) == "true"


def test_extract_chat_template_with_falsy_values():
    """Test that falsy but valid values (0, False) are preserved in template extraction."""

    instructions = [
        {
            "role": "user",
            "content": [
                {"text": "Count: 0, Flag: False, Empty: "},
            ],
        }
    ]
    variables = {"count": 0, "flag": False, "empty": ""}

    result = _extract_chat_template_from_instructions(instructions, variables)

    # 0 and False should be replaced with placeholders
    # Empty string should remain as-is (not replaceable through reverse-templating)
    assert result[0]["content"] == "Count: {{count}}, Flag: {{flag}}, Empty: "


class TestOpenAIParseInputResponseMessages:
    """Tests for _openai_parse_input_response_messages with both dict and SDK object inputs."""

    def test_dict_regular_message(self):
        messages = [{"role": "user", "content": "Hello"}]
        processed, tool_call_ids = _openai_parse_input_response_messages(messages)
        assert len(processed) == 1
        assert processed[0]["role"] == "user"
        assert processed[0]["content"] == "Hello"
        assert tool_call_ids == []

    def test_dict_function_call(self):
        messages = [
            {
                "type": "function_call",
                "call_id": "call_abc",
                "name": "get_weather",
                "arguments": '{"location": "SF"}',
            }
        ]
        processed, tool_call_ids = _openai_parse_input_response_messages(messages)
        assert len(processed) == 1
        assert processed[0]["role"] == "assistant"
        tc = processed[0]["tool_calls"][0]
        assert tc["tool_id"] == "call_abc"
        assert tc["name"] == "get_weather"
        assert tc["arguments"] == {"location": "SF"}
        assert tc["type"] == "function_call"

    def test_dict_function_call_output(self):
        messages = [
            {
                "type": "function_call_output",
                "call_id": "call_abc",
                "output": '{"temp": "72F"}',
            }
        ]
        processed, tool_call_ids = _openai_parse_input_response_messages(messages)
        assert len(processed) == 1
        assert processed[0]["role"] == "user"
        tr = processed[0]["tool_results"][0]
        assert tr["tool_id"] == "call_abc"
        assert tr["result"] == '{"temp": "72F"}'
        assert tool_call_ids == ["call_abc"]

    def test_sdk_object_function_call(self):
        """SDK objects (e.g. ResponseFunctionToolCall) must be handled via _get_attr, not dict access."""

        class FakeResponseFunctionToolCall:
            type = "function_call"
            call_id = "call_sdk_123"
            name = "search"
            arguments = '{"query": "python"}'

        messages = [FakeResponseFunctionToolCall()]
        processed, tool_call_ids = _openai_parse_input_response_messages(messages)
        assert len(processed) == 1
        assert processed[0]["role"] == "assistant"
        tc = processed[0]["tool_calls"][0]
        assert tc["tool_id"] == "call_sdk_123"
        assert tc["name"] == "search"
        assert tc["arguments"] == {"query": "python"}
        assert tc["type"] == "function_call"
        assert tool_call_ids == []

    def test_sdk_object_function_call_output(self):
        """SDK objects representing function call output must be parsed correctly."""

        class FakeFunctionCallOutput:
            type = "function_call_output"
            call_id = "call_sdk_456"
            output = '{"result": "42"}'
            name = "calculate"

        messages = [FakeFunctionCallOutput()]
        processed, tool_call_ids = _openai_parse_input_response_messages(messages)
        assert len(processed) == 1
        assert processed[0]["role"] == "user"
        tr = processed[0]["tool_results"][0]
        assert tr["tool_id"] == "call_sdk_456"
        assert tr["result"] == '{"result": "42"}'
        assert tool_call_ids == ["call_sdk_456"]

    def test_mixed_dict_and_sdk_objects(self):
        """A list mixing dicts and SDK objects should all be parsed correctly."""

        class FakeResponseFunctionToolCall:
            type = "function_call"
            call_id = "call_mixed_1"
            name = "get_weather"
            arguments = '{"location": "NYC"}'

        class FakeFunctionCallOutput:
            type = "function_call_output"
            call_id = "call_mixed_1"
            output = "sunny"
            name = "get_weather"

        messages = [
            {"role": "user", "content": "What's the weather?"},
            FakeResponseFunctionToolCall(),
            FakeFunctionCallOutput(),
        ]
        processed, tool_call_ids = _openai_parse_input_response_messages(messages)
        assert len(processed) == 3
        assert processed[0]["role"] == "user"
        assert processed[0]["content"] == "What's the weather?"
        assert processed[1]["role"] == "assistant"
        assert processed[1]["tool_calls"][0]["tool_id"] == "call_mixed_1"
        assert processed[2]["role"] == "user"
        assert processed[2]["tool_results"][0]["tool_id"] == "call_mixed_1"
        assert tool_call_ids == ["call_mixed_1"]

    def test_function_call_output_list_output(self):
        """output as a list: only input_text parts are captured; images/files are skipped."""

        class TextPart:
            type = "input_text"
            text = "42 degrees"

        class ImagePart:
            type = "input_image"
            image_url = "https://example.com/img.png"

        messages = [
            {
                "type": "function_call_output",
                "call_id": "call_list",
                "output": [TextPart(), ImagePart()],
            }
        ]
        processed, tool_call_ids = _openai_parse_input_response_messages(messages)
        assert len(processed) == 1
        assert processed[0]["role"] == "user"
        tr = processed[0]["tool_results"][0]
        assert tr["tool_id"] == "call_list"
        assert tr["result"] == "42 degrees"
        assert tool_call_ids == ["call_list"]

    def test_sdk_reasoning_item_skipped(self):
        """ResponseReasoningItem (type='reasoning') should be skipped silently."""

        class FakeResponseReasoningItem:
            type = "reasoning"
            id = "reasoning_1"
            summary = []

        messages = [
            {"role": "user", "content": "Think about this"},
            FakeResponseReasoningItem(),
        ]
        processed, tool_call_ids = _openai_parse_input_response_messages(messages)
        assert len(processed) == 1
        assert processed[0]["role"] == "user"
        assert tool_call_ids == []

    def test_input_image_inline_base64_captured(self):
        """An input_image data URL is captured as an ImagePart, not concatenated into the text."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "what is this?"},
                    {"type": "input_image", "image_url": _data_url("AAECAw==")},
                ],
            }
        ]
        processed, _ = _openai_parse_input_response_messages(messages)
        assert processed == [
            {
                "content": "what is this?",
                "role": "user",
                "image_parts": [{"mime_type": "image/png", "content": "AAECAw=="}],
            }
        ]

    def test_input_image_only_message_is_still_emitted(self):
        """A message whose only content was a captured image must not be dropped."""
        messages = [{"role": "user", "content": [{"type": "input_image", "image_url": _data_url("AAA=")}]}]
        processed, _ = _openai_parse_input_response_messages(messages)
        assert processed == [
            {"content": "", "role": "user", "image_parts": [{"mime_type": "image/png", "content": "AAA="}]}
        ]

    def test_input_image_oversize_keeps_marker_and_text(self):
        """An oversize inline image degrades to a marker; the surrounding text survives."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "describe: "},
                    {"type": "input_image", "image_url": _data_url(_OVERSIZE_B64)},
                ],
            }
        ]
        processed, _ = _openai_parse_input_response_messages(messages)
        assert processed == [{"content": "describe: {}".format(IMAGE_TOO_LARGE_MARKER), "role": "user"}]

    def test_input_image_remote_url_and_file_id_references_preserved(self):
        """Capture is bytes-only: remote URLs and file_ids keep their existing reference text."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "input_image", "image_url": "https://example.com/x.png"},
                    {"type": "input_image", "file_id": "file-abc123"},
                    {"type": "input_image"},
                ],
            }
        ]
        processed, _ = _openai_parse_input_response_messages(messages)
        assert processed == [{"content": "https://example.com/x.pngfile-abc123[image]", "role": "user"}]
        assert "image_parts" not in processed[0]

    def test_input_image_chat_shaped_url_object_does_not_leak(self):
        """A chat-shaped nested image_url sent to the Responses parser must not reach the text.

        The SDK does not enforce the Responses shape at runtime, so this is what a user migrating
        chat -> responses sends. It previously stringified the dict into the message content.
        """
        payload = "A" * 4096
        messages = [
            {
                "role": "user",
                "content": [{"type": "input_image", "image_url": {"url": _data_url(payload)}}],
            }
        ]
        processed, _ = _openai_parse_input_response_messages(messages)
        assert payload not in processed[0]["content"]  # never in the message text
        assert processed[0]["image_parts"] == [{"mime_type": "image/png", "content": payload}]

    def test_input_image_leading_whitespace_data_url_does_not_leak(self):
        """Leading whitespace must not let a data URL bypass the inline check into message text."""
        payload = "B" * 4096
        messages = [
            {
                "role": "user",
                "content": [{"type": "input_image", "image_url": "\n " + _data_url(payload)}],
            }
        ]
        processed, _ = _openai_parse_input_response_messages(messages)
        assert payload not in safe_json(processed)

    def test_computer_call_output_screenshot_on_input_is_not_dropped(self):
        """A computer-use screenshot arrives on the NEXT request's input, not in response.output.

        ComputerCallOutput carries only call_id/output/type -- no role, no content -- so without
        its own branch the item matches nothing and the screenshot is lost from the span.
        """
        payload = "A" * 4096
        inline = [
            {
                "type": "computer_call_output",
                "call_id": "call_1",
                "output": {"type": "computer_screenshot", "image_url": _data_url(payload)},
            }
        ]
        processed, _ = _openai_parse_input_response_messages(inline)
        assert processed == [{"role": "user", "content": IMAGE_FALLBACK_MARKER}]

        remote = [
            {
                "type": "computer_call_output",
                "call_id": "call_2",
                "output": {"type": "computer_screenshot", "image_url": "https://example.com/shot.png"},
            }
        ]
        processed, _ = _openai_parse_input_response_messages(remote)
        assert processed == [{"role": "user", "content": "https://example.com/shot.png"}]

    def test_input_image_sdk_object_captured(self):
        """SDK objects (attribute access, detail present) are captured the same as dicts."""

        class ResponseInputImage:
            type = "input_image"
            detail = "auto"
            file_id = None
            image_url = _data_url("AAECAw==", mime_type="image/jpeg")

        messages = [{"role": "user", "content": [ResponseInputImage()]}]
        processed, _ = _openai_parse_input_response_messages(messages)
        assert processed[0]["image_parts"] == [{"mime_type": "image/jpeg", "content": "AAECAw=="}]


def _chunk(content=None, reasoning_content=None, role=None, finish_reason=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning_content, role=role)
    return SimpleNamespace(delta=delta, finish_reason=finish_reason, usage=None, index=0)


class TestOpenAIConstructMessageFromStreamedChunks:
    def test_reasoning_then_content_chunks_aggregate_both(self):
        # OpenAI-compatible reasoning providers (DeepSeek, Qwen, etc.) typically emit
        # reasoning_content chunks first, then content chunks.
        chunks = [
            _chunk(role="assistant"),
            _chunk(reasoning_content="Let me "),
            _chunk(reasoning_content="think..."),
            _chunk(content="The answer "),
            _chunk(content="is 391."),
            _chunk(finish_reason="stop"),
        ]
        message = openai_construct_message_from_streamed_chunks(chunks)
        assert message["reasoning_content"] == "Let me think..."
        assert message["content"] == "The answer is 391."
        assert message["role"] == "assistant"
        assert message["finish_reason"] == "stop"

    def test_reasoning_only_stream(self):
        chunks = [
            _chunk(role="assistant"),
            _chunk(reasoning_content="hmm"),
        ]
        message = openai_construct_message_from_streamed_chunks(chunks)
        assert message["reasoning_content"] == "hmm"
        assert message["content"] == ""

    def test_no_reasoning_key_when_absent(self):
        chunks = [_chunk(role="assistant"), _chunk(content="hello")]
        message = openai_construct_message_from_streamed_chunks(chunks)
        assert "reasoning_content" not in message
        assert message["content"] == "hello"

    def test_interleaved_reasoning_and_content_in_same_chunk(self):
        chunks = [
            _chunk(role="assistant"),
            _chunk(reasoning_content="r", content="c"),
        ]
        message = openai_construct_message_from_streamed_chunks(chunks)
        assert message["reasoning_content"] == "r"
        assert message["content"] == "c"


class TestOpenAIConstructToolCallFromStreamedChunk:
    """OpenAI-compatible backends (e.g. DashScope/Qwen) may stream a tool-call delta with
    ``function.arguments`` / ``custom.input`` set to ``None`` rather than ``""``. ``getattr``
    returns that ``None`` (the default only applies when the attribute is absent), so the
    ``str += None`` accumulation used to raise TypeError and drop the whole LLMObs span.
    """

    def test_function_call_chunk_with_none_arguments(self):
        function_call = SimpleNamespace(name="get_weather", arguments=None)
        stored = []
        openai_construct_tool_call_from_streamed_chunk(stored, function_call_chunk=function_call)
        assert stored[0]["arguments"] == ""

    def test_tool_call_chunk_with_none_function_arguments(self):
        function = SimpleNamespace(name="get_weather", arguments=None)
        tool_call = SimpleNamespace(index=0, id="call_1", type="function", function=function, custom=None)
        stored = []
        openai_construct_tool_call_from_streamed_chunk(stored, tool_call_chunk=tool_call)
        assert stored[0]["function"]["arguments"] == ""

    def test_tool_call_chunk_with_none_custom_input(self):
        custom = SimpleNamespace(name="my_tool", input=None)
        tool_call = SimpleNamespace(index=0, id="call_2", type="custom", function=None, custom=custom)
        stored = []
        openai_construct_tool_call_from_streamed_chunk(stored, tool_call_chunk=tool_call)
        assert stored[0]["custom"]["input"] == ""

    def test_none_then_value_arguments_accumulate(self):
        # DashScope emits arguments=None in the first tool-call delta, then the JSON in later deltas.
        first = SimpleNamespace(name="get_weather", arguments=None)
        later = SimpleNamespace(name=None, arguments='{"city": "NYC"}')
        stored = []
        openai_construct_tool_call_from_streamed_chunk(
            stored, tool_call_chunk=SimpleNamespace(index=0, id="call_1", type="function", function=first, custom=None)
        )
        openai_construct_tool_call_from_streamed_chunk(
            stored, tool_call_chunk=SimpleNamespace(index=0, id=None, type=None, function=later, custom=None)
        )
        assert stored[0]["function"]["arguments"] == '{"city": "NYC"}'


class TestAgentManifestPrimitives:
    """The coercion every integration's manifest needs on the way out.

    These exist because an unencodable value does not fail politely: the span encoder reprs it, and a
    bare NaN or Infinity token is not valid JSON. Spans ship batched, so one bad value discards every
    span batched with it.
    """

    def test_prune_empty_drops_what_means_not_configured(self):
        """Sections assign unconditionally so mypy can check key names; this is what drops the blanks."""
        assert prune_empty(
            {
                "framework": "PydanticAI",
                "instructions": "",
                "system_prompts": [],
                "capabilities": [],
                "metadata": {},
            }
        ) == {"framework": "PydanticAI"}

    def test_prune_empty_keeps_false_and_zero(self):
        """A configured temperature of 0 is not an absent one, which truthiness filtering loses."""
        assert prune_empty({"temperature": 0, "parallel_tool_calls": False, "top_p": 0.0}) == {
            "temperature": 0,
            "parallel_tool_calls": False,
            "top_p": 0.0,
        }

    def test_prune_empty_is_depth_first(self):
        """A container emptied by its own children has to drop too, or an empty husk ships."""
        assert prune_empty({"agent_settings": {"retries": None}, "tools": [{"name": "x", "description": ""}]}) == {
            "tools": [{"name": "x"}]
        }

    def test_is_number_rejects_bool_and_non_finite(self):
        assert is_number(0) and is_number(1.5) and is_number(-3)
        assert not is_number(True), "bool is an int subclass and would otherwise ship as true"
        assert not is_number(float("nan"))
        assert not is_number(float("inf"))
        assert not is_number(float("-inf"))
        assert not is_number("1") and not is_number(None)
        assert is_number(10**400), "a huge int is finite, and converting it to float to check would raise"

    def test_wire_value_drops_non_finite_and_unencodable(self):
        assert wire_value(float("nan")) is None
        assert wire_value(float("inf")) is None
        assert wire_value(object()) is None
        assert wire_value({"good": 1, "bad": object()}) == {"good": 1}
        assert wire_value([1, object()]) is None, "one unencodable element costs the list"

    def test_wire_value_coerces_keys_and_terminates(self):
        assert wire_value({1: "a"}) == {"1": "a"}
        cyclic = {"k": 1}
        cyclic["self"] = cyclic
        assert wire_value(cyclic) == {"k": 1}
        deep = current = {}
        for _ in range(MAX_WIRE_DEPTH + 10):
            current["n"] = {}
            current = current["n"]
        wire_value(deep)

    def test_wire_value_bounds_shared_subtrees(self):
        """Depth alone does not bound the work: shared children expand into a tree.

        Twenty dicts each referencing the same child twice is 2**20 nodes, which took seconds and
        tens of megabytes before the node budget. Cycle detection cannot catch it, because a shared
        child is a second visit rather than an ancestor.
        """
        node = {"leaf": 1}
        for _ in range(20):
            node = {"a": node, "b": node}

        wired = wire_value(node)

        assert len(safe_json(wired)) < 200_000, "the node budget is what keeps this off the wire"

    def test_wire_value_keeps_a_shared_subtree_that_fits(self):
        """A repeated child is legitimate, so the budget must not turn sharing into a drop."""
        child = {"region": "us1", "tier": "gold"}

        assert wire_value({"primary": child, "replica": child}) == {
            "primary": {"region": "us1", "tier": "gold"},
            "replica": {"region": "us1", "tier": "gold"},
        }


class TestAnthropicContentBlocks:
    """Anthropic Messages responses are a list of tagged content blocks.

    The Anthropic SDK and Bedrock InvokeModel integrations both receive this shape, since
    Bedrock passes Anthropic request/response bodies through unchanged.
    """

    TOOL_USE = {"type": "tool_use", "id": "toolu_01", "name": "get_weather", "input": {"city": "Paris"}}
    TEXT = {"type": "text", "text": "Let me check."}
    THINKING = {"type": "thinking", "thinking": "They want Paris weather."}

    def test_text_block(self):
        assert get_messages_from_anthropic_content("assistant", [self.TEXT]) == [
            {"content": "Let me check.", "role": "assistant"}
        ]

    def test_string_content(self):
        assert get_messages_from_anthropic_content("assistant", "hello") == [{"content": "hello", "role": "assistant"}]

    def test_tool_use_block_captures_the_tool_call(self):
        assert get_messages_from_anthropic_content("assistant", [self.TOOL_USE]) == [
            {
                "content": "",
                "role": "assistant",
                "tool_calls": [
                    {
                        "name": "get_weather",
                        "arguments": {"city": "Paris"},
                        "tool_id": "toolu_01",
                        "type": "tool_use",
                    }
                ],
            }
        ]

    def test_tool_use_after_text_does_not_drop_the_text(self):
        """A block list is walked in full: indexing only the first block loses later blocks."""
        messages = get_messages_from_anthropic_content("assistant", [self.TOOL_USE, self.TEXT])

        assert len(messages) == 2
        assert messages[0]["tool_calls"][0]["name"] == "get_weather"
        assert messages[1]["content"] == "Let me check."

    def test_parallel_tool_use_blocks(self):
        second = {"type": "tool_use", "id": "toolu_02", "name": "get_time", "input": {"tz": "CET"}}

        messages = get_messages_from_anthropic_content("assistant", [self.TOOL_USE, second])

        assert [m["tool_calls"][0]["name"] for m in messages] == ["get_weather", "get_time"]

    def test_thinking_block_becomes_a_reasoning_message(self):
        messages = get_messages_from_anthropic_content("assistant", [self.THINKING, self.TEXT])

        assert messages[0] == {"content": "They want Paris weather.", "role": "reasoning"}
        assert messages[1] == {"content": "Let me check.", "role": "assistant"}

    def test_tool_use_arguments_given_as_a_json_string(self):
        block = dict(self.TOOL_USE, input='{"city": "Paris"}')

        messages = get_messages_from_anthropic_content("assistant", [block])

        assert messages[0]["tool_calls"][0]["arguments"] == {"city": "Paris"}

    def test_tool_result_block(self):
        block = {"type": "tool_result", "tool_use_id": "toolu_01", "content": [{"text": "18C"}]}

        messages = get_messages_from_anthropic_content("user", [block])

        assert messages[0]["tool_results"] == [{"result": "18C", "tool_id": "toolu_01", "type": "tool_result"}]

    def test_non_iterable_content(self):
        assert get_messages_from_anthropic_content("assistant", None) == []


class TestBedrockInvokeModelOutputMessages:
    """`_extract_output_message` handles every Bedrock provider's response shape.

    Non-Anthropic providers hand it a string or list of strings; Anthropic models hand it
    the Messages API content-block list.
    """

    @pytest.fixture
    def extract(self):
        from ddtrace.llmobs._integrations.bedrock import BedrockIntegration

        return BedrockIntegration._extract_output_message

    def test_plain_string_response(self, extract):
        assert extract({"text": "hello"}) == [{"content": "hello"}]

    def test_list_of_strings_response(self, extract):
        assert extract({"text": ["a", "b"]}) == [
            {"content": "a"},
            {"content": "b"},
        ]

    def test_empty_response(self, extract):
        assert extract({"text": []}) == []

    def test_anthropic_tool_use_only_response(self, extract):
        """Claude returning only a tool call must still produce a tool call on the span."""
        response = {"text": [{"type": "tool_use", "id": "toolu_01", "name": "get_weather", "input": {"city": "Paris"}}]}

        messages = extract(response)

        assert messages == [
            {
                "content": "",
                "role": "assistant",
                "tool_calls": [
                    {
                        "name": "get_weather",
                        "arguments": {"city": "Paris"},
                        "tool_id": "toolu_01",
                        "type": "tool_use",
                    }
                ],
            }
        ]

    def test_anthropic_text_and_tool_use_response(self, extract):
        response = {
            "text": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "toolu_01", "name": "get_weather", "input": {"city": "Paris"}},
            ]
        }

        messages = extract(response)

        assert len(messages) == 2
        assert messages[0]["content"] == "Let me check."
        assert messages[1]["tool_calls"][0]["name"] == "get_weather"

    def test_anthropic_thinking_then_text_response(self, extract):
        """Extended thinking puts a non-text block first; the answer must survive it."""
        response = {
            "text": [
                {"type": "thinking", "thinking": "They want Paris weather."},
                {"type": "text", "text": "It is 18C."},
            ]
        }

        messages = extract(response)

        assert messages[0]["role"] == "reasoning"
        assert messages[1]["content"] == "It is 18C."


class TestBedrockInvokeModelInputMessages:
    """Anthropic conversation history sent back to `InvokeModel` contains tool blocks.

    Agent loops replay the assistant's `tool_use` and the user's `tool_result`, so those
    blocks have to survive input extraction or the trace shows a model answering from nowhere.
    """

    @pytest.fixture
    def extract(self):
        from ddtrace.llmobs._integrations.bedrock import BedrockIntegration

        return BedrockIntegration._extract_input_message

    def test_plain_string_prompt(self, extract):
        assert extract("hello") == [{"content": "hello"}]

    def test_text_blocks(self, extract):
        prompt = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]

        assert extract(prompt) == [{"content": "hi", "role": "user"}]

    def test_tool_use_block_is_captured(self, extract):
        prompt = [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_01", "name": "get_weather", "input": {"city": "Paris"}}],
            }
        ]

        messages = extract(prompt)

        assert messages == [
            {
                "content": "",
                "role": "assistant",
                "tool_calls": [
                    {
                        "name": "get_weather",
                        "arguments": {"city": "Paris"},
                        "tool_id": "toolu_01",
                        "type": "tool_use",
                    }
                ],
            }
        ]

    def test_tool_result_block_is_captured(self, extract):
        prompt = [
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_01", "content": [{"text": "18C"}]}],
            }
        ]

        messages = extract(prompt)

        assert messages[0]["tool_results"] == [{"result": "18C", "tool_id": "toolu_01", "type": "tool_result"}]

    def test_full_agent_loop_history(self, extract):
        """The whole replayed conversation must survive, not just its text turns."""
        prompt = [
            {"role": "user", "content": [{"type": "text", "text": "weather in Paris?"}]},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_01", "name": "get_weather", "input": {"city": "Paris"}}],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_01", "content": [{"text": "18C"}]}],
            },
        ]

        messages = extract(prompt)

        assert len(messages) == 3
        assert messages[0]["content"] == "weather in Paris?"
        assert messages[1]["tool_calls"][0]["name"] == "get_weather"
        assert messages[2]["tool_results"][0]["result"] == "18C"

    def test_thinking_block_becomes_reasoning(self, extract):
        prompt = [{"role": "assistant", "content": [{"type": "thinking", "thinking": "considering"}]}]

        assert extract(prompt) == [{"content": "considering", "role": "reasoning"}]


class TestAnthropicToolDefinitions:
    """Bedrock `InvokeModel` sends Anthropic-format tool definitions, not the Converse shape."""

    def test_empty(self):
        assert get_tool_definitions_from_anthropic_tools([]) == []
        assert get_tool_definitions_from_anthropic_tools(None) == []

    def test_tool_definition(self):
        tools = [
            {
                "name": "get_weather",
                "description": "Get the weather",
                "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
            }
        ]

        assert get_tool_definitions_from_anthropic_tools(tools) == [
            {
                "name": "get_weather",
                "description": "Get the weather",
                "schema": {"type": "object", "properties": {"city": {"type": "string"}}},
            }
        ]

    def test_deferred_tool_omits_description_and_schema(self):
        tools = [
            {
                "name": "deferred",
                "description": "should not be captured",
                "input_schema": {"type": "object"},
                "defer_loading": True,
            }
        ]

        assert get_tool_definitions_from_anthropic_tools(tools) == [
            {"name": "deferred", "description": "", "schema": {}}
        ]
