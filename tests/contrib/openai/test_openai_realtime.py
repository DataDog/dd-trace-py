"""Tests for OpenAI Realtime API LLMObs instrumentation.

The Realtime API is a bidirectional WebSocket event stream, so there are no VCR cassettes. Instead
we drive the ``_RealtimeState`` machine directly with scripted events (unit tests), and drive a real
patched ``RealtimeConnection`` backed by a fake websocket (integration test).
"""

import base64
import json
from types import SimpleNamespace

import pytest

from ddtrace.contrib.internal.openai import _realtime
from ddtrace.contrib.internal.openai._realtime import _RealtimeState
from ddtrace.llmobs._integrations.utils import g711_to_pcm16
from ddtrace.llmobs._integrations.utils import pcm16_to_wav


try:
    from openai.resources.realtime.realtime import AsyncRealtimeConnection
    from openai.resources.realtime.realtime import RealtimeConnection
except ImportError:
    try:
        from openai.resources.beta.realtime.realtime import AsyncRealtimeConnection
        from openai.resources.beta.realtime.realtime import RealtimeConnection
    except ImportError:
        AsyncRealtimeConnection = None
        RealtimeConnection = None


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def _b64(raw):
    return base64.b64encode(raw).decode("utf-8")


def _wav_b64(raw, sample_rate=24000):
    """Expected base64 of raw PCM16 bytes wrapped in a WAV container."""
    return base64.b64encode(pcm16_to_wav(raw, sample_rate)).decode("utf-8")


class _FakeSpan:
    def __init__(self, resource):
        self.resource = resource
        self.error = 0
        self.finished = False

    def finish(self):
        self.finished = True


class _RecordingIntegration:
    """Stand-in for OpenAIIntegration that records the realtime tagging calls."""

    def __init__(self):
        self.responses = []

    def trace(self, operation_id, **kwargs):
        return _FakeSpan(operation_id)

    def _llmobs_set_tags_from_realtime_response(
        self, span, model_name, input_messages, output_messages, metadata, metrics, session_id=None
    ):
        self.responses.append(
            {
                "span": span,
                "model_name": model_name,
                "input_messages": input_messages,
                "output_messages": output_messages,
                "metadata": metadata,
                "metrics": metrics,
                "session_id": session_id,
            }
        )


def _new_state(model=None):
    integration = _RecordingIntegration()
    state = _RealtimeState(integration, client=None, model=model)
    return integration, state


def _session_created(input_mime="audio/pcm", output_mime="audio/pcm", transcription=True):
    return _ns(
        type="session.created",
        session=_ns(
            model="gpt-realtime",
            instructions="be brief",
            output_modalities=["audio"],
            audio=_ns(
                input=_ns(
                    format=_ns(type=input_mime),
                    transcription=_ns(model="whisper-1") if transcription else None,
                ),
                output=_ns(format=_ns(type=output_mime), voice="alloy"),
            ),
        ),
    )


def _drive_turn(state, input_mime="audio/pcm", output_mime="audio/pcm"):
    state.on_server_event(_session_created(input_mime, output_mime))
    state.on_client_event({"type": "input_audio_buffer.append", "audio": _b64(b"\x01\x02")})
    state.on_client_event({"type": "input_audio_buffer.commit"})
    state.on_server_event(_ns(type="input_audio_buffer.committed", item_id="item_1"))
    state.on_server_event(
        _ns(
            type="conversation.item.input_audio_transcription.completed",
            item_id="item_1",
            transcript="what time is it?",
        )
    )
    state.on_server_event(_ns(type="response.created", response=_ns(id="resp_1")))
    state.on_server_event(_ns(type="response.output_audio.delta", response_id="resp_1", delta=_b64(b"\x03\x04")))
    state.on_server_event(_ns(type="response.output_audio_transcript.delta", response_id="resp_1", delta="It's "))
    state.on_server_event(
        _ns(type="response.output_audio_transcript.done", response_id="resp_1", transcript="It's noon.")
    )
    state.on_server_event(
        _ns(
            type="response.done",
            response=_ns(
                id="resp_1",
                model="gpt-realtime-2025",
                status="completed",
                usage=_ns(input_tokens=10, output_tokens=20, total_tokens=30),
            ),
        )
    )


