import asyncio
import base64
import gzip
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import Mock
import wave

from aws_sdk_bedrock_runtime.models import BidirectionalInputPayloadPart
from aws_sdk_bedrock_runtime.models import InvokeModelWithBidirectionalStreamInputChunk
import pytest
from smithy_core.aio.eventstream import DuplexEventStream

from ddtrace import config
from ddtrace.contrib.internal.aws_sdk_bedrock_runtime._stream import DuplexProxy
from ddtrace.contrib.internal.aws_sdk_bedrock_runtime.patch import patch
from ddtrace.contrib.internal.aws_sdk_bedrock_runtime.patch import unpatch
from ddtrace.llmobs._integrations import _aws_sdk_bedrock_runtime as _sonic
from ddtrace.llmobs._integrations._aws_sdk_bedrock_runtime import InputAudio
from ddtrace.llmobs._integrations._aws_sdk_bedrock_runtime import SonicState
from ddtrace.llmobs._integrations._aws_sdk_bedrock_runtime import Turn
from ddtrace.llmobs._integrations.aws_sdk_bedrock_runtime import AwsSdkBedrockRuntimeIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.trace import Context
from ddtrace.trace import Span
from ddtrace.trace import tracer


MODEL = "amazon.nova-2-sonic-v1:0"
INPUT = {
    "mediaType": "audio/lpcm",
    "sampleSizeBits": 16,
    "channelCount": 1,
    "sampleRateHertz": 16000,
    "encoding": "base64",
}
OUTPUT = dict(INPUT, sampleRateHertz=24000)


class RecordingIntegration(AwsSdkBedrockRuntimeIntegration):
    def __init__(self):
        super().__init__(config.aws_sdk_bedrock_runtime)
        self.spans = []

    def trace(self, operation_id, **kwargs):
        parent = kwargs.get("parent_context")
        span = Span(
            operation_id, trace_id=getattr(parent, "trace_id", None), parent_id=getattr(parent, "span_id", None)
        )
        # Real LLMObs initializes identity on span start; this recorder bypasses it.
        _annotate_llmobs_span_data(span, trace_id=f"{span.trace_id:032x}")
        self.spans.append(span)
        return span


def state():
    integration = RecordingIntegration()
    return SonicState(integration, MODEL, tracer.context_provider.active() or Context()), integration


def event(name, data):
    return InvokeModelWithBidirectionalStreamInputChunk(
        value=BidirectionalInputPayloadPart(bytes_=json.dumps({"event": {name: data}}).encode())
    )


def data(span):
    return _get_llmobs_data_metastruct(span)


def llms(integration):
    return [span for span in integration.spans if span.name == "nova sonic response"]


def audio(message):
    part = message["audio_parts"][0]
    assert part["mime_type"] == "audio/wav"
    with wave.open(io.BytesIO(base64.b64decode(part["content"])), "rb") as wav:
        return wav.getframerate(), wav.readframes(wav.getnframes())


