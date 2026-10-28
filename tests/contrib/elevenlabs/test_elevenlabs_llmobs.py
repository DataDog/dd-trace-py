import asyncio
import base64
import gzip
import io
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import wave

from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai import conversation
import pytest

from ddtrace import config
from ddtrace.contrib.internal.elevenlabs import patch as patch_module
from ddtrace.contrib.internal.elevenlabs._state import _CURRENT_STATE
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import CACHED_LLMOBS_EVENT_CTX_KEY
from ddtrace.llmobs._constants import CACHED_LLMOBS_ROUTING_CTX_KEY
from ddtrace.llmobs._integrations import elevenlabs_agents as _state
from ddtrace.llmobs._integrations.elevenlabs import ElevenLabsIntegration
from ddtrace.llmobs._integrations.elevenlabs_agents import ConversationState
from ddtrace.llmobs._routing import get_routing_context
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_trace_id
from tests.llmobs._utils import assert_llmobs_span_data


PCM = b"\x01\x00" * 1600
B64 = base64.b64encode(PCM).decode()
BASE_NS = 1_790_000_000_000_000_000


class Clock:
    def __init__(self):
        self.now = BASE_NS

    def advance(self, seconds=1):
        self.now += int(seconds * 1e9)


def metadata(session="session-test", fmt="pcm_16000"):
    return {
        "type": "conversation_initiation_metadata",
        "conversation_initiation_metadata_event": {
            "conversation_id": session,
            "user_input_audio_format": fmt,
            "agent_output_audio_format": fmt,
        },
    }


def event(kind, event_id, **data):
    return {"type": kind, _state._EVENT_FIELDS[kind]: {"event_id": event_id, **data}}


def receive(state, value):
    state.on_receive(json.dumps(value))


def input_audio(state, audio=B64):
    state.on_send(json.dumps({"user_audio_chunk": audio}))


def rows(test_spans):
    spans = [span for trace in test_spans.pop_traces() for span in trace]
    return [
        (span, span._get_ctx_item(CACHED_LLMOBS_EVENT_CTX_KEY))
        for span in spans
        if span._get_ctx_item(CACHED_LLMOBS_EVENT_CTX_KEY)
    ]


def llm_rows(test_spans):
    return [(span, row) for span, row in rows(test_spans) if row["meta"]["span"]["kind"] == "llm"]


@pytest.fixture
def state(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(_state.time, "time_ns", lambda: clock.now)
    value = ConversationState(ElevenLabsIntegration(config.elevenlabs), SimpleNamespace(agent_id="test-agent"))
    receive(value, metadata())
    yield value, clock
    value.close()


def decoded_frames(message):
    part = message["audio_parts"][0]
    with wave.open(io.BytesIO(base64.b64decode(part["content"])), "rb") as stream:
        assert stream.getframerate() == 16000
        return stream.readframes(stream.getnframes())


def test_audio_first_late_text_and_serialized_turn_layout(state, test_spans):
    value, clock = state
    input_audio(value)
    receive(value, event("audio", 37, audio_base_64=B64, is_final=False))
    clock.advance()
    receive(value, event("user_transcript", 37, user_transcript="Synthetic question"))
    receive(value, event("agent_response", 37, agent_response="Synthetic answer"))
    value.close()
    captured = rows(test_spans)
    llm, data = next((span, row) for span, row in captured if row["meta"]["span"]["kind"] == "llm")
    root = next(span for span, row in captured if row["name"] == "elevenlabs audio turn")
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(llm),
        span_kind="llm",
        name="elevenlabs response",
        model_provider="elevenlabs",
        parent_id=str(root.span_id),
        input_messages=data["meta"]["input"]["messages"],
        output_messages=data["meta"]["output"]["messages"],
        metadata=data["meta"]["metadata"],
    )
    assert decoded_frames(data["meta"]["input"]["messages"][0]) == PCM
    assert decoded_frames(data["meta"]["output"]["messages"][0]) == PCM
    children = [row for _, row in captured if row["parent_id"] == str(root.span_id)]
    assert {row["name"] for row in children} == {"user speech", "agent speech", "elevenlabs response"}
    assert {row["session_id"] for _, row in captured} == {"session-test"}
    assert all(row["meta"]["metadata"]["ttfa_eligible"] is False for _, row in captured)
    assert all(
        "audio_parts" not in json.dumps(row["meta"]) for _, row in captured if row["meta"]["span"]["kind"] == "workflow"
    )


def test_missing_completion_repeated_text_and_bounded_finalization(state, test_spans):
    value, clock = state
    for event_id in (37, 117, 280):
        clock.advance()
        input_audio(value)
        receive(value, event("user_transcript", event_id, user_transcript="Same phrase"))
        receive(value, event("audio", event_id, audio_base_64=B64, is_final=False))
        receive(value, event("agent_response", event_id, agent_response=str(event_id)))
        assert len(value.turns) <= 2
    receive(value, event("agent_response", 37, agent_response="Stale response"))
    clock.advance()
    value.close()
    value.close()
    captured = llm_rows(test_spans)
    assert len(captured) == 3
    assert {row["meta"]["metadata"]["event_id"] for _, row in captured} == {"37", "117", "280"}
    assert all(row["meta"]["input"]["messages"][0]["content"] == "Same phrase" for _, row in captured)
    assert value.closed and not value.turns and not value.pending.data


def test_interruption_trims_projected_audio_but_accepts_late_transcript(state, test_spans):
    value, clock = state
    receive(value, event("audio", 236, audio_base_64=base64.b64encode(PCM * 10).decode()))
    clock.advance(0.25)
    receive(value, {"type": "interruption", "interruption_event": {"event_id": 274}})
    receive(value, event("audio", 236, audio_base_64=B64))
    receive(value, event("agent_response", 236, agent_response="Late interrupted transcript"))
    receive(value, event("user_transcript", 280, user_transcript="Interrupting question"))
    receive(value, event("agent_response", 280, agent_response="New answer"))
    clock.advance()
    value.close()
    captured = llm_rows(test_spans)
    interrupted = next(row for _, row in captured if row["meta"]["metadata"]["event_id"] == "236")
    output = interrupted["meta"]["output"]["messages"][0]
    assert output["content"] == "Late interrupted transcript"
    assert decoded_frames(output) == PCM * 2 + PCM[:1600]
    assert interrupted["meta"]["metadata"]["interrupted"] is True


@pytest.mark.parametrize("fmt", ["ulaw_8000", "mp3_44100", "unknown"])
def test_unsupported_formats_preserve_text_without_guessing_audio(state, test_spans, fmt):
    value, clock = state
    receive(value, metadata(fmt=fmt))
    input_audio(value)
    receive(value, event("user_transcript", 37, user_transcript="Text survives"))
    receive(value, event("audio", 37, audio_base_64=B64))
    receive(value, event("agent_response", 37, agent_response="Answer survives"))
    clock.advance()
    value.close()
    _, data = llm_rows(test_spans)[0]
    assert data["meta"]["input"]["messages"] == [{"role": "user", "content": "Text survives"}]
    assert data["meta"]["output"]["messages"] == [{"role": "assistant", "content": "Answer survives"}]


def test_shared_audio_budget_and_continuous_input_tail(state, test_spans, monkeypatch):
    value, clock = state
    monkeypatch.setattr(_state, "_RAW_LIMIT", len(PCM) + 100)
    input_audio(value)
    receive(value, event("user_transcript", 37, user_transcript="Question"))
    receive(value, event("audio", 37, audio_base_64=B64))
    receive(value, event("agent_response", 37, agent_response="Answer"))
    clock.advance()
    input_audio(value)
    value.close()
    _, data = llm_rows(test_spans)[0]
    assert data["meta"]["metadata"]["audio_omitted"] is True
    assert data["meta"]["input"]["messages"][0]["content"] == "Question"
    assert data["meta"]["output"]["messages"][0]["content"] == "Answer"