def test_realtime_state_pcm_turn_wraps_audio_as_wav():
    """A raw-PCM turn surfaces the transcript as content and the audio as a WAV-wrapped audio_part."""
    integration, state = _new_state()
    _drive_turn(state)

    assert len(integration.responses) == 1
    resp = integration.responses[0]
    assert resp["model_name"] == "gpt-realtime-2025"
    assert resp["input_messages"] == [
        {
            "role": "user",
            "content": "what time is it?",
            "audio_parts": [{"mime_type": "audio/wav", "content": _wav_b64(b"\x01\x02")}],
        }
    ]
    assert resp["output_messages"] == [
        {
            "role": "assistant",
            "content": "It's noon.",
            "audio_parts": [{"mime_type": "audio/wav", "content": _wav_b64(b"\x03\x04")}],
        }
    ]
    assert resp["metrics"] == {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}
    # response span finished on response.done
    assert resp["span"].finished is True


def test_realtime_state_turn_carries_session_metadata_and_id():
    """Each turn span carries the session config as metadata and a stable session_id (no session span)."""
    integration, state = _new_state()
    _drive_turn(state)

    resp = integration.responses[0]
    assert resp["metadata"]["voice"] == "alloy"
    assert resp["metadata"]["input_audio_format"] == "audio/pcm"
    assert resp["metadata"]["output_audio_format"] == "audio/pcm"
    assert resp["metadata"]["instructions"] == "be brief"
    assert resp["metadata"]["output_modalities"] == ["audio"]
    # session_id groups turns into one conversation; it's stable for the connection.
    assert resp["session_id"] == state._session_id
    assert resp["session_id"]


def test_realtime_state_session_id_stable_across_turns():
    """All turns on one connection share the same session_id so the UI groups them."""
    integration, state = _new_state()
    _drive_turn(state)
    _drive_turn(state)
    assert len(integration.responses) == 2
    assert integration.responses[0]["session_id"] == integration.responses[1]["session_id"]


def test_realtime_state_renderable_audio_emits_audio_parts():
    """When the configured format is renderable (e.g. wav), audio_parts are emitted on both sides."""
    integration, state = _new_state()
    _drive_turn(state, input_mime="audio/wav", output_mime="audio/wav")

    resp = integration.responses[0]
    input_msg = resp["input_messages"][0]
    output_msg = resp["output_messages"][0]
    assert input_msg["audio_parts"] == [{"mime_type": "audio/wav", "content": _b64(b"\x01\x02")}]
    assert output_msg["audio_parts"] == [{"mime_type": "audio/wav", "content": _b64(b"\x03\x04")}]
    # transcripts are still surfaced as content alongside the audio
    assert input_msg["content"] == "what time is it?"
    assert output_msg["content"] == "It's noon."


def test_realtime_state_failed_response_marks_error():
    """A failed response status flags the child span as an error."""
    integration, state = _new_state(model="gpt-realtime")
    state.on_server_event(_ns(type="response.created", response=_ns(id="resp_err")))
    state.on_server_event(_ns(type="response.done", response=_ns(id="resp_err", status="failed")))

    assert integration.responses[0]["span"].error == 1


def test_realtime_state_close_is_idempotent():
    """Closing twice doesn't re-finalize turns or raise."""
    integration, state = _new_state()
    _drive_turn(state)  # one fully-finalized turn
    state.finish_session()
    state.finish_session()
    assert len(integration.responses) == 1


def test_realtime_state_pcm_audio_only_wraps_as_wav_without_transcript():
    """Raw PCM audio with no transcript is still captured as a WAV audio_part (no marker needed)."""
    integration, state = _new_state(model="gpt-realtime")
    state.on_server_event(_session_created())
    state.on_client_event({"type": "input_audio_buffer.append", "audio": _b64(b"\x01\x02")})
    state.on_server_event(_ns(type="response.created", response=_ns(id="r")))
    state.on_server_event(_ns(type="response.output_audio.delta", response_id="r", delta=_b64(b"\x03")))
    state.on_server_event(_ns(type="response.done", response=_ns(id="r", status="completed")))

    resp = integration.responses[0]
    assert resp["input_messages"] == [
        {"role": "user", "content": "", "audio_parts": [{"mime_type": "audio/wav", "content": _wav_b64(b"\x01\x02")}]}
    ]
    assert resp["output_messages"] == [
        {"role": "assistant", "content": "", "audio_parts": [{"mime_type": "audio/wav", "content": _wav_b64(b"\x03")}]}
    ]