@pytest.mark.parametrize("fixture,expected_turns", [("voice-session-1", 5), ("voice-session-2", 6)])
def test_capture_replay(monkeypatch, fixture, expected_turns):
    with gzip.open(Path(__file__).parent / "fixtures" / (fixture + ".json.gz"), "rt") as source:
        capture = json.load(source)
    sonic, integration = state()
    now = [1_800_000_000_000_000_000]
    monkeypatch.setattr(_sonic.time, "time_ns", lambda: now[0])
    for record in capture["events"]:
        now[0] = 1_800_000_000_000_000_000 + record["at_ns"]
        payload = record["event"]
        for name, value in payload.items():
            value = dict(value)
            if "audio_bytes" in record:
                value["content"] = base64.b64encode(bytes(record["audio_bytes"])).decode()
            sonic.observe(event(name, value), outbound=record["direction"] == "outbound")
    sonic.finish()
    responses = llms(integration)
    assert len(responses) == expected_turns
    assert len({data(span)["session_id"] for span in responses}) == 1
    assert len({data(span)["meta"]["metadata"]["completion_id"] for span in responses}) == 1
    assert all(span.finished for span in integration.spans)
    assert sum(data(span)["metrics"]["input_tokens"] for span in responses) == capture["input_tokens"]
    assert sum(data(span)["metrics"]["output_tokens"] for span in responses) == capture["output_tokens"]
    roots = [span for span in integration.spans if span.name == "nova sonic audio turn"]
    assert len({root.span_id for root in roots}) == expected_turns
    assert all(root.parent_id is None for root in roots)
    for response, root in zip(responses, roots):
        tagged = data(response)
        assert tagged["parent_id"] == str(root.span_id)
        assert response.trace_id == root.trace_id
        assert tagged["meta"]["model_name"] == MODEL
        assert tagged["meta"]["model_provider"] == "amazon"
        assert "interrupted" not in tagged["meta"]["output"]["messages"][0]["content"]
        user = tagged["meta"]["input"]["messages"][-1]
        rate, raw = audio(user)
        windows = tagged["meta"]["metadata"]["speech_windows"]
        assert rate == 16000
        assert len(raw) == (windows[-1]["end_ms"] - windows[0]["start_ms"]) * 32
        assistant = tagged["meta"]["output"]["messages"][0]
        rate, raw = audio(assistant)
        assert rate == 24000 and raw
        phases = [span for span in integration.spans if span.parent_id == root.span_id and span is not response]
        assert [phase.name for phase in phases] == ["user speech", "agent speech"]
        assert phases[1].start_ns >= phases[0].start_ns + phases[0].duration_ns
        assert abs(phases[1].duration_ns - len(raw) * 1e9 / 48000) < 1000
    if fixture == "voice-session-2":
        assert len(data(responses[0])["meta"]["metadata"]["speech_windows"]) == 3
        assert sum(data(span)["meta"]["metadata"]["interrupted"] for span in responses) == 4


def test_offsets_continue_across_input_content_and_reset_per_connection():
    sonic, _ = state()
    for key in ("a", "b"):
        sonic.sent(
            "contentStart", {"contentName": key, "role": "USER", "type": "AUDIO", "audioInputConfiguration": INPUT}, 0
        )
        sonic.sent("audioInput", {"contentName": key, "content": base64.b64encode(b"\x01\x02" * 16000).decode()}, 1)
        sonic.sent("contentEnd", {"contentName": key}, 2)
    assert sonic.audio.total == 64000
    assert sonic.audio.clip(1100, 1200) == b"\x01\x02" * 1600
    other, _ = state()
    assert other.audio.total == 0
    assert other.audio.clip(1100, 1200) == b""


def test_ring_buffer_cannot_emit_truncated_clip(monkeypatch):
    monkeypatch.setattr(_sonic, "MAX_AUDIO", 3200)
    audio_buffer = InputAudio()
    audio_buffer.configure(INPUT)
    audio_buffer.append(base64.b64encode(bytes(32000)), 1_000_000_000)
    assert len(audio_buffer.data) == 3200
    assert audio_buffer.total == 32000
    assert audio_buffer.clip(0, 100) == b""
    assert len(audio_buffer.clip(950, 1000)) == 1600


def test_output_queue_gap_and_interruption():
    turn = Turn()
    chunk = base64.b64encode(bytes(48000)).decode()
    turn.append_audio(chunk, OUTPUT, 1_000_000_000)
    turn.append_audio(chunk, OUTPUT, 1_100_000_000)
    assert turn.output_end_ns == 3_000_000_000
    turn.append_audio(chunk, OUTPUT, 4_000_000_000)
    assert turn.output_end_ns == 5_000_000_000
    assert len(turn.output_pcm) == 192000  # includes the one second underrun
    turn.interrupt(4_500_000_000)
    turn.interrupt(4_900_000_000)
    turn.append_audio(chunk, OUTPUT, 4_950_000_000)
    assert len(turn.output_pcm) == 168000
    assert turn.output_end_ns == 4_500_000_000


@pytest.mark.parametrize("changes", [{"sampleSizeBits": 8}, {"channelCount": 2}, {"mediaType": "audio/mpeg"}])
def test_unknown_formats_omit_audio(changes):
    turn = Turn()
    turn.append_audio(base64.b64encode(bytes(48)).decode(), dict(OUTPUT, **changes), 1)
    assert not turn.output_valid
    sonic, _ = state()
    _, output = sonic._messages(turn)
    assert "audio_parts" not in output[0]


