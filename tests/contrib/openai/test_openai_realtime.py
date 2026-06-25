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
from ddtrace.llmobs._integrations.utils import pcm16_to_wav


try:
    from openai.resources.realtime.realtime import RealtimeConnection
except ImportError:
    try:
        from openai.resources.beta.realtime.realtime import RealtimeConnection
    except ImportError:
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
        self.sessions = []

    def trace(self, operation_id, **kwargs):
        return _FakeSpan(operation_id)

    def _llmobs_set_tags_from_realtime_response(
        self, span, model_name, input_messages, output_messages, metadata, metrics
    ):
        self.responses.append(
            {
                "span": span,
                "model_name": model_name,
                "input_messages": input_messages,
                "output_messages": output_messages,
                "metadata": metadata,
                "metrics": metrics,
            }
        )

    def _llmobs_set_tags_from_realtime_session(self, span, model_name, metadata):
        self.sessions.append({"span": span, "model_name": model_name, "metadata": metadata})


def _new_state(model=None):
    integration = _RecordingIntegration()
    state = _RealtimeState(integration, _FakeSpan("createRealtimeSession"), client=None, model=model)
    return integration, state


def _session_created(input_mime="audio/pcm", output_mime="audio/pcm"):
    return _ns(
        type="session.created",
        session=_ns(
            model="gpt-realtime",
            instructions="be brief",
            output_modalities=["audio"],
            audio=_ns(
                input=_ns(format=_ns(type=input_mime)),
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


def test_realtime_state_session_metadata_on_close():
    """The session span is tagged with the session config and finished on close."""
    integration, state = _new_state()
    _drive_turn(state)
    state.finish_session()

    assert integration.sessions, "expected the session span to be tagged"
    session = integration.sessions[-1]
    assert session["model_name"] == "gpt-realtime"
    assert session["metadata"]["voice"] == "alloy"
    assert session["metadata"]["input_audio_format"] == "audio/pcm"
    assert session["metadata"]["output_audio_format"] == "audio/pcm"
    assert session["metadata"]["instructions"] == "be brief"
    assert session["metadata"]["output_modalities"] == ["audio"]
    assert state._session_span.finished is True


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
    """Closing twice only finishes/tags the session once."""
    integration, state = _new_state(model="gpt-realtime")
    state.finish_session()
    state.finish_session()
    assert len(integration.sessions) == 1


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


def test_realtime_state_unwrappable_audio_fallback_marker():
    """Audio in a non-wrappable format (e.g. G.711) with no transcript surfaces an [audio] marker."""
    integration, state = _new_state(model="gpt-realtime")
    state.on_server_event(_session_created(input_mime="audio/pcmu", output_mime="audio/pcmu"))
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
    assert state._session_span.finished is True


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


# ---- integration test: real patched RealtimeConnection over a fake websocket ----


class _FakeWebSocket:
    """Minimal sync websocket double: yields scripted server messages, records sends."""

    def __init__(self, server_messages):
        self._messages = list(server_messages)
        self.sent = []

    def recv(self, decode=False):
        return self._messages.pop(0)

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
    """A full realtime turn over the patched connection produces a session + response span."""
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
    by_resource = {s.resource: s for s in spans}
    assert "createRealtimeSession" in by_resource
    assert "createRealtimeResponse" in by_resource

    from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
    from tests.llmobs._utils import assert_llmobs_span_data

    response_span = by_resource["createRealtimeResponse"]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(response_span),
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
    )

    # The session span is a workflow span, so model fields are projected away; assert config metadata.
    session_span = by_resource["createRealtimeSession"]
    session_data = _get_llmobs_data_metastruct(session_span)
    assert_llmobs_span_data(
        session_data,
        span_kind="workflow",
        name="OpenAI.createRealtimeSession",
        metadata={"voice": "alloy", "output_audio_format": "audio/pcm", "input_audio_format": "audio/pcm"},
    )