def test_realtime_state_g711_input_wrapped_as_wav():
    """G.711 telephony audio (audio/pcmu) is decoded to PCM16 and WAV-wrapped at 8kHz."""
    integration, state = _new_state()
    state.on_server_event(_session_created(input_mime="audio/pcmu", output_mime="audio/pcmu", transcription=False))
    raw = b"\xff\xff\x7f\x7f"
    state.on_client_event({"type": "input_audio_buffer.append", "audio": _b64(raw)})
    state.on_server_event(_ns(type="response.created", response=_ns(id="r")))
    state.on_server_event(_ns(type="response.output_audio_transcript.done", response_id="r", transcript="hello"))
    state.on_server_event(_ns(type="response.done", response=_ns(id="r", status="completed")))

    expected = base64.b64encode(pcm16_to_wav(g711_to_pcm16(raw, "ulaw"), 8000)).decode("utf-8")
    assert integration.responses[0]["input_messages"][0]["audio_parts"] == [
        {"mime_type": "audio/wav", "content": expected}
    ]


def test_realtime_state_function_call_captured():
    """A function call (and the result the app feeds back) is captured as tool_calls/tool_results."""
    integration, state = _new_state()
    state.on_server_event(_session_created(transcription=False))
    # Turn 1: the model calls a function (no speech) -> response.done carries a function_call item.
    state.on_server_event(_ns(type="response.created", response=_ns(id="r1")))
    state.on_server_event(
        _ns(
            type="response.done",
            response=_ns(
                id="r1",
                status="completed",
                output=[_ns(type="function_call", name="get_weather", arguments='{"city": "Paris"}', call_id="call_1")],
            ),
        )
    )
    out1 = integration.responses[0]["output_messages"]
    assert out1 == [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"name": "get_weather", "arguments": {"city": "Paris"}, "tool_id": "call_1", "type": "function"}
            ],
        }
    ]

    # App returns the tool result, then the model speaks the answer.
    state.on_client_event(
        {
            "type": "conversation.item.create",
            "item": {"type": "function_call_output", "call_id": "call_1", "output": "sunny"},
        }
    )
    state.on_server_event(_ns(type="response.created", response=_ns(id="r2")))
    state.on_server_event(_ns(type="response.output_audio_transcript.done", response_id="r2", transcript="It's sunny."))
    state.on_server_event(_ns(type="response.done", response=_ns(id="r2", status="completed")))
    resp2 = integration.responses[1]
    # The result is labeled with the originating call's function name (carried over by call_id), so
    # the UI shows "Tool result: get_weather" rather than "unknown".
    assert resp2["input_messages"] == [
        {
            "role": "user",
            "content": "",
            "tool_results": [
                {"tool_id": "call_1", "result": "sunny", "type": "function_call_output", "name": "get_weather"}
            ],
        }
    ]
    assert resp2["output_messages"] == [{"role": "assistant", "content": "It's sunny."}]


def test_realtime_state_mcp_call_captured():
    """An MCP call is captured as a tool_call plus its inline server-side result as a tool_result."""
    integration, state = _new_state()
    state.on_server_event(_session_created(transcription=False))
    state.on_server_event(_ns(type="response.created", response=_ns(id="r1")))
    state.on_server_event(
        _ns(
            type="response.done",
            response=_ns(
                id="r1",
                status="completed",
                output=[
                    _ns(
                        type="mcp_call",
                        id="mcp_1",
                        name="search",
                        arguments='{"q": "x"}',
                        output="result text",
                        error=None,
                    )
                ],
            ),
        )
    )
    out = integration.responses[0]["output_messages"][0]
    assert out["tool_calls"] == [{"name": "search", "arguments": {"q": "x"}, "tool_id": "mcp_1", "type": "mcp_call"}]
    assert out["tool_results"] == [
        {"name": "search", "result": "result text", "tool_id": "mcp_1", "type": "mcp_tool_result"}
    ]