def test_whole_span_audio_budget(monkeypatch):
    monkeypatch.setattr(_sonic, "LLMOBS_AUDIO_INLINE_MAX_BYTES", 600)
    turn = Turn()
    turn.input_rate = 16000
    turn.input_pcm = bytes(100)
    turn.output_rate = 24000
    turn.output_pcm.extend(bytes(100))
    sonic, _ = state()
    inputs, outputs = sonic._messages(turn)
    assert "audio_parts" in inputs[0]
    assert "audio_parts" not in outputs[0]


def test_shutdown_partial_error_and_idempotence():
    sonic, integration = state()
    sonic.pending.user_text = "hello"
    try:
        raise RuntimeError("provider failed")
    except RuntimeError:
        sonic.finish_error()
    sonic.finish()
    assert len(llms(integration)) == 1
    assert all(span.finished and span.error for span in integration.spans)
    assert data(llms(integration)[0])["meta"]["metadata"]["partial"]


def test_multiple_prompts_omit_unverified_second_offset_origin():
    sonic, _ = state()
    sonic.sent("promptStart", {"promptName": "one"}, 1)
    sonic.sent("promptStart", {"promptName": "two"}, 2)
    assert sonic.closed


@pytest.mark.asyncio
async def test_real_smithy_duplex_iteration_and_half_close():
    sonic, integration = state()
    sender = SimpleNamespace(send=AsyncMock(), close=AsyncMock())
    item = event("userSpeechStart", {"inputAudioOffsetMs": 0})
    receiver = SimpleNamespace(receive=AsyncMock(side_effect=[item, None]), close=AsyncMock())
    future = asyncio.get_running_loop().create_future()
    future.set_result(("response", receiver))
    stream = DuplexProxy(DuplexEventStream(input_stream=sender, output_future=future), sonic)
    assert stream.output_stream is None
    _, output = await stream.await_output()
    assert (await stream.await_output())[1] is output
    assert stream.output_stream is output
    await stream.input_stream.close()
    assert not sonic.closed
    assert [item async for item in output] == [item]
    assert sonic.closed
    assert len(llms(integration)) == 1
    await stream.close()
    assert len(llms(integration)) == 1


@pytest.mark.asyncio
async def test_failed_send_does_not_advance_samples():
    sonic, _ = state()
    sender = SimpleNamespace(send=AsyncMock(side_effect=ValueError("failed")), close=AsyncMock())
    stream = DuplexProxy(SimpleNamespace(input_stream=sender, output_stream=None), sonic)
    sonic.audio.configure(INPUT)
    with pytest.raises(ValueError):
        await stream.input_stream.send(event("audioInput", {"content": base64.b64encode(bytes(320)).decode()}))
    assert sonic.audio.total == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("provider"), asyncio.CancelledError()])
async def test_receiver_exception_propagates_and_finalizes(error):
    sonic, integration = state()
    sonic.pending.user_text = "pending"
    receiver = SimpleNamespace(receive=AsyncMock(side_effect=error))
    stream = DuplexProxy(SimpleNamespace(input_stream=Mock(), output_stream=receiver), sonic)
    with pytest.raises(type(error)):
        await stream.output_stream.receive()
    assert sonic.closed
    assert len(llms(integration)) == 1


@pytest.mark.asyncio
async def test_telemetry_failure_does_not_replace_received_event(monkeypatch):
    sonic, _ = state()
    item = event("userSpeechStart", {"inputAudioOffsetMs": 0})
    monkeypatch.setattr(sonic, "received", Mock(side_effect=ValueError("telemetry")))
    receiver = SimpleNamespace(receive=AsyncMock(return_value=item))
    stream = DuplexProxy(SimpleNamespace(input_stream=Mock(), output_stream=receiver), sonic)
    assert await stream.output_stream.receive() is item


def test_patch_unpatch_is_idempotent():
    from aws_sdk_bedrock_runtime.client import AsyncBedrockRuntimeClient

    unpatch()
    original = AsyncBedrockRuntimeClient.invoke_model_with_bidirectional_stream
    try:
        patch()
        wrapped = AsyncBedrockRuntimeClient.__dict__["invoke_model_with_bidirectional_stream"]
        patch()
        assert AsyncBedrockRuntimeClient.__dict__["invoke_model_with_bidirectional_stream"] is wrapped
    finally:
        unpatch()
    assert AsyncBedrockRuntimeClient.invoke_model_with_bidirectional_stream is original