@pytest.mark.parametrize("parent_kind", ["none", "apm", "workflow"])
def test_parent_context_and_conversation_isolation(tracer, test_spans, parent_kind):
    parent = None
    if parent_kind == "apm":
        parent = tracer.trace("application")
    elif parent_kind == "workflow":
        parent = ElevenLabsIntegration(config.elevenlabs).trace("application", submit_to_llmobs=True, activate=True)
        ElevenLabsIntegration(config.elevenlabs).annotate_voice_span(
            parent, "workflow", "application", "application-session", {}
        )
    values = [
        ConversationState(ElevenLabsIntegration(config.elevenlabs), SimpleNamespace(agent_id="agent"), parent)
        for _ in range(2)
    ]
    for i, value in enumerate(values):
        receive(value, metadata(session="conversation-" + str(i)))
        receive(value, event("agent_response", 37, agent_response="Answer " + str(i)))
        value.close()
    if parent is not None:
        parent.finish()
    captured = rows(test_spans)
    roots = [row for _, row in captured if row["name"] == "elevenlabs audio turn"]
    assert len(roots) == 2
    assert {row["session_id"] for row in roots} == {"conversation-0", "conversation-1"}
    if parent_kind == "workflow":
        assert {row["parent_id"] for row in roots} == {str(parent.span_id)}
    else:
        assert all(row["parent_id"] == "undefined" for row in roots)


@pytest.mark.parametrize("failure", [False, True])
def test_real_client_tool_execution_and_result_ownership(state, test_spans, failure):
    value, clock = state
    receive(
        value, event("client_tool_call", 37, tool_name="weather", tool_call_id="call-1", parameters={"city": "Test"})
    )
    tools = conversation.ClientTools()
    done = threading.Event()

    def handler(parameters):
        if failure:
            raise ValueError("Synthetic tool failure")
        return "Synthetic weather"

    def callback(result):
        value.on_send(json.dumps(result))
        done.set()

    tools.register("weather", handler)
    tools.start()
    token = _CURRENT_STATE.set(value)
    try:
        tools.execute_tool("weather", {"city": "Test", "tool_call_id": "call-1"}, callback)
        assert done.wait(3)
    finally:
        _CURRENT_STATE.reset(token)
        tools.stop()
    clock.advance()
    value.close()
    captured = rows(test_spans)
    tool = next(row for _, row in captured if row["meta"]["span"]["kind"] == "tool")
    llm = next(row for _, row in captured if row["meta"]["span"]["kind"] == "llm")
    assert tool["parent_id"] == llm["parent_id"]
    assert tool["status"] == ("error" if failure else "ok")
    assert llm["meta"]["input"]["messages"][0]["tool_results"][0]["tool_id"] == "call-1"


class ScriptAudio:
    def start(self, input_callback):
        self.input_callback = input_callback

    def output(self, audio):
        pass

    def interrupt(self):
        pass

    def stop(self):
        pass


class AsyncScriptAudio:
    async def start(self, input_callback):
        self.input_callback = input_callback

    async def output(self, audio):
        pass

    async def interrupt(self):
        pass

    async def stop(self):
        pass


class Socket:
    def __init__(self, instance, events, clock):
        self.instance = instance
        self.events = iter(events)
        self.clock = clock
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def send(self, message):
        self.sent.append(json.loads(message))

    def recv(self, **kwargs):
        self.clock.advance()
        try:
            value = next(self.events)
        except StopIteration:
            self.instance.end_session()
            return json.dumps({"type": "end"})
        if value["type"] == "user_transcript":
            self.instance.audio_interface.input_callback(PCM)
        return json.dumps(value)


@pytest.mark.parametrize("default_interface", [False, True])
@pytest.mark.parametrize("vad_enabled", [False, True])
def test_ordinary_sync_sdk_with_normal_event_subset(monkeypatch, test_spans, default_interface, vad_enabled):
    clock = Clock()
    monkeypatch.setattr(_state.time, "time_ns", lambda: clock.now)
    if default_interface:
        stream = SimpleNamespace(write=lambda data: None, stop_stream=lambda: None, close=lambda: None)
        pyaudio = SimpleNamespace(
            paInt16=8,
            paContinue=0,
            PyAudio=lambda: SimpleNamespace(open=lambda **kw: stream, terminate=lambda: None),
        )
        monkeypatch.setitem(sys.modules, "pyaudio", pyaudio)
        from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface

        audio = DefaultAudioInterface()
    else:
        audio = ScriptAudio()
    instance = conversation.Conversation(
        ElevenLabs(api_key="test-key"), "test-agent", requires_auth=False, audio_interface=audio
    )
    events = [
        metadata(),
        *([{"type": "vad_score", "vad_score_event": {"vad_score": 0}}] if vad_enabled else []),
        event("user_transcript", 37, user_transcript="Question"),
        event("audio", 37, audio_base_64=B64),
        event("agent_response", 37, agent_response="Answer"),
    ]
    raw = Socket(instance, events, clock)
    patch_module.unpatch()
    monkeypatch.setattr(conversation, "connect", lambda *args, **kwargs: raw)
    patch_module.patch()
    try:
        instance.start_session()
        instance.wait_for_session_end()
    finally:
        instance.end_session()
    assert instance.audio_interface is audio
    captured = rows(test_spans)
    data = next(row for _, row in captured if row["name"] == "elevenlabs response")
    phase = next(row for _, row in captured if row["name"] == "user speech")
    assert ("user_speech_activity" in phase["meta"]["metadata"]) is vad_enabled
    assert decoded_frames(data["meta"]["input"]["messages"][0]) == PCM
    assert decoded_frames(data["meta"]["output"]["messages"][0]) == PCM


@pytest.mark.asyncio
@pytest.mark.parametrize("default_interface", [False, True])
@pytest.mark.parametrize("vad_enabled", [False, True])
async def test_ordinary_async_sdk_with_custom_interface(monkeypatch, test_spans, default_interface, vad_enabled):
    clock = Clock()
    monkeypatch.setattr(_state.time, "time_ns", lambda: clock.now)
    if default_interface:
        stream = SimpleNamespace(write=lambda data: None, stop_stream=lambda: None, close=lambda: None)
        pyaudio = SimpleNamespace(
            paInt16=8,
            paContinue=0,
            PyAudio=lambda: SimpleNamespace(open=lambda **kw: stream, terminate=lambda: None),
        )
        monkeypatch.setitem(sys.modules, "pyaudio", pyaudio)
        from elevenlabs.conversational_ai.default_audio_interface import AsyncDefaultAudioInterface

        audio = AsyncDefaultAudioInterface()
    else:
        audio = AsyncScriptAudio()
    instance = conversation.AsyncConversation(
        ElevenLabs(api_key="test-key"), "test-agent", requires_auth=False, audio_interface=audio
    )
    events = iter(
        [
            metadata(),
            *([{"type": "vad_score", "vad_score_event": {"vad_score": 0}}] if vad_enabled else []),
            event("user_transcript", 37, user_transcript="Question"),
            event("audio", 37, audio_base_64=B64),
            event("agent_response", 37, agent_response="Answer"),
        ]
    )

    class AsyncSocket:
        async def send(self, raw):
            pass

        async def recv(self):
            clock.advance()
            await asyncio.sleep(0)
            try:
                value = next(events)
            except StopIteration:
                await instance.end_session()
                return json.dumps({"type": "end"})
            if value["type"] == "user_transcript":
                await instance.audio_interface.input_callback(PCM)
            return json.dumps(value)

    class AsyncConnect:
        async def __aenter__(self):
            return AsyncSocket()

        async def __aexit__(self, *args):
            return False

    import websockets

    monkeypatch.setattr(websockets, "connect", lambda *args, **kw: AsyncConnect())
    try:
        await instance.start_session()
        await instance.wait_for_session_end()
    finally:
        await instance.end_session()
    captured = rows(test_spans)
    data = next(row for _, row in captured if row["name"] == "elevenlabs response")
    phase = next(row for _, row in captured if row["name"] == "user speech")
    assert ("user_speech_activity" in phase["meta"]["metadata"]) is vad_enabled
    assert decoded_frames(data["meta"]["input"]["messages"][0]) == PCM
    assert decoded_frames(data["meta"]["output"]["messages"][0]) == PCM