def test_realtime_state_unwrappable_audio_fallback_marker():
    """Audio in a format we can't wrap (not PCM16/G.711) with no transcript surfaces an [audio] marker."""
    integration, state = _new_state(model="gpt-realtime")
    state.on_server_event(_session_created(input_mime="audio/basic", output_mime="audio/basic", transcription=False))
    state.on_client_event({"type": "input_audio_buffer.append", "audio": _b64(b"\x01\x02")})
    state.on_server_event(_ns(type="response.created", response=_ns(id="r")))
    state.on_server_event(_ns(type="response.output_audio.delta", response_id="r", delta=_b64(b"\x03")))
    state.on_server_event(_ns(type="response.done", response=_ns(id="r", status="completed")))

    resp = integration.responses[0]
    assert resp["input_messages"] == [{"role": "user", "content": "[audio]"}]
    assert resp["output_messages"] == [{"role": "assistant", "content": "[audio]"}]


def test_realtime_state_close_tags_in_flight_response():
    """Closing mid-turn (before response.done) tags and finishes the in-flight response span."""
    integration, state = _new_state(model="gpt-realtime")
    state.on_server_event(_session_created())
    state.on_server_event(
        _ns(
            type="conversation.item.input_audio_transcription.completed",
            item_id="item_1",
            transcript="partial question",
        )
    )
    # snapshot the pending input into the response turn, then stream a partial transcript
    state.on_server_event(_ns(type="response.created", response=_ns(id="resp_1")))
    state.on_server_event(_ns(type="response.output_audio_transcript.delta", response_id="resp_1", delta="partial "))
    # no response.done — connection closes mid-turn
    state.finish_session()

    assert len(integration.responses) == 1, "in-flight response span should still be tagged"
    resp = integration.responses[0]
    assert resp["output_messages"] == [{"role": "assistant", "content": "partial "}]
    assert resp["span"].finished is True


def test_realtime_state_defers_finalization_until_transcript():
    """If input transcription lands after response.done, the span is held then finalized with it."""
    integration, state = _new_state()
    state.on_server_event(_session_created())
    state.on_client_event({"type": "input_audio_buffer.append", "audio": _b64(b"\x01\x02")})
    state.on_server_event(_ns(type="input_audio_buffer.committed", item_id="item_1"))
    state.on_server_event(_ns(type="response.created", response=_ns(id="r1")))
    state.on_server_event(_ns(type="response.output_audio_transcript.done", response_id="r1", transcript="noon"))
    state.on_server_event(
        _ns(
            type="response.done",
            response=_ns(id="r1", status="completed", usage=_ns(input_tokens=1, output_tokens=2, total_tokens=3)),
        )
    )
    # transcription hasn't arrived yet -> the turn is held, not finalized
    assert integration.responses == []

    # the late transcription arrives -> the held turn is finalized with it
    state.on_server_event(
        _ns(
            type="conversation.item.input_audio_transcription.completed",
            item_id="item_1",
            transcript="what time is it?",
        )
    )
    assert len(integration.responses) == 1
    resp = integration.responses[0]
    assert resp["input_messages"][0]["content"] == "what time is it?"
    assert resp["span"].finished is True


def test_realtime_state_awaiting_flushed_on_next_turn():
    """A turn still awaiting a transcription is flushed when the next turn starts (no hang)."""
    integration, state = _new_state()
    state.on_server_event(_session_created())
    state.on_client_event({"type": "input_audio_buffer.append", "audio": _b64(b"\x01\x02")})
    state.on_server_event(_ns(type="input_audio_buffer.committed", item_id="item_1"))
    state.on_server_event(_ns(type="response.created", response=_ns(id="r1")))
    state.on_server_event(_ns(type="response.done", response=_ns(id="r1", status="completed")))
    assert integration.responses == []  # held, awaiting transcription

    # next turn begins -> the prior turn is finalized (without a transcript; audio kept)
    state.on_server_event(_ns(type="response.created", response=_ns(id="r2")))
    assert len(integration.responses) == 1
    assert integration.responses[0]["input_messages"][0]["audio_parts"][0]["mime_type"] == "audio/wav"