def test_final_text_stays_with_previous_response_after_new_speech():
    sonic, integration = state()
    sonic.pending.user_text = "first question"
    sonic.received(
        "contentStart",
        {
            "contentId": "s1",
            "role": "ASSISTANT",
            "type": "TEXT",
            "additionalModelFields": '{"generationStage":"SPECULATIVE"}',
        },
        10,
    )
    sonic.received("textOutput", {"contentId": "s1", "content": "repeat"}, 11)
    sonic.received("textOutput", {"contentId": "s1", "content": "repeat"}, 12)
    sonic.received("contentEnd", {"contentId": "s1"}, 13)
    sonic.received("userSpeechStart", {"inputAudioOffsetMs": 0}, 20)
    sonic.received(
        "contentStart",
        {
            "contentId": "final",
            "role": "ASSISTANT",
            "type": "TEXT",
            "additionalModelFields": '{"generationStage":"FINAL"}',
        },
        21,
    )
    sonic.received("textOutput", {"contentId": "final", "content": "repeat"}, 22)
    sonic.received("textOutput", {"contentId": "final", "content": "repeat"}, 23)
    sonic.received("contentEnd", {"contentId": "final", "stopReason": "END_TURN"}, 24)
    assert not integration.spans  # END_TURN on a final text block does not close the response
    sonic.received(
        "contentStart",
        {
            "contentId": "s2",
            "role": "ASSISTANT",
            "type": "TEXT",
            "additionalModelFields": '{"generationStage":"SPECULATIVE"}',
        },
        30,
    )
    first = data(llms(integration)[0])["meta"]["output"]["messages"][0]
    assert first["content"] == "repeatrepeat"


def test_tool_use_and_result_keep_the_response_and_ids():
    sonic, integration = state()
    sonic.pending.user_text = "weather"
    sonic.received("contentStart", {"contentId": "tool", "role": "ASSISTANT", "type": "TOOL"}, 1)
    sonic.received(
        "toolUse",
        {"contentId": "tool", "toolName": "weather", "toolUseId": "call-1", "content": '{"city":"Boston"}'},
        2,
    )
    sonic.received("contentEnd", {"contentId": "tool", "stopReason": "TOOL_USE"}, 3)
    sonic.sent(
        "contentStart",
        {
            "contentName": "result",
            "type": "TOOL",
            "role": "TOOL",
            "toolResultInputConfiguration": {"toolUseId": "call-1"},
        },
        4,
    )
    sonic.sent("toolResult", {"contentName": "result", "content": "sunny"}, 5)
    sonic.sent("contentEnd", {"contentName": "result"}, 6)
    sonic.received(
        "contentStart",
        {
            "contentId": "answer",
            "role": "ASSISTANT",
            "type": "TEXT",
            "additionalModelFields": '{"generationStage":"FINAL"}',
        },
        7,
    )
    sonic.received("textOutput", {"contentId": "answer", "content": "It is sunny."}, 8)
    sonic.received("contentEnd", {"contentId": "answer", "stopReason": "END_TURN"}, 9)
    sonic.finish()
    assert len(llms(integration)) == 1
    meta = data(llms(integration)[0])["meta"]
    assert meta["output"]["messages"][0]["tool_calls"][0]["tool_id"] == "call-1"
    assert meta["input"]["messages"][-1]["tool_results"][0]["tool_id"] == "call-1"


def test_user_json_is_not_an_interruption():
    sonic, integration = state()
    sonic.received("contentStart", {"contentId": "user", "role": "USER", "type": "TEXT"}, 1)
    sonic.received("textOutput", {"contentId": "user", "content": '{"interrupted": true}'}, 2)
    sonic.received("contentEnd", {"contentId": "user"}, 3)
    sonic.finish()
    assert data(llms(integration)[0])["meta"]["input"]["messages"][0]["content"] == '{"interrupted": true}'


