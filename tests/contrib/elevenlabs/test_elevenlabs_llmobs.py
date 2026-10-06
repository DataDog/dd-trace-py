import asyncio
import base64
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
from ddtrace.contrib.internal.elevenlabs import _state
from ddtrace.contrib.internal.elevenlabs import patch as patch_module
from ddtrace.contrib.internal.elevenlabs._state import ConversationState
from ddtrace.contrib.trace_utils import wrap
from ddtrace.llmobs._constants import CACHED_LLMOBS_EVENT_CTX_KEY
from ddtrace.llmobs._integrations.elevenlabs import ElevenLabsIntegration
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
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
        parent = patch_module.elevenlabs._datadog_integration.trace("application", submit_to_llmobs=True, activate=True)
        patch_module.elevenlabs._datadog_integration.annotate_voice_span(
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
    token = _state._CURRENT_STATE.set(value)
    try:
        tools.execute_tool("weather", {"city": "Test", "tool_call_id": "call-1"}, callback)
        assert done.wait(3)
    finally:
        _state._CURRENT_STATE.reset(token)
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
            self.instance._should_stop.set()
            return json.dumps({"type": "end"})
        if value["type"] == "user_transcript":
            self.instance.audio_interface.input_callback(PCM)
        return json.dumps(value)


@pytest.mark.parametrize("default_interface", [False, True])
def test_ordinary_sync_sdk_with_normal_event_subset(monkeypatch, test_spans, default_interface):
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
        event("user_transcript", 37, user_transcript="Question"),
        event("audio", 37, audio_base_64=B64),
        event("agent_response", 37, agent_response="Answer"),
    ]
    raw = Socket(instance, events, clock)
    monkeypatch.setattr(conversation, "connect", lambda *args, **kwargs: raw)
    wrap("elevenlabs.conversational_ai.conversation", "connect", patch_module.traced_connect)
    try:
        instance.start_session()
        instance.wait_for_session_end()
    finally:
        instance.end_session()
    assert instance.audio_interface is audio
    _, data = llm_rows(test_spans)[0]
    assert decoded_frames(data["meta"]["input"]["messages"][0]) == PCM
    assert decoded_frames(data["meta"]["output"]["messages"][0]) == PCM


@pytest.mark.asyncio
@pytest.mark.parametrize("default_interface", [False, True])
async def test_ordinary_async_sdk_with_custom_interface(monkeypatch, test_spans, default_interface):
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
                instance._should_stop.set()
                return json.dumps({"type": "end"})
            if value["type"] == "user_transcript":
                await instance.audio_interface.input_callback(PCM)
            return json.dumps(value)

    class AsyncConnect:
        async def __aenter__(self):
            return AsyncSocket()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(patch_module._original_websockets, "connect", lambda *args, **kw: AsyncConnect())
    try:
        await instance.start_session()
        await instance.wait_for_session_end()
    finally:
        await instance.end_session()
    _, data = llm_rows(test_spans)[0]
    assert decoded_frames(data["meta"]["input"]["messages"][0]) == PCM
    assert decoded_frames(data["meta"]["output"]["messages"][0]) == PCM


@pytest.mark.parametrize("normal_events_only", [False, True])
def test_sanitized_capture_replay(state, test_spans, normal_events_only):
    value, clock = state
    fixture = json.loads((Path(__file__).parent / "data" / "conversation.json").read_text())
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
    monkeypatch.setattr(conversation, "connect", lambda *args, **kwargs: sockets[_state._CURRENT_STATE.get().agent_id])
    wrap("elevenlabs.conversational_ai.conversation", "connect", patch_module.traced_connect)
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

    token = _state._CURRENT_STATE.set(value)
    try:
        result = await patch_module.traced_tool(handler, None, ("weather", {"tool_call_id": "call-1"}), {})
        assert result == "Tool result"
    finally:
        _state._CURRENT_STATE.reset(token)