def test_realtime_state_awaiting_flushed_on_close():
    """A turn awaiting a transcription is flushed on session close."""
    integration, state = _new_state()
    state.on_server_event(_session_created())
    state.on_client_event({"type": "input_audio_buffer.append", "audio": _b64(b"\x01\x02")})
    state.on_server_event(_ns(type="input_audio_buffer.committed", item_id="item_1"))
    state.on_server_event(_ns(type="response.created", response=_ns(id="r1")))
    state.on_server_event(_ns(type="response.done", response=_ns(id="r1", status="completed")))
    assert integration.responses == []

    state.finish_session()
    assert len(integration.responses) == 1


def test_realtime_state_input_buffer_clear_discards_audio():
    """An input_audio_buffer.clear drops buffered audio so it isn't attributed to the next turn."""
    integration, state = _new_state(model="gpt-realtime")
    state.on_server_event(_session_created())
    state.on_client_event({"type": "input_audio_buffer.append", "audio": _b64(b"\x01\x02")})
    state.on_client_event({"type": "input_audio_buffer.clear"})
    state.on_server_event(_ns(type="response.created", response=_ns(id="r")))
    state.on_server_event(_ns(type="response.output_audio_transcript.done", response_id="r", transcript="hi"))
    state.on_server_event(_ns(type="response.done", response=_ns(id="r", status="completed")))

    # no leftover input audio -> no input message (cleared) ; output transcript captured
    assert integration.responses[0]["input_messages"] == []
    assert integration.responses[0]["output_messages"] == [{"role": "assistant", "content": "hi"}]


def test_realtime_state_absorb_input_item_skips_non_user_role():
    """conversation.item.create for a non-user item is not captured as user input."""
    integration, state = _new_state(model="gpt-realtime")
    state.on_server_event(_session_created())
    state.on_client_event(
        {"type": "conversation.item.create", "item": {"role": "assistant", "content": [{"type": "text", "text": "x"}]}}
    )
    state.on_client_event(
        {
            "type": "conversation.item.create",
            "item": {"role": "user", "content": [{"type": "input_text", "text": "hello"}]},
        }
    )
    state.on_server_event(_ns(type="response.created", response=_ns(id="r")))
    state.on_server_event(_ns(type="response.done", response=_ns(id="r", status="completed")))

    assert integration.responses[0]["input_messages"] == [{"role": "user", "content": "hello"}]


def test_realtime_state_no_defer_when_transcription_disabled():
    """With input transcription off, a turn finalizes at response.done (not held for a transcript)."""
    integration, state = _new_state()
    state.on_server_event(_session_created(transcription=False))
    state.on_client_event({"type": "input_audio_buffer.append", "audio": _b64(b"\x01\x02")})
    state.on_server_event(_ns(type="input_audio_buffer.committed", item_id="item_1"))
    state.on_server_event(_ns(type="response.created", response=_ns(id="r1")))
    state.on_server_event(_ns(type="response.output_audio_transcript.done", response_id="r1", transcript="hi"))
    state.on_server_event(_ns(type="response.done", response=_ns(id="r1", status="completed")))

    # Finalized immediately — not parked in _awaiting waiting for a transcript that never comes.
    assert len(integration.responses) == 1
    assert state._awaiting == []


def test_realtime_state_transcription_failed_finalizes_awaiting():
    """A transcription.failed event finalizes the turn waiting on that item (no indefinite hang)."""
    integration, state = _new_state()
    state.on_server_event(_session_created())  # transcription enabled
    state.on_client_event({"type": "input_audio_buffer.append", "audio": _b64(b"\x01\x02")})
    state.on_server_event(_ns(type="input_audio_buffer.committed", item_id="item_1"))
    state.on_server_event(_ns(type="response.created", response=_ns(id="r1")))
    state.on_server_event(_ns(type="response.done", response=_ns(id="r1", status="completed")))
    assert integration.responses == []  # awaiting the transcript

    state.on_server_event(_ns(type="conversation.item.input_audio_transcription.failed", item_id="item_1"))
    assert len(integration.responses) == 1
    assert state._awaiting == []