def test_oversize_output_never_emits_a_prefix(monkeypatch):
    monkeypatch.setattr(_sonic, "MAX_AUDIO", 200)
    turn = Turn()
    chunk = base64.b64encode(bytes(150)).decode()
    turn.append_audio(chunk, OUTPUT, 1)
    turn.append_audio(chunk, OUTPUT, 2)
    assert turn.output_bytes == 300
    assert not turn.output_valid
    assert not turn.output_pcm


def test_emitter_failure_is_once_and_does_not_block_next_turn(monkeypatch):
    sonic, integration = state()
    sonic.pending.user_text = "first"
    sonic._start_response({}, 1)
    monkeypatch.setattr(integration, "start_span", Mock(side_effect=RuntimeError("telemetry")))
    sonic.pending.user_text = "second"
    sonic._start_response({}, 2)
    assert sonic.current.user_text == "second"
    assert sonic.pending.user_text == ""
    assert integration.start_span.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("half", ["input", "output", "duplex"])
async def test_close_failure_is_preserved_and_marks_spans(half):
    sonic, integration = state()
    sonic.pending.user_text = "pending"
    error = RuntimeError("close failed")
    sender = SimpleNamespace(close=AsyncMock(side_effect=error))
    receiver = SimpleNamespace(close=AsyncMock(side_effect=error))
    underlying = SimpleNamespace(input_stream=sender, output_stream=receiver, close=AsyncMock(side_effect=error))
    stream = DuplexProxy(underlying, sonic)
    target = {"input": stream.input_stream, "output": stream.output_stream, "duplex": stream}[half]
    with pytest.raises(RuntimeError) as caught:
        await target.close()
    assert caught.value is error
    assert sonic.closed
    assert all(span.error for span in integration.spans)


def test_malformed_audio_cannot_shift_subsequent_clips():
    sonic, _ = state()
    sonic.audio.configure(INPUT)
    with pytest.raises(ValueError):
        sonic.audio.append("malformed!", 1)
    sonic.audio.append(base64.b64encode(bytes(3200)).decode(), 2)
    assert sonic.audio.clip(0, 100) == b""
    turn = Turn()
    turn.append_audio(base64.b64encode(bytes(4800)).decode(), OUTPUT, 1)
    with pytest.raises(ValueError):
        turn.append_audio("malformed!", OUTPUT, 2)
    assert not turn.output_valid and not turn.output_pcm


def test_missing_speech_boundaries_fall_back_to_transcripts_per_turn():
    sonic, integration = state()
    for index in range(2):
        user, output = f"user-{index}", f"audio-{index}"
        sonic.received("contentStart", {"contentId": user, "role": "USER", "type": "TEXT"}, index * 10)
        sonic.received("textOutput", {"contentId": user, "content": f"question {index}"}, index * 10 + 1)
        sonic.received("contentEnd", {"contentId": user}, index * 10 + 2)
        sonic.received(
            "contentStart",
            {"contentId": output, "role": "ASSISTANT", "type": "AUDIO", "audioOutputConfiguration": OUTPUT},
            index * 10 + 3,
        )
        sonic.received(
            "audioOutput", {"contentId": output, "content": base64.b64encode(bytes(48)).decode()}, index * 10 + 4
        )
        sonic.received("contentEnd", {"contentId": output, "stopReason": "END_TURN"}, index * 10 + 5)
    sonic.finish()
    assert len(llms(integration)) == 2
    assert not any(span.name == "user speech" for span in integration.spans)
    assert [data(span)["meta"]["input"]["messages"][0]["content"] for span in llms(integration)] == [
        "question 0",
        "question 1",
    ]


def test_audio_budget_reserves_serialized_unicode_and_tool_results(monkeypatch):
    monkeypatch.setattr(_sonic, "LLMOBS_AUDIO_INLINE_MAX_BYTES", 1000)
    sonic, _ = state()
    turn = Turn()
    turn.user_text = "\U0001f600" * 50
    turn.tool_results = [{"role": "tool", "tool_results": [{"result": "\U0001f600" * 50, "tool_id": "one"}]}]
    turn.input_pcm, turn.input_rate = bytes(200), 16000
    turn.output_pcm.extend(bytes(200))
    turn.output_rate = 24000
    inputs, outputs = sonic._messages(turn)
    assert all("audio_parts" not in message for message in inputs + outputs)