@pytest.mark.parametrize("normal_events_only", [False, True])
def test_sanitized_capture_replay(state, test_spans, normal_events_only):
    value, clock = state
    with gzip.open(Path(__file__).parent / "data" / "conversation.json.gz", "rt") as stream:
        fixture = json.load(stream)
    ordinary = {
        "conversation_initiation_metadata",
        "audio",
        "user_transcript",
        "agent_response",
        "agent_response_correction",
        "interruption",
        "client_tool_call",
    }
    for record in fixture:
        clock.now = BASE_NS + record["offset_ns"]
        message = record["event"]
        if record["direction"] == "send":
            value.on_send(json.dumps(message))
        elif not normal_events_only or message["type"] in ordinary:
            receive(value, message)
    clock.advance()
    value.close()
    captured = llm_rows(test_spans)
    by_id = {row["meta"]["metadata"]["event_id"]: row for _, row in captured}
    assert {"37", "117", "236", "280", "544"} <= by_id.keys()
    assert (
        by_id["37"]["meta"]["input"]["messages"][0]["content"]
        == by_id["117"]["meta"]["input"]["messages"][0]["content"]
    )
    assert by_id["117"]["meta"]["metadata"]["interrupted"] is False
    assert by_id["236"]["meta"]["metadata"]["interrupted"] is True
    assert by_id["236"]["meta"]["output"]["messages"][0]["content"] == "Synthetic response 236"
    assert all(row["meta"]["metadata"]["ttfa_eligible"] is False for row in by_id.values())


def test_typed_input_without_echoed_transcript(state, test_spans):
    value, clock = state
    value.on_send(json.dumps({"type": "user_message", "text": "Typed question"}))
    receive(value, event("audio", 37, audio_base_64=B64))
    receive(value, event("agent_response", 37, agent_response="Answer"))
    clock.advance()
    value.close()
    _, data = llm_rows(test_spans)[0]
    assert data["meta"]["input"]["messages"][0]["content"] == "Typed question"


@pytest.mark.parametrize("streamed", [False, True])
def test_multiple_agent_responses_share_a_turn_without_overwriting_text(state, test_spans, streamed):
    value, clock = state
    input_audio(value)
    receive(value, event("user_transcript", 37, user_transcript="Question"))
    for response_id, text in (("before-tool", "Let me check."), ("after-tool", "The answer is 42.")):
        if streamed:
            for part_type, part in (("start", ""), ("delta", text), ("stop", "")):
                receive(
                    value, event("agent_chat_response_part", 37, response_id=response_id, type=part_type, text=part)
                )
        else:
            receive(value, event("agent_response", 37, response_id=response_id, agent_response=text))
        receive(value, event("audio", 37, audio_base_64=B64))
        clock.advance()
        if response_id == "before-tool":
            receive(value, event("client_tool_call", 37, tool_call_id="lookup", tool_name="lookup", parameters={}))
            value.on_send(json.dumps({"type": "client_tool_result", "tool_call_id": "lookup", "result": "42"}))
    # A final response can be resent, and a correction can target an earlier utterance.
    for _ in range(2):
        receive(value, event("agent_response", 37, response_id="after-tool", agent_response="The answer is 42."))
    receive(
        value,
        event("agent_response_correction", 37, response_id="before-tool", corrected_agent_response="I checked."),
    )
    value.close()
    captured = rows(test_spans)
    data = next(row for _, row in captured if row["name"] == "elevenlabs response")
    assert data["meta"]["output"]["messages"][0]["content"] == "I checked.\nThe answer is 42."
    assert decoded_frames(data["meta"]["output"]["messages"][0]) == PCM * 2
    assert data["meta"]["output"]["messages"][0]["tool_calls"][0]["tool_id"] == "lookup"
    assert data["meta"]["input"]["messages"][0]["tool_results"][0]["result"] == "42"
    assert next(row for _, row in captured if row["name"] == "elevenlabs audio turn")["meta"]["output"]["value"] == (
        "I checked.\nThe answer is 42."
    )


def test_streamed_responses_preserve_repeated_utterances(state, test_spans):
    value, _ = state
    for response_id in ("first", "second"):
        for part_type, text in (("start", ""), ("delta", "Same phrase"), ("stop", "")):
            receive(value, event("agent_chat_response_part", 37, response_id=response_id, type=part_type, text=text))
    value.close()
    assert llm_rows(test_spans)[0][1]["meta"]["output"]["messages"][0]["content"] == "Same phrase\nSame phrase"


@pytest.mark.parametrize("final_text", ["Final answer", ""])
def test_final_response_replaces_its_stream_and_ignores_late_parts(state, test_spans, final_text):
    value, _ = state
    receive(value, event("agent_chat_response_part", 37, response_id="first", type="delta", text="Draft"))
    receive(value, event("agent_response", 37, response_id="first", agent_response=final_text))
    receive(value, event("agent_chat_response_part", 37, response_id="first", type="start", text="Late"))
    receive(value, event("agent_chat_response_part", 37, response_id="first", type="delta", text=" part"))
    value.close()
    assert llm_rows(test_spans)[0][1]["meta"]["output"]["messages"][0]["content"] == final_text


def test_response_events_without_response_ids_retain_legacy_replacement(state, test_spans):
    value, _ = state
    receive(value, event("agent_chat_response_part", 37, type="start", text="Draft"))
    receive(value, event("agent_response", 37, agent_response="Original"))
    receive(value, event("agent_response_correction", 37, corrected_agent_response="Corrected"))
    value.close()
    assert llm_rows(test_spans)[0][1]["meta"]["output"]["messages"][0]["content"] == "Corrected"


def test_input_before_metadata_is_retained(monkeypatch, test_spans):
    clock = Clock()
    monkeypatch.setattr(_state.time, "time_ns", lambda: clock.now)
    value = ConversationState(ElevenLabsIntegration(config.elevenlabs), SimpleNamespace(agent_id="test-agent"))
    input_audio(value)
    receive(value, metadata())
    receive(value, event("user_transcript", 37, user_transcript="Question"))
    clock.advance()
    value.close()
    _, data = llm_rows(test_spans)[0]
    assert decoded_frames(data["meta"]["input"]["messages"][0]) == PCM
    assert not value.early_audio


@pytest.mark.parametrize("event_id", [None, True, -1, 1.5, "bad", [], {}])
def test_invalid_event_ids_do_not_open_turns(state, event_id):
    value, _ = state
    receive(value, event("agent_response", event_id, agent_response="Ignore"))
    assert not value.turns


@pytest.mark.parametrize("direction", ["send", "recv"])
def test_transport_errors_preserve_partial_turn_and_mark_error(state, test_spans, direction):
    value, clock = state
    receive(value, event("agent_response", 37, agent_response="Partial answer"))

    class FailingSocket:
        def send(self, raw):
            raise RuntimeError("Synthetic connection failure")

        def recv(self):
            raise RuntimeError("Synthetic connection failure")

    socket = patch_module._Socket(FailingSocket(), value)
    with pytest.raises(RuntimeError, match="Synthetic connection failure"):
        if direction == "send":
            socket.send(json.dumps({"user_audio_chunk": B64}))
        else:
            socket.recv()
    clock.advance()
    value.close()
    _, data = llm_rows(test_spans)[0]
    assert data["status"] == "error"
    assert data["meta"]["output"]["messages"][0]["content"] == "Partial answer"