def test_realtime_state_input_transcripts_no_leak():
    """The input-transcript cache doesn't accumulate across normal turns."""
    integration, state = _new_state()
    _drive_turn(state)
    _drive_turn(state)
    assert len(integration.responses) == 2
    assert state._input_transcripts == {}


def test_realtime_state_usage_total_tokens_fallback():
    """When usage omits total_tokens, it's derived from input + output."""
    integration, state = _new_state()
    state.on_server_event(_session_created(transcription=False))
    state.on_server_event(_ns(type="response.created", response=_ns(id="r1")))
    state.on_server_event(
        _ns(
            type="response.done",
            response=_ns(id="r1", status="completed", usage=_ns(input_tokens=4, output_tokens=6, total_tokens=None)),
        )
    )
    assert integration.responses[0]["metrics"] == {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10}


# ---- integration test: real patched RealtimeConnection over a fake websocket ----


class _FakeWebSocket:
    """Minimal sync websocket double: yields scripted server messages, records sends."""

    def __init__(self, server_messages):
        self._messages = list(server_messages)
        self.sent = []

    def recv(self, decode=False):
        # The real SDK calls recv(decode=False) and (in openai>=1.66) asserts it returns bytes.
        msg = self._messages.pop(0)
        return msg.encode("utf-8") if isinstance(msg, str) else msg

    def send(self, data):
        self.sent.append(data)

    def close(self, code=1000, reason=""):
        self.closed = True


def _server_messages():
    return [
        json.dumps(
            {
                "type": "session.created",
                "event_id": "e0",
                "session": {
                    "type": "realtime",
                    "model": "gpt-realtime",
                    "instructions": "be brief",
                    "output_modalities": ["audio"],
                    "audio": {
                        "input": {"format": {"type": "audio/pcm"}},
                        "output": {"format": {"type": "audio/pcm"}, "voice": "alloy"},
                    },
                },
            }
        ),
        json.dumps({"type": "input_audio_buffer.committed", "event_id": "e1", "item_id": "item_1"}),
        json.dumps(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "event_id": "e2",
                "item_id": "item_1",
                "content_index": 0,
                "transcript": "what time is it?",
            }
        ),
        json.dumps({"type": "response.created", "event_id": "e3", "response": {"id": "resp_1"}}),
        json.dumps(
            {
                "type": "response.output_audio_transcript.done",
                "event_id": "e4",
                "response_id": "resp_1",
                "item_id": "item_2",
                "output_index": 0,
                "content_index": 0,
                "transcript": "It's noon.",
            }
        ),
        json.dumps(
            {
                "type": "response.done",
                "event_id": "e5",
                "response": {
                    "id": "resp_1",
                    "model": "gpt-realtime-2025",
                    "status": "completed",
                    "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
                },
            }
        ),
    ]


@pytest.mark.skipif(RealtimeConnection is None, reason="openai realtime API not available")
def test_realtime_integration_spans(openai, openai_llmobs, test_spans):
    """A full realtime turn over the patched connection produces one per-turn llm span."""
    messages = _server_messages()
    fake_ws = _FakeWebSocket(messages)
    conn = RealtimeConnection(fake_ws)

    client = openai.OpenAI()
    _realtime._attach_session(SimpleNamespace(_dd_client=client, _dd_model="gpt-realtime"), conn)

    # client event flows through the patched send (records onto the fake socket)
    conn.input_audio_buffer.append(audio=_b64(b"\x01\x02"))
    assert fake_ws.sent, "expected the client event to reach the websocket"

    for _ in range(len(messages)):
        conn.recv()
    conn.close()

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    # No session span anymore — just the per-turn llm span.
    assert [s.resource for s in spans] == ["createRealtimeResponse"]

    from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
    from tests.llmobs._utils import assert_llmobs_span_data

    data = _get_llmobs_data_metastruct(spans[0])
    assert_llmobs_span_data(
        data,
        span_kind="llm",
        name="OpenAI.createRealtimeResponse",
        model_name="gpt-realtime-2025",
        model_provider="openai",
        input_messages=[
            {
                "role": "user",
                "content": "what time is it?",
                "audio_parts": [{"mime_type": "audio/wav", "content": _wav_b64(b"\x01\x02")}],
            }
        ],
        output_messages=[{"role": "assistant", "content": "It's noon."}],
        metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        # session config rides on each turn span as metadata now.
        metadata={"voice": "alloy", "output_audio_format": "audio/pcm", "input_audio_format": "audio/pcm"},
    )

    # The turn is grouped into a conversation via session_id.
    assert data.get("session_id")


