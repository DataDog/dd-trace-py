"""Tests for OpenAI Realtime API LLMObs instrumentation.

The Realtime API is a bidirectional WebSocket event stream, so there are no VCR cassettes. Instead
we drive the `_RealtimeState` machine directly with scripted events (unit tests), and drive a real
patched `RealtimeConnection` backed by a fake websocket (integration test).
"""

import base64
import gc
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

    def finish(self, finish_time=None):
        self.finished = True
        # Real ddtrace Span.finish takes an optional epoch-seconds end; the state machine back-dates
        # phase/turn spans. Record it (ns) when given so tests can assert durations/ordering.
        self.finish_ns = int(finish_time * 1e9) if finish_time is not None else None


class _RecordingIntegration:
    """Stand-in for OpenAIIntegration that records the realtime tagging calls."""

    def __init__(self):
        self.responses = []
        # Workflow spans (the turn root + user-speech/agent-speech windows) recorded by name.
        self.workflows = []

    def trace(self, operation_id, **kwargs):
        return _FakeSpan(operation_id)

    def _llmobs_set_tags_from_realtime_response(
        self,
        span,
        model_name,
        input_messages,
        output_messages,
        metadata,
        metrics,
        session_id=None,
        parent_span=None,
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
                "parent_span": parent_span,
            }
        )

    def _llmobs_set_tags_from_realtime_workflow(
        self, span, name, session_id=None, parent_span=None, input_value=None, output_value=None, metadata=None
    ):
        self.workflows.append(
            {
                "span": span,
                "name": name,
                "session_id": session_id,
                "parent_span": parent_span,
                "input_value": input_value,
                "output_value": output_value,
                "metadata": metadata,
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


def test_realtime_state_audio_turn_emits_phase_spans():
    """An audio turn emits the phase span tree - a workflow turn root with user-speech, llm, and
    agent-speech children - with the turn's timing carried on the span boundaries (start/finish).
    """
    integration, state = _new_state()
    _drive_turn(state)

    wf = {w["name"]: w for w in integration.workflows}
    assert {"realtime audio turn", "user speech", "agent speech"} <= set(wf)
    root, user, agent = wf["realtime audio turn"], wf["user speech"], wf["agent speech"]
    llm = integration.responses[0]

    # Nesting: the root is parentless; user-speech, llm, and agent-speech all hang off the root span.
    assert root["parent_span"] is None
    assert user["parent_span"] is root["span"]
    assert agent["parent_span"] is root["span"]
    assert llm["parent_span"] is root["span"]

    # Timing lives on the span boundaries now (no separate metadata): each span has start <= finish,
    # the user speaks before the agent, and generation (llm) starts no earlier than the user began.
    for span in (root["span"], user["span"], agent["span"], llm["span"]):
        assert span.start_ns is not None and span.finish_ns is not None
        assert span.start_ns <= span.finish_ns
    assert user["span"].start_ns <= agent["span"].start_ns
    assert llm["span"].start_ns >= user["span"].start_ns


def test_realtime_state_text_only_turn_emits_no_speech_spans():
    """A turn with no audio (text in, text out) emits the turn root and llm span but no user-speech
    or agent-speech workflow spans (nothing was spoken to bracket).
    """
    integration, state = _new_state()
    state.on_server_event(_session_created(transcription=False))
    state.on_client_event(
        {
            "type": "conversation.item.create",
            "item": _ns(type="message", role="user", content=[_ns(type="input_text", text="hi")]),
        }
    )
    state.on_server_event(_ns(type="response.created", response=_ns(id="r")))
    state.on_server_event(_ns(type="response.output_text.delta", response_id="r", delta="hello"))
    state.on_server_event(_ns(type="response.output_text.done", response_id="r", text="hello"))
    state.on_server_event(_ns(type="response.done", response=_ns(id="r", status="completed")))

    names = [w["name"] for w in integration.workflows]
    assert "realtime audio turn" in names
    assert "user speech" not in names
    assert "agent speech" not in names


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
    """A failed response status flags both the llm span and the turn root as errors.

    The root is the trace root, so flagging only the llm child surfaces a successful turn containing a
    failed generation, and error filters and error-rate metrics keyed on the root miss the failure.
    """
    integration, state = _new_state(model="gpt-realtime")
    state.on_server_event(_ns(type="response.created", response=_ns(id="resp_err")))
    state.on_server_event(_ns(type="response.done", response=_ns(id="resp_err", status="failed")))

    assert integration.responses[0]["span"].error == 1
    root = {w["name"]: w for w in integration.workflows}["realtime audio turn"]
    assert root["span"].error == 1, "the turn root must report the failure too"


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


# ---- speech-window timing (server VAD, continuously streaming client) ----

# One mic block: 10 ms of PCM16 mono @ 24 kHz (48 bytes/ms).
_BLOCK_MS = 10
_BLOCK_BYTES = 480


class _Clock:
    """Fake `time.time_ns` that only moves when the test says so, so wall-clock assertions on the
    span boundaries are exact.
    """

    def __init__(self, now=1_700_000_000_000_000_000):
        self.now = now

    def __call__(self):
        return self.now

    def advance_ms(self, ms):
        self.now += int(ms * 1_000_000)


class _Mic:
    """A client that streams the microphone continuously, the way a server-VAD app does: audio is
    appended in real time whether or not anyone is speaking, so the input buffer is open from the
    moment the previous turn was committed.
    """

    def __init__(self, state, clock):
        self.state = state
        self.clock = clock
        self.blocks = []  # every raw block appended this session, in order
        self.buffer_ms = 0  # offset on the session's input-audio-buffer timeline
        self.onset_index = 0  # index into `blocks` of the first block after the last speech onset

    def stream(self, ms):
        """Append `ms` of mic audio, advancing the clock in lockstep (real-time streaming)."""
        for _ in range(ms // _BLOCK_MS):
            block = bytes([len(self.blocks) % 256, 0]) * (_BLOCK_BYTES // 2)
            self.blocks.append(block)
            self.state.on_client_event({"type": "input_audio_buffer.append", "audio": _b64(block)})
            self.buffer_ms += _BLOCK_MS
            self.clock.advance_ms(_BLOCK_MS)

    def speech_started(self):
        """VAD detects speech starting at the current buffer head (everything streamed so far was
        pre-speech listening).
        """
        self.onset_index = len(self.blocks)
        self.state.on_server_event(_ns(type="input_audio_buffer.speech_started", audio_start_ms=self.buffer_ms))

    def speech_blocks(self):
        """The blocks appended since the last onset - what should survive the pre-speech trim."""
        return b"".join(self.blocks[self.onset_index :])


def _agent_pcm(ms):
    """`ms` of agent audio as PCM16 mono @ 24 kHz (48 bytes per ms)."""
    return b"\x02\x00" * (24 * ms)


def _truncate(state, response_id, audio_end_ms):
    """The client reporting how far the listener actually got into that response's audio before
    cutting it off - what a barge-in-capable client sends when the user interrupts.
    """
    state.on_client_event(
        {
            "type": "conversation.item.truncate",
            "item_id": "audio_" + response_id,
            "content_index": 0,
            "audio_end_ms": audio_end_ms,
        }
    )


def _respond(state, mic, response_id, agent_audio_ms, playback_ms=None, transcript="ok"):
    """Commit the pending input and drive a full response: 100 ms of model latency, then the agent's
    audio streamed down fast and played out over `agent_audio_ms`. The mic keeps streaming
    throughout, as it would for a continuous client. `playback_ms` cuts the wall clock short of the
    full playback (an interruption).
    """
    item_id = "item_" + response_id
    state.on_server_event(_ns(type="input_audio_buffer.speech_stopped", audio_end_ms=mic.buffer_ms))
    state.on_server_event(_ns(type="input_audio_buffer.committed", item_id=item_id))
    state.on_server_event(
        _ns(type="conversation.item.input_audio_transcription.completed", item_id=item_id, transcript="question")
    )
    state.on_server_event(_ns(type="response.created", response=_ns(id=response_id)))
    mic.stream(100)  # model latency; the buffer has reopened, so this audio is the next turn's
    # The model streams the whole clip down faster than it plays - one delta here.
    state.on_server_event(
        _ns(
            type="response.output_audio.delta",
            response_id=response_id,
            item_id="audio_" + response_id,
            delta=_b64(_agent_pcm(agent_audio_ms)),
        )
    )
    state.on_server_event(
        _ns(type="response.output_audio_transcript.done", response_id=response_id, transcript=transcript)
    )
    state.on_server_event(_ns(type="response.done", response=_ns(id=response_id, status="completed")))
    mic.stream(agent_audio_ms if playback_ms is None else playback_ms)  # the agent's audio plays out


def _windows(integration):
    """The (start, end) wall-clock window of every workflow span emitted, by name, in order."""
    return [(w["name"], w["span"].start_ns, w["span"].finish_ns) for w in integration.workflows]


def _assert_ns(actual, expected, tolerance_ns=1_000):
    """Compare two ns timestamps loosely.

    Span starts are set as integer ns, but ends go through `Span.finish(finish_time=<epoch
    seconds>)` - and a float64 can only resolve ~256 ns at unix-epoch magnitudes. That rounding is
    immaterial next to audio timings measured in milliseconds, so don't assert on it.
    """
    assert abs(actual - expected) <= tolerance_ns, "{} != {} (within {} ns)".format(actual, expected, tolerance_ns)


def test_realtime_state_user_speech_anchored_on_vad_onset(monkeypatch):
    """The user-speech window starts at the VAD speech onset, not at the first buffered chunk.

    A continuously-streaming client opens the buffer the instant the previous turn was committed, so
    anchoring on the first append would report the whole listening window as speech. The pre-speech
    lead-in is trimmed off the captured audio too, so the clip covers the window the span reports.
    """
    clock = _Clock()
    monkeypatch.setattr(_realtime.time, "time_ns", clock)
    integration, state = _new_state()
    state.on_server_event(_session_created())
    mic = _Mic(state, clock)

    listening_start = clock.now
    mic.stream(200)  # mic open, nobody speaking
    onset = clock.now - _BLOCK_MS * 1_000_000  # onset offset points at the last block we appended
    mic.speech_started()
    mic.stream(300)  # the user speaks
    speech_end = clock.now
    spoken = mic.speech_blocks()
    _respond(state, mic, "r1", agent_audio_ms=500)

    user = {w["name"]: w for w in integration.workflows}["user speech"]
    assert user["span"].start_ns == onset, "user speech must start at the VAD onset"
    assert user["span"].start_ns > listening_start, "not at the first append (the listening window)"
    _assert_ns(user["span"].finish_ns, speech_end)  # the commit
    # Only the audio from the onset on is kept, so the clip matches the reported window.
    assert integration.responses[0]["input_messages"][0]["audio_parts"] == [
        {"mime_type": "audio/wav", "content": _wav_b64(spoken)}
    ]


def test_realtime_state_consecutive_turns_do_not_overlap(monkeypatch):
    """Two sequential spoken turns (no barge-in) produce non-overlapping windows on one timeline.

    Regression test: the user-speech window used to open at the previous turn's commit, so turn N+1's
    user speech spanned the whole of turn N's agent response.
    """
    clock = _Clock()
    monkeypatch.setattr(_realtime.time, "time_ns", clock)
    integration, state = _new_state()
    state.on_server_event(_session_created())
    mic = _Mic(state, clock)

    mic.stream(200)
    mic.speech_started()
    mic.stream(300)
    _respond(state, mic, "r1", agent_audio_ms=500)
    mic.stream(200)  # a beat after the agent finishes, then the user speaks again
    mic.speech_started()
    mic.stream(300)
    _respond(state, mic, "r2", agent_audio_ms=400)

    windows = _windows(integration)
    assert [name for name, _, _ in windows] == [
        "realtime audio turn",
        "user speech",
        "agent speech",
        "realtime audio turn",
        "user speech",
        "agent speech",
    ]
    speech = sorted([w for w in windows if w[0] != "realtime audio turn"], key=lambda w: w[1])
    assert [name for name, _, _ in speech] == ["user speech", "agent speech", "user speech", "agent speech"]
    for (_, _, prev_end), (name, start, _) in zip(speech, speech[1:]):
        assert start >= prev_end, "{} starts before the previous window ended".format(name)
    # The turn roots (whole perceived turns) don't overlap either.
    turns = [w for w in windows if w[0] == "realtime audio turn"]
    assert turns[1][1] >= turns[0][2]


def test_realtime_state_barge_in_windows_still_overlap(monkeypatch):
    """When the user genuinely talks over the agent, the windows overlap - the fix reflects real
    speech timing rather than forcing turns apart.

    This is a client that never truncates: it plays out every byte it received, so the listener did
    hear the agent over their own speech and the reported overlap is the true one.
    """
    clock = _Clock()
    monkeypatch.setattr(_realtime.time, "time_ns", clock)
    integration, state = _new_state()
    state.on_server_event(_session_created())
    mic = _Mic(state, clock)

    mic.stream(200)
    mic.speech_started()
    mic.stream(300)
    # The user cuts in 300 ms into the agent's 500 ms answer.
    _respond(state, mic, "r1", agent_audio_ms=500, playback_ms=300)
    mic.speech_started()
    mic.stream(300)
    _respond(state, mic, "r2", agent_audio_ms=400)

    windows = _windows(integration)
    agent1 = [w for w in windows if w[0] == "agent speech"][0]
    user2 = [w for w in windows if w[0] == "user speech"][1]
    assert user2[1] < agent1[2], "a barge-in must still show the user speaking over the agent"


def test_realtime_state_truncation_caps_agent_speech_to_what_was_heard(monkeypatch):
    """A barge-in truncation caps the agent segment - window and stored audio - at what was played.

    The truncation lands after `response.done` (the model streams faster than it plays), so it can
    only apply to a turn we are still holding. Holding is enabled by having seen this client truncate
    before, which is why the first interruption of a connection is still reported untruncated.
    """
    clock = _Clock()
    monkeypatch.setattr(_realtime.time, "time_ns", clock)
    integration, state = _new_state()
    state.on_server_event(_session_created())
    mic = _Mic(state, clock)

    # Turn 1: the user cuts the agent off 200 ms into a 500 ms answer, and the client says so - but
    # the turn was already submitted at response.done, so this one can't be capped.
    mic.stream(200)
    mic.speech_started()
    mic.stream(300)
    _respond(state, mic, "r1", agent_audio_ms=500, playback_ms=200)
    assert len(integration.responses) == 1, "an unheld turn is submitted at response.done"
    mic.speech_started()
    _truncate(state, "r1", audio_end_ms=200)
    agent1 = [w for w in _windows(integration) if w[0] == "agent speech"][0]
    _assert_ns(agent1[2] - agent1[1], 500 * 1_000_000)  # too late to cap the first interruption

    # Turn 2: now that the client is known to truncate, the finished turn is held while its audio
    # plays, so the same interruption lands in time.
    mic.stream(300)
    _respond(state, mic, "r2", agent_audio_ms=400, playback_ms=150)
    assert len(integration.responses) == 1, "turn 2 is held while its audio is still playing"
    agent_start = clock.now - 150 * 1_000_000
    mic.speech_started()
    _truncate(state, "r2", audio_end_ms=150)

    assert len(integration.responses) == 2, "the truncation ends the wait and submits the turn"
    agent2 = [w for w in _windows(integration) if w[0] == "agent speech"][1]
    assert agent2[1] == agent_start, "the window still starts at the first audio (TTFA is unchanged)"
    _assert_ns(agent2[2], agent_start + 150 * 1_000_000)  # ...and now ends where playback stopped
    # The stored audio stops where playback did, so the clip is what the listener heard.
    assert integration.responses[1]["output_messages"][0]["audio_parts"] == [
        {"mime_type": "audio/wav", "content": _wav_b64(_agent_pcm(150))}
    ]


def test_realtime_state_held_turn_submitted_when_playback_ends(monkeypatch):
    """A held turn that is never interrupted is still submitted once its audio finishes playing,
    without waiting for the next turn or for close - and with its audio intact.
    """
    clock = _Clock()
    monkeypatch.setattr(_realtime.time, "time_ns", clock)
    integration, state = _new_state()
    state.on_server_event(_session_created())
    mic = _Mic(state, clock)
    # A truncation for a turn we no longer hold - what the first barge-in of a connection looks like.
    # It marks the client as one that cuts playback short.
    _truncate(state, "gone", audio_end_ms=0)

    mic.stream(200)
    mic.speech_started()
    mic.stream(300)
    _respond(state, mic, "r1", agent_audio_ms=500, playback_ms=0)
    assert integration.responses == [], "held until the agent's audio has played out"
    assert len(state._playing) == 1

    mic.stream(600)  # the audio plays out; the mic keeps streaming, and one of those events flushes
    assert len(integration.responses) == 1
    assert state._playing == []
    agent = [w for w in _windows(integration) if w[0] == "agent speech"][0]
    _assert_ns(agent[2] - agent[1], 500 * 1_000_000)
    assert integration.responses[0]["output_messages"][0]["audio_parts"] == [
        {"mime_type": "audio/wav", "content": _wav_b64(_agent_pcm(500))}
    ]


def test_realtime_state_no_hold_for_clients_that_never_truncate(monkeypatch):
    """A client that never truncates hears every byte we captured, so its turns are submitted at
    response.done as before - it pays none of the holding cost.
    """
    clock = _Clock()
    monkeypatch.setattr(_realtime.time, "time_ns", clock)
    integration, state = _new_state()
    state.on_server_event(_session_created())
    mic = _Mic(state, clock)

    mic.stream(200)
    mic.speech_started()
    mic.stream(300)
    _respond(state, mic, "r1", agent_audio_ms=500, playback_ms=0)

    assert len(integration.responses) == 1, "submitted at response.done, not held"
    assert state._playing == []


def test_realtime_state_park_deadline_is_capped(monkeypatch):
    """Holding a turn is bounded by _PARK_MAX_NS rather than by the full playback length.

    Flushing is event-driven, so a connection that goes idle right after a long response would leave
    the held turn unsubmitted for as long as its audio would have played. A listener who interrupts
    does it early, so capping the wait costs almost no real truncations.
    """
    clock = _Clock()
    monkeypatch.setattr(_realtime.time, "time_ns", clock)
    monkeypatch.setattr(_realtime, "_PARK_MAX_NS", 2 * 1_000_000_000)  # 2 s, vs a 30 s response
    integration, state = _new_state()
    state.on_server_event(_session_created())
    mic = _Mic(state, clock)
    state._client_truncates = True  # a barge-in was seen earlier on this connection

    mic.stream(100)
    mic.speech_started()
    mic.stream(200)
    # 30 s of agent audio, but the clock only advances 100 ms of it, so playback is still "in flight".
    _respond(state, mic, "r1", agent_audio_ms=30_000, playback_ms=100)

    assert len(state._playing) == 1, "the turn should be held"
    held = state._playing[0]
    parked_at = held.response_done_ns
    assert held.playback_end_ns - parked_at <= 2 * 1_000_000_000, "the wait must be capped"
    assert held.playback_end_ns < held.audio.start_ns + 30 * 1_000_000_000, "not the full playback"

    # Once the capped deadline passes, the next event submits it - no 30 s wait.
    clock.advance_ms(2_100)
    mic.stream(10)
    assert state._playing == []
    assert len(integration.responses) == 1


def test_realtime_state_user_speech_falls_back_to_first_append_without_vad(monkeypatch):
    """With client-side turn detection there are no VAD events and the client only appends while the
    user talks, so the first buffered chunk is the onset and nothing is trimmed.
    """
    clock = _Clock()
    monkeypatch.setattr(_realtime.time, "time_ns", clock)
    integration, state = _new_state()
    state.on_server_event(_session_created())
    mic = _Mic(state, clock)

    speech_start = clock.now
    mic.stream(300)
    spoken = b"".join(mic.blocks)
    _respond(state, mic, "r1", agent_audio_ms=200)

    user = {w["name"]: w for w in integration.workflows}["user speech"]
    assert user["span"].start_ns == speech_start
    assert integration.responses[0]["input_messages"][0]["audio_parts"] == [
        {"mime_type": "audio/wav", "content": _wav_b64(spoken)}
    ]


def _vad_onset(state, mic, prefix_padding_ms):
    """Fire the VAD onset with the offset pointing `prefix_padding_ms` *behind* the buffer head.

    Real server VAD reports `audio_start_ms` including the session's `prefix_padding_ms`, so some
    speech is already buffered when the event lands. `_Mic.speech_started` instead points at the
    head exactly, which makes the pre-onset run cover everything buffered and sends `trim_leading`
    down its reset-outright branch - so it cannot exercise the partial trim that the padding causes in
    practice.
    """
    mic.onset_index = len(mic.blocks) - prefix_padding_ms // _BLOCK_MS
    state.on_server_event(
        _ns(type="input_audio_buffer.speech_started", audio_start_ms=mic.buffer_ms - prefix_padding_ms)
    )


def test_realtime_state_long_lead_in_does_not_discard_the_users_speech(monkeypatch):
    """A pre-speech lead-in that spends the byte cap must not cost the turn its actual speech.

    A continuously-streaming client holds the buffer open across the whole previous agent response, so
    on a long one the lead-in alone can exceed the accumulation cap - and `append` frees the buffered
    chunks when it trips. Trimming that lead-in at the onset has to reopen the accumulator, or every
    chunk of the speech that follows is rejected by a cap the discarded audio filled and the turn
    surfaces nothing but an `[audio]` marker.
    """
    clock = _Clock()
    monkeypatch.setattr(_realtime.time, "time_ns", clock)
    # 300 ms of lead-in is 14400 bytes at 48 B/ms, so a 12000-byte cap trips before the onset while
    # still leaving room for the 200 ms of speech (9600 bytes) that follows.
    monkeypatch.setattr(_realtime, "_AUDIO_ACCUM_MAX_BYTES", 12_000)
    integration, state = _new_state()
    state.on_server_event(_session_created())
    mic = _Mic(state, clock)

    mic.stream(300)  # mic open through the previous agent response; nobody speaking
    assert state._pending_input.audio.oversize, "test needs the cap to trip during the lead-in"
    _vad_onset(state, mic, prefix_padding_ms=50)
    assert not state._pending_input.audio.oversize, "the trim must reopen the accumulator"
    speech_index = len(mic.blocks)
    mic.stream(200)  # the user speaks
    # The 50 ms of padding ahead of the onset is gone for good - `append` dropped it when the cap
    # tripped - but everything from the onset on is captured, which is what the window reports.
    # Snapshot before `_respond`, which keeps streaming into the *next* turn's buffer.
    spoken = b"".join(mic.blocks[speech_index:])
    _respond(state, mic, "r1", agent_audio_ms=100)

    assert integration.responses[0]["input_messages"][0]["audio_parts"] == [
        {"mime_type": "audio/wav", "content": _wav_b64(spoken)}
    ], "the user's speech must survive a lead-in that spent the cap"


@pytest.mark.parametrize("raw_len", [0, 1, 2, 3, 4, 159, 160, 161, 1920, 3199])
def test_realtime_decoded_b64_len_is_exact(raw_len):
    """The decoded-length estimate has to be exact, padding included.

    It sizes the speech windows, the per-item audio offsets and the barge-in cut point. PCM16 chunks
    are even-length, so most of them encode with padding; counting that padding as data drifts the
    timeline and lands the cut on an odd byte, splitting a sample.
    """
    raw = b"\x01\x02" * (raw_len // 2) + b"\x03" * (raw_len % 2)
    assert _realtime._decoded_b64_len(_b64(raw)) == raw_len


def test_realtime_state_trim_leading_keeps_the_byte_cap_consistent():
    """After a partial trim the running cap must equal what is still buffered.

    The trim drops whole chunks, so a cap tracked by subtraction drifts from the surviving chunks
    whenever `append` has already freed them.
    """
    accumulator = _realtime._AudioAccumulator()
    for _ in range(10):
        accumulator.append(_b64(b"\x01\x02" * 240))  # 480 bytes each
    accumulator.trim_leading(480 * 3)

    assert len(accumulator.chunks) == 7
    assert accumulator._bytes == sum(_realtime._decoded_b64_len(c) for c in accumulator.chunks)
    assert not accumulator.oversize


def test_realtime_state_input_buffer_clock_survives_a_late_session_format(monkeypatch):
    """Audio appended before the session's audio format is known must not cost the whole session.

    Client sends are observed as they happen, but `session.created` is only seen when the app parses
    it - a client streaming from its own thread routinely wins that race. Writing the buffer origin off
    at that point disabled the VAD-offset projection, and with it the lead-in trimming, for every turn
    that followed rather than just the first.
    """
    clock = _Clock()
    monkeypatch.setattr(_realtime.time, "time_ns", clock)
    integration, state = _new_state()
    mic = _Mic(state, clock)

    mic.stream(100)  # streaming before session.created: the format, and so the byte rate, is unknown
    assert state._input_buffer_ms_at_ns is None, "the clock cannot be live yet"
    state.on_server_event(_session_created())
    mic.stream(100)  # a rate is known now, so the backlog converts and the clock goes live
    assert state._input_buffer_ms_at_ns is not None, "the origin must survive the race"
    _assert_ns(state._input_buffer_ms, 200, tolerance_ns=1)  # ms: both streams are on the timeline

    # First turn: consume the pre-format audio so the next turn starts with a live clock throughout.
    _respond(state, mic, "r1", agent_audio_ms=100)

    # Second turn: the lead-in is trimmed, which the lost origin used to prevent for the whole session.
    mic.stream(300)  # mic open through r1's response; nobody speaking
    _vad_onset(state, mic, prefix_padding_ms=50)
    mic.stream(200)  # the user speaks
    # The onset offset lands on a chunk boundary here, so the 50 ms of padding survives the
    # whole-chunk trim alongside the speech. Snapshot before `_respond` streams into the next turn.
    kept = mic.speech_blocks()
    _respond(state, mic, "r2", agent_audio_ms=100)

    assert integration.responses[1]["input_messages"][0]["audio_parts"] == [
        {"mime_type": "audio/wav", "content": _wav_b64(kept)}
    ], "the pre-speech lead-in must still be trimmed after a late session format"


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


def _by_operation(spans):
    """Spans keyed by operation (resource); a turn emits at most one span per phase."""
    by_operation = {s.resource: s for s in spans}
    assert len(by_operation) == len(spans), "unexpected duplicate phase spans: {}".format([s.resource for s in spans])
    return by_operation


@pytest.mark.skipif(RealtimeConnection is None, reason="openai realtime API not available")
def test_realtime_integration_spans(openai, openai_llmobs, test_spans):
    """A full realtime turn over the patched connection produces the turn's phase span tree."""
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

    spans = _by_operation([s for trace in test_spans.pop_traces() for s in trace])
    # The turn root, the llm (generation) span, and the user-speech window. No agent-speech span:
    # this turn's response carried a transcript but no output audio.
    assert sorted(spans) == ["createRealtimeResponse", "createRealtimeTurn", "createRealtimeUserSpeech"]
    root = spans["createRealtimeTurn"]
    llm = spans["createRealtimeResponse"]
    user = spans["createRealtimeUserSpeech"]

    from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
    from tests.llmobs._utils import assert_llmobs_span_data

    data = _get_llmobs_data_metastruct(llm)
    assert_llmobs_span_data(
        data,
        span_kind="llm",
        name="OpenAI.createRealtimeResponse",
        parent_id=str(root.span_id),
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
    # The phase spans are workflow-kind timing regions under the root; the audio rides on the llm span.
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(root),
        span_kind="workflow",
        name="realtime audio turn",
        input_value="what time is it?",
        output_value="It's noon.",
    )
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(user),
        span_kind="workflow",
        name="user speech",
        parent_id=str(root.span_id),
        output_value="what time is it?",
    )
    # The root opens with the user speech and closes no earlier than its children.
    assert root.start_ns == user.start_ns
    assert llm.start_ns >= user.start_ns
    assert root.start_ns + root.duration_ns >= llm.start_ns + llm.duration_ns

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
    # Nothing was spoken on either side (input committed with no appended audio, no output audio), so
    # the turn is just the root and the llm span - no phase windows to bracket.
    assert sorted(s.resource for s in spans) == ["createRealtimeResponse", "createRealtimeTurn"]


@pytest.mark.skipif(RealtimeConnection is None, reason="openai realtime API not available")
def test_realtime_dropped_connection_finalizes_held_turn(openai, openai_llmobs, test_spans):
    """A turn held for playback is still submitted when the caller drops the connection.

    `with` blocks and a socket that closes are already covered (the SDK's `__exit__` closes, and
    `recv` raises), so this is the un-managed case: without the connection's finalizer nothing
    would ever flush the hold and the whole turn would be lost.
    """
    conn = RealtimeConnection(_FakeWebSocket([]))
    client = openai.OpenAI()
    _realtime._attach_session(SimpleNamespace(_dd_client=client, _dd_model="gpt-realtime"), conn)
    state = conn._dd_realtime_state
    state.on_server_event(_session_created(transcription=False))
    # A truncation for a turn we no longer hold marks this as a client that cuts playback short, so
    # the next turn is held while its audio plays.
    _truncate(state, "gone", audio_end_ms=0)
    state.on_server_event(_ns(type="response.created", response=_ns(id="r1")))
    state.on_server_event(
        _ns(type="response.output_audio.delta", response_id="r1", item_id="audio_r1", delta=_b64(_agent_pcm(5000)))
    )
    state.on_server_event(_ns(type="response.output_audio_transcript.done", response_id="r1", transcript="hi"))
    state.on_server_event(_ns(type="response.done", response=_ns(id="r1", status="completed")))
    assert len(state._playing) == 1, "held while the agent's 5s of audio plays out"
    assert not [s for trace in test_spans.pop_traces() for s in trace], "nothing submitted while held"

    # Drop the connection the way an un-managed caller would: no close(), no closed socket.
    del conn, state
    gc.collect()

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert sorted(s.resource for s in spans) == [
        "createRealtimeAgentSpeech",
        "createRealtimeResponse",
        "createRealtimeTurn",
    ]


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
    """The async connection path (async send/recv/close) produces the same phase span tree."""
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

    spans = _by_operation([s for trace in test_spans.pop_traces() for s in trace])
    assert sorted(spans) == ["createRealtimeResponse", "createRealtimeTurn", "createRealtimeUserSpeech"]
    data = _get_llmobs_data_metastruct(spans["createRealtimeResponse"])
    assert_llmobs_span_data(
        data,
        span_kind="llm",
        name="OpenAI.createRealtimeResponse",
        parent_id=str(spans["createRealtimeTurn"].span_id),
        model_name="gpt-realtime-2025",
        output_messages=[{"role": "assistant", "content": "It's noon."}],
        metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    )
    assert data.get("session_id")


def _turn_messages(response_id, item_id, out_audio_b64, out_transcript):
    """Server events for one full turn: committed input, transcription, response with output audio."""
    return [
        json.dumps({"type": "input_audio_buffer.committed", "event_id": "c" + response_id, "item_id": item_id}),
        json.dumps(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "event_id": "t" + response_id,
                "item_id": item_id,
                "transcript": "hi " + response_id,
            }
        ),
        json.dumps({"type": "response.created", "event_id": "rc" + response_id, "response": {"id": response_id}}),
        json.dumps(
            {
                "type": "response.output_audio.delta",
                "event_id": "ad" + response_id,
                "response_id": response_id,
                "delta": out_audio_b64,
            }
        ),
        json.dumps(
            {
                "type": "response.output_audio_transcript.done",
                "event_id": "td" + response_id,
                "response_id": response_id,
                "transcript": out_transcript,
            }
        ),
        json.dumps(
            {
                "type": "response.done",
                "event_id": "rd" + response_id,
                "response": {"id": response_id, "status": "completed"},
            }
        ),
    ]


@pytest.mark.skipif(RealtimeConnection is None, reason="openai realtime API not available")
def test_realtime_integration_multi_turn_with_output_audio(openai, openai_llmobs, test_spans):
    """Two turns over one connection produce two phase trees sharing a session_id, each with output
    audio, and each agent-speech window sized from that audio rather than from wall clock.
    """
    session = json.dumps(
        {
            "type": "session.created",
            "event_id": "e0",
            "session": {
                "type": "realtime",
                "model": "gpt-realtime",
                "audio": {
                    "input": {"format": {"type": "audio/pcm"}},
                    "output": {"format": {"type": "audio/pcm"}, "voice": "alloy"},
                },
            },
        }
    )
    messages = [session]
    messages += _turn_messages("r1", "item_1", _b64(b"\x01\x02"), "first answer")
    messages += _turn_messages("r2", "item_2", _b64(b"\x03\x04"), "second answer")

    conn = RealtimeConnection(_FakeWebSocket(messages))
    client = openai.OpenAI()
    _realtime._attach_session(SimpleNamespace(_dd_client=client, _dd_model="gpt-realtime"), conn)
    for _ in range(len(messages)):
        conn.recv()
    conn.close()

    from ddtrace.llmobs._utils import _get_llmobs_data_metastruct

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    # Two turns, each a root + llm + agent-speech window (no client audio was appended, so neither
    # turn has a user-speech window).
    assert sorted(s.resource for s in spans) == [
        "createRealtimeAgentSpeech",
        "createRealtimeAgentSpeech",
        "createRealtimeResponse",
        "createRealtimeResponse",
        "createRealtimeTurn",
        "createRealtimeTurn",
    ]
    turns = sorted((s for s in spans if s.resource == "createRealtimeTurn"), key=lambda s: s.start_ns)
    llm_spans = sorted((s for s in spans if s.resource == "createRealtimeResponse"), key=lambda s: s.start_ns)
    agent_spans = sorted((s for s in spans if s.resource == "createRealtimeAgentSpeech"), key=lambda s: s.start_ns)
    span_data = [_get_llmobs_data_metastruct(s) for s in llm_spans]
    # Both turns grouped into one conversation.
    assert span_data[0]["session_id"] == span_data[1]["session_id"]
    # Each turn's output carries a playable WAV audio_part.
    for data, raw in zip(span_data, (b"\x01\x02", b"\x03\x04")):
        out = data["meta"]["output"]["messages"][0]
        assert out["audio_parts"] == [{"mime_type": "audio/wav", "content": _wav_b64(raw)}]
    for turn, llm, agent in zip(turns, llm_spans, agent_spans):
        assert _get_llmobs_data_metastruct(llm)["parent_id"] == str(turn.span_id)
        assert _get_llmobs_data_metastruct(agent)["parent_id"] == str(turn.span_id)
        # The window is the playback duration of the 2 bytes of PCM16 above (~42 us), not the wall
        # clock the events happened to take.
        assert 0 < agent.duration_ns < 1_000_000


@pytest.mark.skipif(RealtimeConnection is None, reason="openai realtime API not available")
def test_realtime_integration_tool_call(openai, openai_llmobs, test_spans):
    """A function_call in response.done is captured as a tool_call on the turn span (real integration)."""
    messages = [
        json.dumps(
            {"type": "session.created", "event_id": "e0", "session": {"type": "realtime", "model": "gpt-realtime"}}
        ),
        json.dumps({"type": "response.created", "event_id": "rc", "response": {"id": "r1"}}),
        json.dumps(
            {
                "type": "response.done",
                "event_id": "rd",
                "response": {
                    "id": "r1",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "name": "get_weather",
                            "call_id": "call_1",
                            "arguments": '{"city": "Paris"}',
                        }
                    ],
                },
            }
        ),
    ]
    conn = RealtimeConnection(_FakeWebSocket(messages))
    client = openai.OpenAI()
    _realtime._attach_session(SimpleNamespace(_dd_client=client, _dd_model="gpt-realtime"), conn)
    for _ in range(len(messages)):
        conn.recv()
    conn.close()

    from ddtrace.llmobs._utils import _get_llmobs_data_metastruct

    spans = _by_operation([s for trace in test_spans.pop_traces() for s in trace])
    # A tool-call-only response speaks nothing, so there is no agent-speech window.
    assert sorted(spans) == ["createRealtimeResponse", "createRealtimeTurn"]
    out = _get_llmobs_data_metastruct(spans["createRealtimeResponse"])["meta"]["output"]["messages"][0]
    assert out["tool_calls"] == [
        {"name": "get_weather", "arguments": {"city": "Paris"}, "tool_id": "call_1", "type": "function"}
    ]


@pytest.mark.skipif(RealtimeConnection is None, reason="openai realtime API not available")
def test_realtime_patch_and_unpatch(openai):
    """patch() wraps the realtime connection methods; unpatch() restores them."""
    from ddtrace.contrib.internal.openai.patch import patch
    from ddtrace.contrib.internal.openai.patch import unpatch

    methods = ("parse_event", "send", "recv", "close")
    # The `openai` fixture already called patch().
    for m in methods:
        assert hasattr(getattr(RealtimeConnection, m), "__wrapped__"), "%s should be wrapped" % m
        assert hasattr(getattr(AsyncRealtimeConnection, m), "__wrapped__"), "async %s should be wrapped" % m

    unpatch()
    for m in methods:
        assert not hasattr(getattr(RealtimeConnection, m), "__wrapped__"), "%s should be unwrapped" % m
        assert not hasattr(getattr(AsyncRealtimeConnection, m), "__wrapped__"), "async %s should be unwrapped" % m

    patch()  # restore for the fixture's teardown
    assert hasattr(RealtimeConnection.parse_event, "__wrapped__")