def test_observation_failure_does_not_change_socket_result(state, monkeypatch):
    value, _ = state
    raw = json.dumps({"type": "unknown"})
    socket = patch_module._Socket(SimpleNamespace(send=lambda raw: "sent", recv=lambda: raw), value)

    def fail(*args):
        raise ValueError("Synthetic observer failure")

    monkeypatch.setattr(value, "on_send", fail)
    monkeypatch.setattr(value, "on_receive", fail)
    assert socket.send(raw) == "sent"
    assert socket.recv() == raw


def test_unpatch_restores_sdk_and_does_not_patch_global_websockets():
    import websockets

    original = websockets.connect
    patch_module.patch()
    assert websockets.connect is original
    assert conversation.websockets is not websockets
    patch_module.unpatch()
    assert conversation.websockets is websockets
    assert not hasattr(conversation.Conversation._run, "__wrapped__")
    patch_module.patch()
    patch_module.patch()
    assert conversation.Conversation._run.__wrapped__.__name__ == "_run"


def test_disabled_llmobs_does_not_create_conversation_state(elevenlabs_llmobs):
    elevenlabs_llmobs.disable()
    instance = SimpleNamespace(agent_id="test-agent")
    assert patch_module._new_state(instance) is None
    assert not hasattr(instance, "_dd_elevenlabs_state")


def test_concurrent_sdk_workers_keep_identical_event_ids_separate(monkeypatch, test_spans):
    barrier = threading.Barrier(2)
    worker_clock = threading.local()
    instances = [
        conversation.Conversation(
            ElevenLabs(api_key="test-key"),
            "agent-" + str(i),
            requires_auth=False,
            audio_interface=ScriptAudio(),
        )
        for i in range(2)
    ]

    class ConcurrentSocket(Socket):
        def recv(self, **kwargs):
            if not self.sent or len(self.sent) == 1:
                barrier.wait(timeout=3)
                self.sent.append({"type": "barrier-passed"})
            raw = super().recv(**kwargs)
            worker_clock.now = self.clock.now
            return raw

    sockets = {
        instance.agent_id: ConcurrentSocket(
            instance,
            [
                metadata(session=instance.agent_id),
                event("user_transcript", 37, user_transcript=instance.agent_id),
                event("audio", 37, audio_base_64=B64),
                event("agent_response", 37, agent_response="Answer " + instance.agent_id),
            ],
            Clock(),
        )
        for instance in instances
    }
    monkeypatch.setattr(
        _state.time,
        "time_ns",
        lambda: getattr(worker_clock, "now", BASE_NS),
    )
    patch_module.unpatch()
    monkeypatch.setattr(conversation, "connect", lambda *args, **kwargs: sockets[_CURRENT_STATE.get().agent_id])
    patch_module.patch()
    try:
        for instance in instances:
            instance.start_session()
        for instance in instances:
            instance.wait_for_session_end()
    finally:
        for instance in instances:
            instance.end_session()
    captured = llm_rows(test_spans)
    assert len(captured) == 2
    assert {row["session_id"] for _, row in captured} == {"agent-0", "agent-1"}
    for _, row in captured:
        assert row["meta"]["input"]["messages"][0]["content"] == row["session_id"]
        assert decoded_frames(row["meta"]["output"]["messages"][0]) == PCM


@pytest.mark.asyncio
async def test_tool_observation_failure_preserves_handler_result(state, monkeypatch):
    value, _ = state

    def fail(*args):
        raise ValueError("Synthetic annotation failure")

    monkeypatch.setattr(value, "begin_tool", fail)

    async def handler(*args):
        return "Tool result"

    token = _CURRENT_STATE.set(value)
    try:
        result = await patch_module.traced_tool(handler, None, ("weather", {"tool_call_id": "call-1"}), {})
        assert result == "Tool result"
    finally:
        _CURRENT_STATE.reset(token)


def vad(value, score):
    receive(value, {"type": "vad_score", "vad_score_event": {"vad_score": score}})


def phase_rows(test_spans):
    return [row for _, row in rows(test_spans) if row["name"] == "user speech"]


def test_vad_serialized_activity_preserves_wav_placement_and_ttfa_opt_out(state, test_spans):
    value, clock = state
    vad(value, 0)
    for i in range(1, 11):
        clock.advance(0.1)
        input_audio(value)
        vad(value, 0.9 if i in (2, 3, 7, 8) else 0)
    receive(value, event("user_transcript", 37, user_transcript="Same phrase"))
    receive(value, event("audio", 37, audio_base_64=B64))
    clock.advance(0.1)
    value.close()
    captured = rows(test_spans)
    phase = next(row for _, row in captured if row["name"] == "user speech")
    activity = phase["meta"]["metadata"]["user_speech_activity"]
    assert activity == {
        "version": 1,
        "source": "elevenlabs_vad",
        "timing": "estimated",
        "intervals_ms": [[200, 400], [700, 900]],
    }
    assert phase["start_ns"] == BASE_NS
    assert phase["duration"] == 1_000_000_000
    data = next(row for _, row in captured if row["name"] == "elevenlabs response")
    assert decoded_frames(data["meta"]["input"]["messages"][0]) == PCM * 10
    assert decoded_frames(data["meta"]["output"]["messages"][0]) == PCM
    assert all(row["meta"]["metadata"]["ttfa_eligible"] is False for _, row in captured)
    assert all("user_speech_activity" not in row["meta"]["metadata"] for _, row in captured if row is not phase)
    assert len(json.dumps(activity)) < 200


def test_vad_silence_during_agent_audio_and_shutdown_tail(state, test_spans):
    value, clock = state
    vad(value, 0)
    for _ in range(3):
        clock.advance(0.1)
        input_audio(value)
        vad(value, 0)
    receive(value, event("audio", 37, audio_base_64=B64))
    for _ in range(3):
        clock.advance(0.1)
        input_audio(value)
        vad(value, 0)
    value.close()
    captured = rows(test_spans)
    phase = next(row for _, row in captured if row["name"] == "user speech")
    assert phase["meta"]["metadata"]["user_speech_activity"]["intervals_ms"] == []
    assert phase["duration"] == 600_000_000
    data = next(row for _, row in captured if row["name"] == "elevenlabs response")
    assert decoded_frames(data["meta"]["input"]["messages"][0]) == PCM * 6


def test_vad_turn_transfer_keeps_activity_relative_to_each_clip(state, test_spans):
    value, clock = state
    vad(value, 0)
    for event_id in (37, 117, 280):
        for i in range(3):
            clock.advance(0.1)
            input_audio(value)
            vad(value, 1 if i == 0 else 0)
        receive(value, event("user_transcript", event_id, user_transcript="Repeated phrase"))
    value.close()
    phases = phase_rows(test_spans)
    assert len(phases) == 3
    assert all(p["meta"]["metadata"]["user_speech_activity"]["intervals_ms"] == [[100, 200]] for p in phases)
    assert sorted(p["start_ns"] for p in phases) == [BASE_NS, BASE_NS + 300_000_000, BASE_NS + 600_000_000]


