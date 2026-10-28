import asyncio
import base64
import json

from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai import conversation
import pytest
import websockets

from ddtrace.contrib.internal.elevenlabs.patch import patch
from ddtrace.contrib.internal.elevenlabs.patch import unpatch
from ddtrace.llmobs import LLMObs


PCM = b"\x01\x00" * 160
EVENTS = [
    {
        "type": "conversation_initiation_metadata",
        "conversation_initiation_metadata_event": {
            "conversation_id": "apm-only-session",
            "user_input_audio_format": "pcm_16000",
            "agent_output_audio_format": "pcm_16000",
        },
    },
    {"type": "user_transcript", "user_transcription_event": {"event_id": 1, "user_transcript": "Question"}},
    {"type": "audio", "audio_event": {"event_id": 1, "audio_base_64": base64.b64encode(PCM).decode()}},
    {"type": "agent_response", "agent_response_event": {"event_id": 1, "agent_response": "Answer"}},
]


@pytest.fixture(autouse=True)
def elevenlabs_llmobs(monkeypatch):
    # Override the directory fixture to exercise ordinary APM without LLMObs.
    LLMObs.disable()
    patch()
    yield
    unpatch()


class Audio:
    def __init__(self):
        self.output_audio = []

    def start(self, input_callback):
        self.input_callback = input_callback

    def output(self, audio):
        self.output_audio.append(audio)

    def interrupt(self):
        pass

    def stop(self):
        pass


class AsyncAudio(Audio):
    async def start(self, input_callback):
        self.input_callback = input_callback

    async def output(self, audio):
        self.output_audio.append(audio)

    async def interrupt(self):
        pass

    async def stop(self):
        pass


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_public_session_preserves_apm_without_llmobs(monkeypatch, tracer, test_spans, asynchronous, failure):
    responses = []
    transcripts = []
    sent = []
    events = iter(EVENTS)
    audio = AsyncAudio() if asynchronous else Audio()
    instance = None

    def next_event():
        try:
            return next(events)
        except StopIteration:
            if failure:
                raise RuntimeError("Synthetic transport failure")
            return None

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def send(self, message):
            sent.append(json.loads(message))

        def recv(self, **kwargs):
            message = next_event()
            if message is None:
                instance.end_session()
                return json.dumps({"type": "end"})
            if message["type"] == "user_transcript":
                audio.input_callback(PCM)
            return json.dumps(message)

    class AsyncSocket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def send(self, message):
            sent.append(json.loads(message))

        async def recv(self, **kwargs):
            message = next_event()
            if message is None:
                await instance.end_session()
                return json.dumps({"type": "end"})
            if message["type"] == "user_transcript":
                await audio.input_callback(PCM)
            return json.dumps(message)

    async def on_response(text):
        responses.append(text)

    async def on_transcript(text):
        transcripts.append(text)

    # Fake only transport. All tracing is installed through the ordinary patch API.
    unpatch()
    monkeypatch.setattr(conversation, "connect", lambda *args, **kwargs: Socket())
    monkeypatch.setattr(websockets, "connect", lambda *args, **kwargs: AsyncSocket())
    patch()
    cls = conversation.AsyncConversation if asynchronous else conversation.Conversation
    instance = cls(
        ElevenLabs(api_key="test-key"),
        "test-agent",
        requires_auth=False,
        audio_interface=audio,
        callback_agent_response=on_response if asynchronous else responses.append,
        callback_user_transcript=on_transcript if asynchronous else transcripts.append,
    )

    async def run_async():
        try:
            await instance.start_session()
            return await instance.wait_for_session_end()
        finally:
            await instance.end_session()

    with tracer.trace("application") as parent:
        if asynchronous:
            session_id = asyncio.run(run_async())
        else:
            try:
                instance.start_session()
                session_id = instance.wait_for_session_end()
            finally:
                instance.end_session()
        assert tracer.current_span() is parent
    assert session_id == "apm-only-session"
    assert responses == ["Answer"]
    assert transcripts == ["Question"]
    assert audio.output_audio == [PCM]
    assert any("user_audio_chunk" in message for message in sent)
    spans = [span for trace in test_spans.pop_traces() for span in trace]
    assert spans == [parent]
    assert not parent.error
    assert not LLMObs.enabled