class _FakeConnectionClosedOK(Exception):
    """Name contains 'ConnectionClosed' so the close-detection wrapper matches it."""


class _ClosingFakeWebSocket(_FakeWebSocket):
    """Like _FakeWebSocket, but recv raises a connection-closed error once messages run out."""

    def recv(self, decode=False):
        if not self._messages:
            raise _FakeConnectionClosedOK()
        return super().recv(decode=decode)


def _awaiting_turn_messages():
    # transcription enabled + a committed audio turn whose transcript never arrives -> the turn is
    # parked in _awaiting and must be finalized when the connection closes.
    return [
        json.dumps(
            {
                "type": "session.created",
                "event_id": "e0",
                "session": {
                    "type": "realtime",
                    "model": "gpt-realtime",
                    "audio": {"input": {"format": {"type": "audio/pcm"}, "transcription": {"model": "whisper-1"}}},
                },
            }
        ),
        json.dumps({"type": "input_audio_buffer.committed", "event_id": "e1", "item_id": "item_1"}),
        json.dumps({"type": "response.created", "event_id": "e2", "response": {"id": "resp_1"}}),
        json.dumps({"type": "response.done", "event_id": "e3", "response": {"id": "resp_1", "status": "completed"}}),
    ]


@pytest.mark.skipif(RealtimeConnection is None, reason="openai realtime API not available")
def test_realtime_recv_close_finalizes_without_explicit_close(openai, openai_llmobs, test_spans):
    """If the connection closes mid-iteration (no with/close()), the awaiting span is still finalized."""
    msgs = _awaiting_turn_messages()
    conn = RealtimeConnection(_ClosingFakeWebSocket(msgs))
    client = openai.OpenAI()
    _realtime._attach_session(SimpleNamespace(_dd_client=client, _dd_model="gpt-realtime"), conn)

    # Drive events then let recv raise on close — never call conn.close().
    with pytest.raises(_FakeConnectionClosedOK):
        while True:
            conn.recv()

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert [s.resource for s in spans] == ["createRealtimeResponse"]


class _FakeAsyncWebSocket:
    """Minimal async websocket double."""

    def __init__(self, server_messages):
        self._messages = list(server_messages)
        self.sent = []

    async def recv(self, decode=False):
        msg = self._messages.pop(0)
        return msg.encode("utf-8") if isinstance(msg, str) else msg

    async def send(self, data):
        self.sent.append(data)

    async def close(self, code=1000, reason=""):
        self.closed = True


@pytest.mark.skipif(AsyncRealtimeConnection is None, reason="openai realtime API not available")
@pytest.mark.asyncio
async def test_realtime_async_integration_spans(openai, openai_llmobs, test_spans):
    """The async connection path (async send/recv/close) produces a per-turn llm span."""
    from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
    from tests.llmobs._utils import assert_llmobs_span_data

    msgs = _server_messages()
    conn = AsyncRealtimeConnection(_FakeAsyncWebSocket(msgs))
    client = openai.AsyncOpenAI()
    _realtime._attach_session(SimpleNamespace(_dd_client=client, _dd_model="gpt-realtime"), conn)

    await conn.input_audio_buffer.append(audio=_b64(b"\x01\x02"))
    for _ in range(len(msgs)):
        await conn.recv()
    await conn.close()

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert [s.resource for s in spans] == ["createRealtimeResponse"]
    data = _get_llmobs_data_metastruct(spans[0])
    assert_llmobs_span_data(
        data,
        span_kind="llm",
        name="OpenAI.createRealtimeResponse",
        model_name="gpt-realtime-2025",
        output_messages=[{"role": "assistant", "content": "It's noon."}],
        metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    )
    assert data.get("session_id")