@pytest.mark.parametrize("score", [None, True, "0.1", float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_vad_retains_phase_fallback(state, test_spans, score):
    value, clock = state
    input_audio(value)
    vad(value, score)
    receive(value, event("agent_response", 37, agent_response="Answer"))
    value.close()
    assert "user_speech_activity" not in phase_rows(test_spans)[0]["meta"]["metadata"]


def test_vad_missing_and_sparse_events_leave_unknown_audio_active(state, test_spans):
    value, clock = state
    input_audio(value)
    vad(value, 0)
    clock.advance(1)
    input_audio(value)
    vad(value, 0)
    clock.advance(0.1)
    input_audio(value)
    receive(value, event("agent_response", 37, agent_response="Answer"))
    value.close()
    # No VAD for the first block, and stale delivery between the next two.
    assert phase_rows(test_spans)[0]["meta"]["metadata"]["user_speech_activity"]["intervals_ms"] == [[0, 200]]


def test_vad_silence_hold_is_bounded_by_samples(state, test_spans):
    value, clock = state
    vad(value, 0)
    input_audio(value, base64.b64encode(PCM * 10).decode())
    receive(value, event("agent_response", 37, agent_response="Answer"))
    value.close()
    assert phase_rows(test_spans)[0]["meta"]["metadata"]["user_speech_activity"]["intervals_ms"] == [[500, 1000]]


def test_vad_hysteresis_and_conversation_isolation(state, test_spans):
    value, clock = state
    other = ConversationState(ElevenLabsIntegration(config.elevenlabs), SimpleNamespace(agent_id="other"))
    receive(other, metadata(session="other"))
    vad(value, 0)
    vad(other, 1)
    for score in (0.4, 0.6, 0.4, 0.3):
        clock.advance(0.1)
        for connection in (value, other):
            input_audio(connection)
        vad(value, score)
        vad(other, 1)
    for connection in (value, other):
        receive(connection, event("agent_response", 37, agent_response="Answer"))
        connection.close()
    phases = {row["session_id"]: row for row in phase_rows(test_spans)}
    assert phases["session-test"]["meta"]["metadata"]["user_speech_activity"]["intervals_ms"] == [[200, 400]]
    assert phases["other"]["meta"]["metadata"]["user_speech_activity"]["intervals_ms"] == [[0, 400]]


def test_vad_interval_overflow_and_audio_budget_retain_fallback(state, test_spans, monkeypatch):
    value, clock = state
    vad(value, 0)
    for i in range(260):
        clock.advance(0.1)
        input_audio(value)
        vad(value, i % 2)
    assert value.pending.activity.overflow
    assert not value.pending.activity.silence
    receive(value, event("agent_response", 37, agent_response="Answer"))
    value.close()
    assert "user_speech_activity" not in phase_rows(test_spans)[0]["meta"]["metadata"]

    other = ConversationState(ElevenLabsIntegration(config.elevenlabs), SimpleNamespace(agent_id="other"))
    receive(other, metadata(session="other"))
    monkeypatch.setattr(_state, "_RAW_LIMIT", 1)
    vad(other, 0)
    input_audio(other)
    receive(other, event("agent_response", 37, agent_response="Answer"))
    other.close()
    assert all("user_speech_activity" not in row["meta"]["metadata"] for _, row in rows(test_spans))


def test_vad_stale_score_does_not_color_audio_after_a_microphone_pause(state, test_spans):
    value, clock = state
    vad(value, 0)
    clock.advance(2)
    input_audio(value)
    receive(value, event("agent_response", 37, agent_response="Answer"))
    value.close()
    assert phase_rows(test_spans)[0]["meta"]["metadata"]["user_speech_activity"]["intervals_ms"] == [[0, 100]]


def test_vad_millisecond_quantization_coalesces_overlapping_activity():
    activity = _state.SpeechActivity()
    activity.observe(1, 0, BASE_NS)
    activity.observe(0, 100_000, BASE_NS + 100_000)
    activity.observe(1, 200_000, BASE_NS + 200_000)
    assert activity.metadata(1_000_000, BASE_NS + 1_000_000)["intervals_ms"] == [[0, 1]]


@pytest.mark.parametrize("known_turn", [False, True])
@pytest.mark.parametrize(
    "late_kind",
    ["agent_response", "agent_response_correction", "user_transcript", "agent_chat_response_part", "client_tool_call"],
)
def test_late_interrupted_events_preserve_next_input_and_activity(state, test_spans, known_turn, late_kind):
    value, clock = state
    stale_id = 37 if known_turn else 99
    if known_turn:
        input_audio(value)
        receive(value, event("user_transcript", stale_id, user_transcript="Original question"))
    receive(value, {"type": "interruption", "interruption_event": {"event_id": 100}})
    vad(value, 0)
    clock.advance(0.1)
    input_audio(value)
    vad(value, 1)
    clock.advance(0.1)
    input_audio(value)
    vad(value, 0)
    value.on_send(json.dumps({"type": "user_message", "text": "Next typed input"}))
    pending = value.pending
    receive(
        value,
        event(
            late_kind,
            stale_id,
            agent_response="Late answer",
            corrected_agent_response="Late correction",
            user_transcript="Late question",
            text="Late part",
            type="start",
            tool_call_id="late-tool",
            tool_name="weather",
            parameters={},
        ),
    )
    assert value.pending is pending
    assert value.pending_text == "Next typed input"
    assert bytes(value.pending.data) == PCM * 2
    assert (stale_id in value.turns) is known_turn
    receive(value, event("agent_response", 101, agent_response="Next answer"))
    assert value.turns[101].user_text == "Next typed input"
    receive(value, event("user_transcript", 101, user_transcript="Next question"))
    value.close()
    captured = rows(test_spans)
    next_llm = next(
        row
        for _, row in captured
        if row["name"] == "elevenlabs response" and row["meta"]["metadata"]["event_id"] == "101"
    )
    assert decoded_frames(next_llm["meta"]["input"]["messages"][0]) == PCM * 2
    assert next_llm["meta"]["input"]["messages"][0]["content"] == "Next question"
    assert next_llm["meta"]["output"]["messages"][0]["content"] == "Next answer"
    next_phase = next(
        row for _, row in captured if row["name"] == "user speech" and row["meta"]["metadata"]["event_id"] == "101"
    )
    assert next_phase["meta"]["metadata"]["user_speech_activity"]["intervals_ms"] == [[100, 200]]
    assert next_phase["duration"] == 200_000_000
    if known_turn:
        old_llm = next(
            row
            for _, row in captured
            if row["name"] == "elevenlabs response" and row["meta"]["metadata"]["event_id"] == "37"
        )
        assert old_llm["meta"]["metadata"]["interrupted"] is True
        assert decoded_frames(old_llm["meta"]["input"]["messages"][0]) == PCM
        if late_kind == "agent_response_correction":
            assert old_llm["meta"]["output"]["messages"][0]["content"] == "Late correction"
        elif late_kind == "user_transcript":
            assert old_llm["meta"]["input"]["messages"][0]["content"] == "Late question"
    else:
        assert {row["meta"]["metadata"]["event_id"] for _, row in captured} == {"101"}
    assert all(row["meta"]["metadata"]["ttfa_eligible"] is False for _, row in captured)


@pytest.mark.parametrize("parent_kind", ["none", "apm", "workflow", "finished_workflow"])
def test_captured_parent_is_independent_of_event_processing_context(monkeypatch, tracer, test_spans, parent_kind):
    clock = Clock()
    monkeypatch.setattr(_state.time, "time_ns", lambda: clock.now)
    assert tracer.context_provider.active() is None
    parent = None
    if parent_kind == "apm":
        parent = tracer.trace("captured-apm")
    elif parent_kind != "none":
        parent = LLMObs.workflow(name="captured-workflow")
    value = ConversationState(ElevenLabsIntegration(config.elevenlabs), SimpleNamespace(agent_id="agent"), parent)
    if parent_kind == "finished_workflow":
        parent.finish()
    tracer.context_provider.activate(None)
    LLMObs._instance._llmobs_context_provider.activate(None)
    with LLMObs.workflow(name="unrelated-workflow") as unrelated:
        receive(value, metadata())
        input_audio(value)
        receive(value, event("agent_response", 37, agent_response="Answer"))
        receive(value, event("audio", 37, audio_base_64=B64))
        assert tracer.context_provider.active() is unrelated
        assert LLMObs._instance._current_span() is unrelated
        clock.advance(0.2)
        value.close()
        assert tracer.context_provider.active() is unrelated
        assert LLMObs._instance._current_span() is unrelated
    if parent is not None and not parent.finished:
        parent.finish()
    captured = rows(test_spans)
    root, root_row = next((span, row) for span, row in captured if row["name"] == "elevenlabs audio turn")
    assert root.parent_id == (parent.span_id if parent else None)
    assert root.trace_id != unrelated.trace_id
    if parent is not None:
        assert root.trace_id == parent.trace_id
    expected_parent = str(parent.span_id) if parent_kind in ("workflow", "finished_workflow") else "undefined"
    assert root_row["parent_id"] == expected_parent
    assert root_row["trace_id"] != get_llmobs_trace_id(unrelated)
    if parent_kind in ("workflow", "finished_workflow"):
        assert root_row["trace_id"] == get_llmobs_trace_id(parent)
    children = [(span, row) for span, row in captured if row["parent_id"] == str(root.span_id)]
    assert {row["name"] for _, row in children} == {"elevenlabs response", "user speech", "agent speech"}
    assert all(span.parent_id == root.span_id and span.trace_id == root.trace_id for span, _ in children)


def test_sync_start_failure_retry_captures_new_parent(monkeypatch, tracer, test_spans):
    instance = conversation.Conversation(
        ElevenLabs(api_key="test-key"), "test-agent", requires_auth=True, audio_interface=ScriptAudio()
    )
    failure = RuntimeError("Synthetic signed URL failure")

    def fail_url():
        raise failure

    monkeypatch.setattr(instance, "_get_signed_url", fail_url)
    with LLMObs.workflow(name="failed-start") as first:
        with pytest.raises(RuntimeError) as raised:
            instance.start_session()
        assert raised.value is failure
        failed_state = instance._dd_elevenlabs_state
        assert failed_state.closed
        assert tracer.context_provider.active() is first
    monkeypatch.setattr(instance, "_get_signed_url", lambda: "wss://synthetic")
    patch_module.unpatch()
    socket = Socket(instance, [metadata(), event("agent_response", 37, agent_response="Retry answer")], Clock())
    monkeypatch.setattr(conversation, "connect", lambda *args, **kwargs: socket)
    patch_module.patch()
    with LLMObs.workflow(name="retry-start") as second:
        instance.start_session()
        instance.wait_for_session_end()
        assert instance._dd_elevenlabs_state is not failed_state
        assert tracer.context_provider.active() is second
    root, row = next((span, row) for span, row in rows(test_spans) if row["name"] == "elevenlabs audio turn")
    assert root.parent_id == second.span_id
    assert row["parent_id"] == str(second.span_id)
    assert root.trace_id != first.trace_id


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_async_start_failure_retry_captures_new_parent(monkeypatch, tracer, test_spans, cancelled):
    instance = conversation.AsyncConversation(
        ElevenLabs(api_key="test-key"), "test-agent", requires_auth=True, audio_interface=AsyncScriptAudio()
    )
    failure = RuntimeError("Synthetic signed URL failure")

    def fail_url():
        raise failure

    monkeypatch.setattr(instance, "_get_signed_url", fail_url)
    loop = asyncio.get_running_loop()
    run_in_executor = loop.run_in_executor
    with LLMObs.workflow(name="failed-start") as first:
        if cancelled:
            waiting = asyncio.Event()

            def blocked_executor(*args):
                waiting.set()
                return loop.create_future()

            monkeypatch.setattr(loop, "run_in_executor", blocked_executor)
            task = asyncio.create_task(instance.start_session())
            await asyncio.wait_for(waiting.wait(), 3)
            failed_state = instance._dd_elevenlabs_state
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RuntimeError) as raised:
                await instance.start_session()
            assert raised.value is failure
            failed_state = instance._dd_elevenlabs_state
        assert failed_state.closed
        assert tracer.context_provider.active() is first
    monkeypatch.setattr(loop, "run_in_executor", run_in_executor)
    monkeypatch.setattr(instance, "_get_signed_url", lambda: "wss://synthetic")

    class RetrySocket:
        def __init__(self):
            self.events = iter([metadata(), event("agent_response", 37, agent_response="Retry answer")])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def send(self, message):
            pass

        async def recv(self):
            try:
                return json.dumps(next(self.events))
            except StopIteration:
                await instance.end_session()
                return json.dumps({"type": "end"})

    import websockets

    monkeypatch.setattr(websockets, "connect", lambda *args, **kwargs: RetrySocket())
    with LLMObs.workflow(name="retry-start") as second:
        await instance.start_session()
        await instance.wait_for_session_end()
        assert instance._dd_elevenlabs_state is not failed_state
        assert tracer.context_provider.active() is second
    root, row = next((span, row) for span, row in rows(test_spans) if row["name"] == "elevenlabs audio turn")
    assert root.parent_id == second.span_id
    assert row["parent_id"] == str(second.span_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_redundant_start_failure_preserves_ongoing_state(state, asynchronous):
    value, _ = state
    instance = SimpleNamespace(_dd_elevenlabs_state=value)
    failure = RuntimeError("Synthetic redundant startup failure")

    def fail():
        raise failure

    async def async_fail():
        raise failure

    with pytest.raises(RuntimeError) as raised:
        if asynchronous:
            await patch_module.traced_async_start(async_fail, instance, (), {})
        else:
            patch_module.traced_start(fail, instance, (), {})
    assert raised.value is failure
    assert instance._dd_elevenlabs_state is value
    assert not value.closed
    assert not value.error


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sync_error", "async_error", "async_cancel"])
@pytest.mark.parametrize("already_closing", [False, True])
async def test_aborted_external_shutdown_restores_observation(state, test_spans, monkeypatch, mode, already_closing):
    value, _ = state
    asynchronous = mode != "sync_error"
    audio = AsyncScriptAudio() if asynchronous else ScriptAudio()
    cls = conversation.AsyncConversation if asynchronous else conversation.Conversation
    instance = cls(ElevenLabs(api_key="test-key"), "test-agent", requires_auth=False, audio_interface=audio)
    instance._dd_elevenlabs_state = value
    value.closing = already_closing
    failure = RuntimeError("Synthetic audio cleanup failure")
    waiting = asyncio.Event()

    def fail_stop():
        raise failure

    async def async_stop():
        if mode == "async_cancel":
            waiting.set()
            await asyncio.Event().wait()
        raise failure

    monkeypatch.setattr(audio, "stop", async_stop if asynchronous else fail_stop)
    try:
        if mode == "async_cancel":
            task = asyncio.create_task(instance.end_session())
            await asyncio.wait_for(waiting.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RuntimeError) as raised:
                if asynchronous:
                    await instance.end_session()
                else:
                    instance.end_session()
            assert raised.value is failure
        assert not instance._should_stop.is_set()
        assert value.closing is already_closing
        assert not value.closed
        if not already_closing:
            input_audio(value)
            receive(value, event("agent_response", 37, agent_response="Still observed"))
            value.close()
            data = llm_rows(test_spans)[0][1]
            assert data["meta"]["output"]["messages"][0]["content"] == "Still observed"
            assert decoded_frames(data["meta"]["input"]["messages"][0]) == PCM
    finally:
        instance.client_tools.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_shutdown_callback_error_after_stop_keeps_closing(state, asynchronous):
    value, _ = state
    failure = RuntimeError("Synthetic end callback failure")

    def fail_callback():
        raise failure

    async def async_callback():
        raise failure

    cls = conversation.AsyncConversation if asynchronous else conversation.Conversation
    instance = cls(
        ElevenLabs(api_key="test-key"),
        "test-agent",
        requires_auth=False,
        audio_interface=AsyncScriptAudio() if asynchronous else ScriptAudio(),
        callback_end_session=async_callback if asynchronous else fail_callback,
    )
    instance._dd_elevenlabs_state = value
    try:
        with pytest.raises(RuntimeError) as raised:
            if asynchronous:
                await instance.end_session()
            else:
                instance.end_session()
        assert raised.value is failure
        assert instance._should_stop.is_set()
        assert value.closing
    finally:
        instance.client_tools.stop()


def test_zero_event_id_is_valid_before_first_interruption(state, test_spans):
    value, clock = state
    input_audio(value)
    receive(value, event("audio", 0, audio_base_64=B64))
    receive(value, event("agent_response", 0, agent_response="First response"))
    clock.advance()
    value.close()
    _, row = llm_rows(test_spans)[0]
    assert row["meta"]["metadata"]["event_id"] == "0"
    assert decoded_frames(row["meta"]["input"]["messages"][0]) == PCM
    assert decoded_frames(row["meta"]["output"]["messages"][0]) == PCM


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_nested_tool_spans_and_caller_context_restoration(state, tracer, test_spans, outcome):
    value, clock = state
    receive(value, event("client_tool_call", 37, tool_name="weather", tool_call_id="nested-tool", parameters={}))
    failure = ValueError("Synthetic handler failure")

    async def handler(*args, **kwargs):
        with LLMObs.llm(name="nested-llm", model_name="test", model_provider="test") as nested:
            tool = value.tool_spans["nested-tool"]
            assert nested.parent_id == tool.span_id
            assert _get_llmobs_data_metastruct(nested)["parent_id"] == str(tool.span_id)
            await asyncio.sleep(0)
        if outcome == "error":
            raise failure
        if outcome == "cancel":
            raise asyncio.CancelledError()
        return "Synthetic result"

    with LLMObs.workflow(name="unrelated-caller") as caller:
        token = _CURRENT_STATE.set(value)
        try:
            if outcome == "success":
                assert (
                    await patch_module.traced_tool(
                        handler, None, (), {"tool_name": "weather", "parameters": {"tool_call_id": "nested-tool"}}
                    )
                    == "Synthetic result"
                )
            else:
                with pytest.raises(ValueError if outcome == "error" else asyncio.CancelledError) as raised:
                    await patch_module.traced_tool(
                        handler, None, (), {"tool_name": "weather", "parameters": {"tool_call_id": "nested-tool"}}
                    )
                if outcome == "error":
                    assert raised.value is failure
            assert tracer.context_provider.active() is caller
            assert LLMObs._instance._current_span() is caller
            assert not value.tool_spans
        finally:
            _CURRENT_STATE.reset(token)
    clock.advance()
    value.close()
    captured = rows(test_spans)
    tool, tool_row = next((span, row) for span, row in captured if row["name"] == "weather")
    nested_row = next(row for _, row in captured if row["name"] == "nested-llm")
    assert nested_row["parent_id"] == str(tool.span_id)
    assert nested_row["trace_id"] == tool_row["trace_id"]
    assert tool_row["status"] == ("ok" if outcome == "success" else "error")


@pytest.mark.parametrize("limit", ["bytes", "chunks"])
def test_pre_metadata_overflow_omits_audio_and_recovers(monkeypatch, test_spans, limit):
    value = ConversationState(ElevenLabsIntegration(config.elevenlabs), SimpleNamespace(agent_id="test-agent"))
    if limit == "bytes":
        monkeypatch.setattr(_state, "_RAW_LIMIT", 96)
        input_audio(value, base64.b64encode(PCM[:48]).decode())
        input_audio(value, base64.b64encode(PCM[:96]).decode())
    else:
        for _ in range(257):
            input_audio(value, base64.b64encode(PCM[:2]).decode())
    assert not value.early_audio
    receive(value, metadata())
    input_audio(value, base64.b64encode(PCM[:2]).decode())
    receive(value, event("user_transcript", 37, user_transcript="Preserved text"))
    input_audio(value, base64.b64encode(PCM[:2]).decode())
    receive(value, event("user_transcript", 117, user_transcript="Next turn"))
    value.close()
    by_id = {row["meta"]["metadata"]["event_id"]: row for _, row in llm_rows(test_spans)}
    first = by_id["37"]
    assert first["meta"]["metadata"]["audio_omitted"] is True
    assert first["meta"]["input"]["messages"][0] == {"role": "user", "content": "Preserved text"}
    assert decoded_frames(by_id["117"]["meta"]["input"]["messages"][0]) == PCM[:2]
    assert "audio_omitted" not in by_id["117"]["meta"]["metadata"]


@pytest.mark.parametrize("completion_event", [False, True])
@pytest.mark.parametrize("direction", ["send", "recv"])
def test_later_transport_error_preserves_completed_turn(state, test_spans, completion_event, direction):
    value, clock = state
    receive(value, event("agent_response", 37, agent_response="Completed answer"))
    if completion_event:
        receive(value, event("agent_response_complete", 37))
    clock.advance()
    receive(value, event("agent_response", 117, agent_response="Partial answer"))

    class FailingSocket:
        def send(self, *args):
            raise RuntimeError("Later transport failure")

        def recv(self):
            raise RuntimeError("Later transport failure")

    socket = patch_module._Socket(FailingSocket(), value)
    with pytest.raises(RuntimeError, match="Later transport failure"):
        if direction == "send":
            socket.send("{}")
        else:
            socket.recv()
    value.close()
    by_id = {row["meta"]["metadata"]["event_id"]: row for _, row in llm_rows(test_spans)}
    assert by_id["37"]["status"] == "ok"
    assert by_id["117"]["status"] == "error"


@pytest.mark.parametrize("closing", [False, True])
async def test_worker_cancellation_preserves_completed_turn(state, test_spans, closing):
    value, clock = state
    receive(value, event("agent_response", 37, agent_response="Completed answer"))
    receive(value, event("agent_response_complete", 37))
    clock.advance()
    receive(value, event("agent_response", 117, agent_response="Partial answer"))
    started = asyncio.Event()

    async def run():
        started.set()
        await asyncio.Event().wait()

    instance = SimpleNamespace(_dd_elevenlabs_state=value)
    task = asyncio.create_task(patch_module.traced_async_run(run, instance, (), {}))
    await started.wait()
    value.closing = closing
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert value.closed
    by_id = {row["meta"]["metadata"]["event_id"]: row for _, row in llm_rows(test_spans)}
    assert by_id["37"]["status"] == "ok"
    assert by_id["117"]["status"] == ("ok" if closing else "error")


@pytest.mark.parametrize("completion_event", [False, True])
@pytest.mark.parametrize("still_playing", [False, True])
def test_interruption_only_marks_active_response_or_playback(state, test_spans, completion_event, still_playing):
    value, clock = state
    receive(value, event("audio", 37, audio_base_64=base64.b64encode(PCM * 20).decode()))
    receive(value, event("agent_response", 37, agent_response="Complete text"))
    if completion_event:
        receive(value, event("agent_response_complete", 37))
    clock.advance(0.1 if still_playing else 3)
    receive(value, event("audio", 117, audio_base_64=base64.b64encode(PCM * 20).decode()))
    clock.advance(0.1)
    receive(value, {"type": "interruption", "interruption_event": {"event_id": 150}})
    receive(value, event("agent_response_correction", 37, corrected_agent_response="Late correction"))
    value.close()
    by_id = {row["meta"]["metadata"]["event_id"]: row for _, row in llm_rows(test_spans)}
    assert by_id["37"]["meta"]["metadata"]["interrupted"] is still_playing
    assert by_id["37"]["meta"]["output"]["messages"][0]["content"] == "Late correction"
    assert by_id["117"]["meta"]["metadata"]["interrupted"] is True
    assert len(decoded_frames(by_id["117"]["meta"]["output"]["messages"][0])) < len(PCM * 20)


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_routing_survives_worker_and_tool_scope(monkeypatch, test_spans, outcome):
    instances = []
    expected = {}
    for name in ("tenant-a", "tenant-b", "default"):
        instance = SimpleNamespace(agent_id=name)
        if name == "default":
            patch_module.traced_start(lambda: None, instance, (), {})
            expected[name] = None
        else:
            with LLMObs.routing_context(dd_api_key="test-" + name, dd_site="datadoghq.eu"):
                expected[name] = get_routing_context()
                patch_module.traced_start(lambda: None, instance, (), {})
        instances.append(instance)
    captured = []
    failures = []

    def worker():
        try:
            # A reused worker's ambient route must not override startup routing,
            # including a conversation started without an explicit destination.
            with LLMObs.routing_context(dd_api_key="test-worker-ambient"):
                ambient = get_routing_context()
                for instance in instances:
                    value = instance._dd_elevenlabs_state

                    def run():
                        receive(value, metadata(session=value.agent_id))
                        input_audio(value)
                        receive(value, event("audio", 37, audio_base_64=B64))
                        receive(
                            value, event("client_tool_call", 37, tool_name="nested", tool_call_id="call", parameters={})
                        )
                        captured.extend((value.agent_id, span) for span in (value.turns[37].root, value.turns[37].llm))
                        assert get_routing_context() is ambient

                        async def handler(*args, **kwargs):
                            captured.append((value.agent_id, value.tool_spans["call"]))
                            with LLMObs.llm(name="nested-" + value.agent_id) as nested:
                                captured.append((value.agent_id, nested))
                                assert get_routing_context() == expected[value.agent_id]
                            if outcome == "error":
                                raise ValueError("Synthetic tool failure")
                            if outcome == "cancel":
                                raise asyncio.CancelledError()
                            return "done"

                        invocation = patch_module.traced_tool(
                            handler, None, (), {"tool_name": "nested", "parameters": {"tool_call_id": "call"}}
                        )
                        if outcome == "success":
                            assert asyncio.run(invocation) == "done"
                        else:
                            with pytest.raises(ValueError if outcome == "error" else asyncio.CancelledError):
                                asyncio.run(invocation)
                        assert get_routing_context() is ambient

                    patch_module.traced_run(run, instance, (), {})
                    assert get_routing_context() is ambient
            assert get_routing_context() is None
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert not failures
    assert len(captured) == 12
    for name, span in captured:
        assert span._get_ctx_item(CACHED_LLMOBS_ROUTING_CTX_KEY) == expected[name]
    routed_events = [
        call.args for call in LLMObs._instance._llmobs_span_writer.enqueue.call_args_list if len(call.args) == 2
    ]
    for name in ("tenant-a", "tenant-b"):
        events = [(row, targets) for row, targets in routed_events if row.get("session_id") == name]
        assert {row["name"] for row, _ in events} >= {"elevenlabs response", "user speech", "agent speech"}
        assert all(targets[0].api_key == "test-" + name for _, targets in events)


def test_configured_models_belong_only_to_matching_retained_turns(state, test_spans):
    value, clock = state
    receive(value, event("agent_response", 37, agent_response="First"))
    receive(value, event("context_usage", 37, model="model-a"))
    receive(value, event("agent_response", 117, agent_response="Second"))
    receive(value, event("context_usage", 117, model="model-b"))
    receive(value, event("context_usage", 999, model="unknown-turn"))
    receive(value, event("context_usage", None, model="missing-id"))
    receive(value, event("agent_response", 236, agent_response="Third"))
    receive(value, event("context_usage", 37, model="late-finalized-turn"))
    assert list(value.turns) == [117, 236]
    value.close()
    by_id = {row["meta"]["metadata"]["event_id"]: row for _, row in llm_rows(test_spans)}
    assert by_id["37"]["meta"]["metadata"]["configured_llm"] == "model-a"
    assert by_id["117"]["meta"]["metadata"]["configured_llm"] == "model-b"
    assert "configured_llm" not in by_id["236"]["meta"]["metadata"]


@pytest.mark.parametrize("source", ["handler", "send", "server"])
def test_tool_result_budget_preserves_conversation_content(state, test_spans, source):
    value, clock = state
    input_audio(value)
    receive(value, event("user_transcript", 37, user_transcript="Keep this question"))
    receive(value, event("agent_response", 37, agent_response="Keep this answer"))
    oversized = "document " * _state._MAX_TOOL_RESULT_CHARS
    for index in range(2):
        call_id = str(index)
        receive(value, event("client_tool_call", 37, tool_name="lookup", tool_call_id=call_id, parameters={}))
        if source == "handler":
            value.begin_tool(call_id, "lookup", {})
            value.end_tool(call_id, oversized)
        elif source == "send":
            value.on_send(json.dumps({"type": "client_tool_result", "tool_call_id": call_id, "result": oversized}))
        else:
            receive(
                value, event("agent_tool_response_full_payload", 37, tool_call_id=call_id, full_tool_result=oversized)
            )
    # Replacing a result reclaims its share without increasing the aggregate cap.
    value.on_send(json.dumps({"type": "client_tool_result", "tool_call_id": "0", "result": oversized}))
    clock.advance()
    value.close()
    captured = rows(test_spans)
    response = next(row for _, row in captured if row["meta"]["span"]["kind"] == "llm")
    incoming = response["meta"]["input"]["messages"][0]
    assert incoming["content"] == "Keep this question"
    assert decoded_frames(incoming) == PCM
    assert response["meta"]["output"]["messages"][0]["content"] == "Keep this answer"
    assert sum(len(item["result"]) for item in incoming["tool_results"]) <= _state._MAX_TOOL_RESULT_CHARS
    assert response["meta"]["metadata"]["tool_results_truncated"] is True
    for _, row in captured:
        assert len(json.dumps(row)) < config._llmobs_event_size_limit
        if row["meta"]["span"]["kind"] == "tool":
            assert len(row["meta"]["output"]["value"]) <= _state._MAX_TOOL_RESULT_CHARS


@pytest.mark.parametrize("ending", ["transport_error", "final_response", "completion"])
def test_streamed_response_duration_uses_last_accepted_observation(state, test_spans, ending):
    value, clock = state
    receive(value, event("agent_chat_response_part", 37, response_id="response", type="start", text="Hello"))
    clock.advance(2)
    receive(value, event("agent_chat_response_part", 37, response_id="response", type="delta", text=" world"))
    expected_duration = 2_000_000_000
    if ending != "transport_error":
        clock.advance(3)
        if ending == "final_response":
            receive(value, event("agent_response", 37, response_id="response", agent_response="Hello world"))
        else:
            receive(value, event("agent_chat_response_part", 37, response_id="response", type="stop", text=""))
            receive(value, event("agent_response_complete", 37))
        expected_duration = 5_000_000_000
        clock.advance(3)
        receive(value, event("agent_chat_response_part", 37, response_id="response", type="delta", text=" ignored"))
    else:
        clock.advance(3)

        class FailingSocket:
            def recv(self):
                raise RuntimeError("Stream transport failure")

        with pytest.raises(RuntimeError, match="Stream transport failure"):
            patch_module._Socket(FailingSocket(), value).recv()
    value.close()
    span, data = llm_rows(test_spans)[0]
    assert span.duration_ns == expected_duration
    assert data["meta"]["output"]["messages"][0]["content"] == "Hello world"
    assert data["status"] == ("error" if ending == "transport_error" else "ok")
