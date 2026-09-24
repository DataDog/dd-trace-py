import asyncio
import base64
from contextlib import ExitStack
import gzip
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import aws_sdk_bedrock_runtime
from aws_sdk_bedrock_runtime.client import AsyncBedrockRuntimeClient
from aws_sdk_bedrock_runtime.models import InvokeModelWithBidirectionalStreamOperationInput
import pytest
from smithy_core.aio.eventstream import DuplexEventStream

from ddtrace import config
from ddtrace import patch as patch_integrations
from ddtrace.contrib.internal.aws_sdk_bedrock_runtime._stream import DuplexProxy
from ddtrace.contrib.internal.aws_sdk_bedrock_runtime._stream import InputProxy
from ddtrace.contrib.internal.aws_sdk_bedrock_runtime.patch import unpatch
from ddtrace.internal import core
from ddtrace.internal.settings.standalone import standalone_config
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._integrations import _aws_sdk_bedrock_runtime as sonic_module
from ddtrace.llmobs._integrations._aws_sdk_bedrock_runtime import SonicState
from ddtrace.llmobs._integrations.aws_sdk_bedrock_runtime import AwsSdkBedrockRuntimeIntegration
from ddtrace.trace import Context
from tests.contrib.aws_sdk_bedrock_runtime.test_sonic import INPUT
from tests.contrib.aws_sdk_bedrock_runtime.test_sonic import MODEL
from tests.contrib.aws_sdk_bedrock_runtime.test_sonic import OUTPUT
from tests.contrib.aws_sdk_bedrock_runtime.test_sonic import audio
from tests.contrib.aws_sdk_bedrock_runtime.test_sonic import event
from tests.utils import override_global_config


@pytest.fixture
def llmobs(monkeypatch, tracer):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(standalone_config, "apm_tracing_enabled", False)
    LLMObs.disable()
    with override_global_config({"_llmobs_ml_app": "sonic-test", "_dd_api_key": "not-a-real-key"}):
        LLMObs.enable(integrations_enabled=False, _tracer=tracer, agentless_enabled=False)
        LLMObs._instance._llmobs_span_writer.stop()
        monkeypatch.setattr(LLMObs._instance._llmobs_span_writer, "enqueue", MagicMock())
        patch_integrations(aws_sdk_bedrock_runtime=True)
        try:
            yield LLMObs._instance._llmobs_span_writer
        finally:
            unpatch()
            LLMObs.disable()


async def mock_stream(monkeypatch, events):
    publisher = SimpleNamespace(send=AsyncMock(), close=AsyncMock())
    receiver = SimpleNamespace(receive=AsyncMock(side_effect=events + [None]), close=AsyncMock())
    future = asyncio.get_running_loop().create_future()
    future.set_result((None, receiver))
    stream = DuplexEventStream(input_stream=publisher, output_future=future)
    monkeypatch.setattr(aws_sdk_bedrock_runtime.client.RequestPipeline, "duplex_stream", AsyncMock(return_value=stream))
    return stream


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_kind", ["none", "apm", "workflow", "workflow_apm"])
async def test_patched_sdk_emits_real_llmobs_tree(monkeypatch, llmobs, tracer, parent_kind):
    events = [
        event("userSpeechStart", {"inputAudioOffsetMs": 100, "sessionId": "provider-session"}),
        event("userSpeechEnd", {"inputAudioOffsetMs": 500, "inputAudioDetectionOffsetMs": 750}),
        event(
            "contentStart",
            {
                "contentId": "user",
                "role": "USER",
                "type": "TEXT",
                "additionalModelFields": json.dumps({"generationStage": "FINAL"}),
            },
        ),
        event("textOutput", {"contentId": "user", "content": "hello"}),
        event("contentEnd", {"contentId": "user"}),
        event(
            "contentStart",
            {
                "contentId": "output",
                "role": "ASSISTANT",
                "type": "AUDIO",
                "audioOutputConfiguration": OUTPUT,
                "completionId": "shared",
            },
        ),
        event("audioOutput", {"contentId": "output", "content": base64.b64encode(bytes(4800)).decode()}),
        event("contentEnd", {"contentId": "output", "stopReason": "END_TURN"}),
        event(
            "contentStart",
            {
                "contentId": "text",
                "role": "ASSISTANT",
                "type": "TEXT",
                "additionalModelFields": json.dumps({"generationStage": "FINAL"}),
            },
        ),
        event("textOutput", {"contentId": "text", "content": "hi"}),
        event("contentEnd", {"contentId": "text", "stopReason": "END_TURN"}),
    ]
    await mock_stream(monkeypatch, events)
    with ExitStack() as stack:
        outer = stack.enter_context(LLMObs.workflow(name="application")) if "workflow" in parent_kind else None
        apm = stack.enter_context(tracer.trace("request")) if "apm" in parent_kind else None
        async with AsyncBedrockRuntimeClient() as client:
            stream = await client.invoke_model_with_bidirectional_stream(
                InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL)
            )
            assert isinstance(stream, DuplexProxy)
            # Receiver callbacks may run without the caller's active LLMObs context.
            provider = LLMObs._instance._llmobs_context_provider
            saved_context = provider.active()
            provider.activate(None)
            stack.callback(provider.activate, saved_context)
            await stream.input_stream.send(
                event(
                    "contentStart",
                    {
                        "contentName": "mic",
                        "role": "USER",
                        "type": "AUDIO",
                        "audioInputConfiguration": INPUT,
                    },
                )
            )
            await stream.input_stream.send(
                event(
                    "audioInput",
                    {
                        "contentName": "mic",
                        "content": base64.b64encode(bytes(32000)).decode(),
                    },
                )
            )
            await stream.input_stream.close()
            _, receiver = await stream.await_output()
            assert [received async for received in receiver] == events
            await stream.close()
    emitted = [call.args[0] for call in llmobs.enqueue.call_args_list]
    assert len(emitted) == (5 if outer is not None else 4)
    by_name = {span["name"]: span for span in emitted}
    root = by_name["nova sonic audio turn"]
    assert root["parent_id"] == (str(outer.span_id) if outer is not None else "undefined")
    if outer is not None:
        assert root["trace_id"] == by_name["application"]["trace_id"]
    if apm is not None:
        assert root["trace_id"] != f"{apm.trace_id:032x}"
        assert root["_dd"]["apm_trace_id"] == f"{apm.trace_id:032x}"

    assert root["session_id"] == "provider-session"
    for name in ("user speech", "nova sonic response", "agent speech"):
        child = by_name[name]
        assert child["parent_id"] == root["span_id"]
        assert child["trace_id"] == root["trace_id"]
        assert child["session_id"] == root["session_id"]
    response = by_name["nova sonic response"]
    assert response["meta"]["input"]["messages"][-1]["content"] == "hello"
    assert response["meta"]["output"]["messages"][0]["content"] == "hi"
    assert response["meta"]["input"]["messages"][-1]["audio_parts"][0]["mime_type"] == "audio/wav"
    assert response["meta"]["output"]["messages"][0]["audio_parts"][0]["mime_type"] == "audio/wav"


@pytest.mark.asyncio
@pytest.mark.parametrize("model,enabled", [(MODEL, False), ("amazon.nova-sonic-v1:0", True)])
async def test_unrelated_calls_remain_unwrapped(monkeypatch, llmobs, model, enabled):
    if not enabled:
        LLMObs.disable()
    underlying = await mock_stream(monkeypatch, [])
    async with AsyncBedrockRuntimeClient() as client:
        result = await client.invoke_model_with_bidirectional_stream(
            InvokeModelWithBidirectionalStreamOperationInput(model_id=model)
        )
    assert result is underlying
    assert not llmobs.enqueue.called


@pytest.mark.asyncio
async def test_invoke_failure_emits_error_without_replacing_exception(monkeypatch, llmobs):
    original = RuntimeError("connection failed")
    monkeypatch.setattr(
        aws_sdk_bedrock_runtime.client.RequestPipeline, "duplex_stream", AsyncMock(side_effect=original)
    )
    async with AsyncBedrockRuntimeClient() as client:
        with pytest.raises(RuntimeError) as caught:
            await client.invoke_model_with_bidirectional_stream(
                InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL)
            )
    assert caught.value is original
    emitted = [call.args[0] for call in llmobs.enqueue.call_args_list]
    assert len(emitted) == 2
    assert all(span["status"] == "error" for span in emitted)


@pytest.mark.asyncio
async def test_observer_registration_follows_enable_disable(monkeypatch, llmobs, tracer):
    event_name = "aws_sdk_bedrock_runtime.bidirectional_stream"
    assert core.has_listeners(event_name)
    LLMObs.disable()
    assert not core.has_listeners(event_name)
    underlying = await mock_stream(monkeypatch, [])
    async with AsyncBedrockRuntimeClient() as client:
        request = InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL)
        assert await client.invoke_model_with_bidirectional_stream(request) is underlying
        # Patching before enable must begin observing later calls when LLMObs starts.
        LLMObs.enable(integrations_enabled=False, _tracer=tracer, agentless_enabled=False)
        LLMObs._instance._llmobs_span_writer.stop()
        monkeypatch.setattr(LLMObs._instance._llmobs_span_writer, "enqueue", MagicMock())
        assert core.has_listeners(event_name)
        result = await client.invoke_model_with_bidirectional_stream(request)
        assert isinstance(result, DuplexProxy)
        await result.close()
    LLMObs.disable()
    assert not core.has_listeners(event_name)


@pytest.mark.asyncio
async def test_observer_initialization_failure_preserves_sdk_result(monkeypatch, llmobs):
    underlying = await mock_stream(monkeypatch, [])
    monkeypatch.setattr(core, "dispatch_event", MagicMock(side_effect=RuntimeError("observer failed")))
    async with AsyncBedrockRuntimeClient() as client:
        result = await client.invoke_model_with_bidirectional_stream(
            InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL)
        )
    assert result is underlying
    assert not llmobs.enqueue.called


def serialized_turn(llmobs, index=0):
    # Round-trip the writer payload, not Span internals, to assert the consumer contract.
    payloads = json.loads(json.dumps([call.args[0] for call in llmobs.enqueue.call_args_list]))
    roots = sorted((p for p in payloads if p["name"] == "nova sonic audio turn"), key=lambda p: p["start_ns"])
    root = roots[index]
    children = {p["name"]: p for p in payloads if p["parent_id"] == root["span_id"]}
    assert set(children) == {"user speech", "nova sonic response", "agent speech"}
    assert root["meta"]["span"]["kind"] == "workflow"
    for child in children.values():
        assert child["trace_id"] == root["trace_id"]
        assert child["session_id"] == root["session_id"] == "review-session"
    response = children["nova sonic response"]
    assert response["meta"]["span"]["kind"] == "llm"
    for phase in (root, children["user speech"], children["agent speech"]):
        assert "audio_parts" not in json.dumps(phase["meta"])
    assert (
        children["agent speech"]["start_ns"]
        - (children["user speech"]["start_ns"] + children["user speech"]["duration"])
        == 500_000_000
    )
    assert response["meta"]["metadata"]["ttfa_boundary"] == "speech_end_event_receipt"
    return root, children, response


def speech_state(input_configuration=INPUT):
    state = SonicState(AwsSdkBedrockRuntimeIntegration(config.aws_sdk_bedrock_runtime), MODEL, Context())
    state.sent(
        "contentStart",
        {"contentName": "mic", "role": "USER", "type": "AUDIO", "audioInputConfiguration": input_configuration},
        0,
    )
    state.sent(
        "audioInput", {"contentName": "mic", "content": base64.b64encode(b"\x01\x00" * 16000).decode()}, 1_000_000_000
    )
    state.received("userSpeechStart", {"inputAudioOffsetMs": 100, "sessionId": "review-session"}, 1_100_000_000)
    state.received("userSpeechEnd", {"inputAudioOffsetMs": 500}, 2_500_000_000)
    state.received(
        "contentStart",
        {"contentId": "output", "role": "ASSISTANT", "type": "AUDIO", "audioOutputConfiguration": OUTPUT},
        2_700_000_000,
    )
    return state


def output_chunk(state, now, samples=2400):
    state.received(
        "audioOutput", {"contentId": "output", "content": base64.b64encode(b"\x02\x00" * samples).decode()}, now
    )


@pytest.mark.parametrize("gap_seconds,limit", [(1, None), (40, None), (70, None), (0, 6000)])
def test_serialized_audio_placement_and_overflow(monkeypatch, llmobs, gap_seconds, limit):
    if limit is not None:
        monkeypatch.setattr(sonic_module, "MAX_AUDIO", limit)
    state = speech_state()
    output_chunk(state, 3_000_000_000)
    # The second chunk queues after the first, even though it arrives before playback ends.
    output_chunk(state, 3_050_000_000)
    last_start = 3_200_000_000 + gap_seconds * 1_000_000_000
    if gap_seconds:
        # A tool round-trip stays in this logical turn; its silence is real elapsed time.
        state.received("contentStart", {"contentId": "tool", "role": "ASSISTANT", "type": "TOOL"}, 3_100_000_000)
        state.received(
            "toolUse",
            {"contentId": "tool", "toolUseId": "tool-1", "toolName": "lookup", "content": "{}"},
            3_150_000_000,
        )
        state.sent(
            "contentStart",
            {"contentName": "result", "role": "TOOL", "toolResultInputConfiguration": {"toolUseId": "tool-1"}},
            last_start - 2,
        )
        state.sent("toolResult", {"contentName": "result", "content": "done"}, last_start - 1)
    output_chunk(state, last_start)
    state.received("contentEnd", {"contentId": "output", "stopReason": "END_TURN"}, last_start + 10_000_000)
    state.finish()
    root, phases, response = serialized_turn(llmobs)
    agent = phases["agent speech"]
    assert agent["duration"] == last_start + 100_000_000 - agent["start_ns"]
    assert root["start_ns"] + root["duration"] == last_start + 100_000_000
    message = response["meta"]["output"]["messages"][0]
    if gap_seconds == 70 or limit is not None:
        assert "audio_parts" not in message
        assert response["meta"]["metadata"]["output_audio_omitted_reason"] == "retention_limit"
    else:
        rate, pcm = audio(message)
        assert rate == 24000
        assert pcm == b"\x02\x00" * 4800 + bytes(gap_seconds * 48000) + b"\x02\x00" * 2400
        assert len(pcm) * 1_000_000_000 // (2 * rate) == agent["duration"]
    assert len(json.dumps(response).encode()) < 4 * 1024 * 1024 + 8192


@pytest.mark.parametrize("fields", [{"generationStage": "FINAL"}, "broken{", '["FINAL"]', '"FINAL"', None])
def test_serialized_content_metadata_fallback(llmobs, fields):
    state = speech_state()
    # Invalid optional metadata must not discard an audio block or its timing.
    state.received(
        "contentStart",
        {
            "contentId": "metadata-audio",
            "type": "AUDIO",
            "role": "ASSISTANT",
            "additionalModelFields": fields,
            "audioOutputConfiguration": OUTPUT,
        },
        2_800_000_000,
    )
    state.received(
        "audioOutput",
        {"contentId": "metadata-audio", "content": base64.b64encode(b"\x02\x00" * 2400).decode()},
        3_000_000_000,
    )
    state.received("contentEnd", {"contentId": "metadata-audio", "stopReason": "END_TURN"}, 3_020_000_000)
    state.received(
        "contentStart",
        {"contentId": "text", "type": "TEXT", "role": "ASSISTANT", "additionalModelFields": fields},
        3_030_000_000,
    )
    state.received("textOutput", {"contentId": "text", "content": "retained text"}, 3_040_000_000)
    state.received("contentEnd", {"contentId": "text"}, 3_050_000_000)
    state.finish()
    _, _, response = serialized_turn(llmobs)
    message = response["meta"]["output"]["messages"][0]
    assert message["content"] == "retained text"
    assert audio(message) == (24000, b"\x02\x00" * 2400)


def test_serialized_interruption_ends_before_next_turn(llmobs):
    state = speech_state()
    output_chunk(state, 3_000_000_000, samples=48000)
    state.received("contentEnd", {"contentId": "output", "stopReason": "INTERRUPTED"}, 3_250_000_000)
    state.received("userSpeechStart", {"inputAudioOffsetMs": 600}, 3_300_000_000)
    # A decoded FINAL stage arriving after barge-in still belongs to the interrupted turn.
    state.received(
        "contentStart",
        {
            "contentId": "late",
            "type": "TEXT",
            "role": "ASSISTANT",
            "additionalModelFields": {"generationStage": "FINAL"},
        },
        3_400_000_000,
    )
    state.received("textOutput", {"contentId": "late", "content": "late final"}, 3_450_000_000)
    state.received("contentEnd", {"contentId": "late"}, 3_500_000_000)
    state.received("userSpeechEnd", {"inputAudioOffsetMs": 900}, 8_000_000_000)
    state.received(
        "contentStart",
        {"contentId": "next", "type": "AUDIO", "role": "ASSISTANT", "audioOutputConfiguration": OUTPUT},
        8_100_000_000,
    )
    root, phases, response = serialized_turn(llmobs)
    for span in (root, response, phases["agent speech"]):
        assert span["start_ns"] + span["duration"] == 3_250_000_000
    assert response["meta"]["metadata"]["partial"]
    assert response["meta"]["metadata"]["interrupted"]
    assert response["meta"]["output"]["messages"][0]["content"] == "late final"
    assert audio(response["meta"]["output"]["messages"][0]) == (24000, b"\x02\x00" * 6000)
    state.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["body", "cleanup", "cancelled", "suppressed", "clean"])
async def test_serialized_input_context_exit(llmobs, failure):
    state = speech_state()
    output_chunk(state, 3_000_000_000)
    error = asyncio.CancelledError() if failure == "cancelled" else RuntimeError("input context failed")
    sender = MagicMock()
    sender.__aenter__ = AsyncMock(return_value=sender)
    sender.__aexit__ = AsyncMock(
        return_value=failure == "suppressed", side_effect=error if failure == "cleanup" else None
    )
    proxy = InputProxy(sender, state)
    if failure in ("body", "cancelled", "cleanup"):
        with pytest.raises(type(error)) as caught:
            async with proxy:
                if failure != "cleanup":
                    raise error
        assert caught.value is error
    else:
        async with proxy:
            if failure == "suppressed":
                raise error
    if failure == "clean":
        assert not llmobs.enqueue.called
        assert not state.closed
        state.received("contentEnd", {"contentId": "output", "stopReason": "END_TURN"}, 3_050_000_000)
        state.finish()
    _, phases, _ = serialized_turn(llmobs)
    assert all(p["status"] == ("ok" if failure == "clean" else "error") for p in phases.values())
    count = llmobs.enqueue.call_count
    state.finish()
    assert llmobs.enqueue.call_count == count


@pytest.mark.parametrize("capture_name,turn_count", [("voice-session-1", 5), ("voice-session-2", 6)])
def test_serialized_capture_turn_contract(monkeypatch, llmobs, capture_name, turn_count):
    with gzip.open(Path(__file__).parent / "fixtures" / (capture_name + ".json.gz"), "rt") as source:
        capture = json.load(source)
    state = SonicState(AwsSdkBedrockRuntimeIntegration(config.aws_sdk_bedrock_runtime), MODEL, Context())
    now = [1_000_000_000]
    monkeypatch.setattr(sonic_module.time, "time_ns", lambda: now[0])
    speech_ends, audio_starts, interruptions = [], [], []
    for record in capture["events"]:
        now[0] = 1_000_000_000 + record["at_ns"]
        for name, original in record["event"].items():
            data = dict(original)
            if "audio_bytes" in record:
                data["content"] = base64.b64encode(bytes(record["audio_bytes"])).decode()
            if record["direction"] == "inbound":
                if name == "userSpeechEnd":
                    speech_ends.append(now[0])
                elif name == "audioOutput":
                    audio_starts.append(now[0])
                elif name == "contentEnd" and data.get("stopReason") == "INTERRUPTED":
                    interruptions.append(now[0])
                elif name == "textOutput" and data.get("content") == '{"interrupted":true}':
                    interruptions.append(now[0])
            state.observe(event(name, data), outbound=record["direction"] == "outbound")
    state.finish()
    payloads = json.loads(json.dumps([call.args[0] for call in llmobs.enqueue.call_args_list]))
    roots = sorted((p for p in payloads if p["name"] == "nova sonic audio turn"), key=lambda p: p["start_ns"])
    assert len(roots) == turn_count
    assert len(payloads) == turn_count * 4
    assert len({p["session_id"] for p in payloads}) == 1
    for index, root in enumerate(roots):
        children = {p["name"]: p for p in payloads if p["parent_id"] == root["span_id"]}
        assert set(children) == {"user speech", "nova sonic response", "agent speech"}
        assert all(p["trace_id"] == root["trace_id"] for p in children.values())
        user, agent, response = (children[n] for n in ("user speech", "agent speech", "nova sonic response"))
        user_end = user["start_ns"] + user["duration"]
        assert agent["start_ns"] in audio_starts
        expected_user_end = max(t for t in speech_ends if t <= agent["start_ns"])
        assert user_end == pytest.approx(expected_user_end, abs=1)
        assert agent["start_ns"] - user_end == pytest.approx(agent["start_ns"] - expected_user_end, abs=1)
        metadata = response["meta"]["metadata"]
        assert metadata["ttfa_boundary"] == "speech_end_event_receipt"
        input_rate, input_pcm = audio(response["meta"]["input"]["messages"][-1])
        windows = metadata["speech_windows"]
        assert len(input_pcm) == int((windows[-1]["end_ms"] - windows[0]["start_ms"]) * input_rate / 1000) * 2
        output_rate, output_pcm = audio(response["meta"]["output"]["messages"][0])
        # Each PCM chunk rounds to whole nanoseconds; allow sub-microsecond accumulation.
        assert agent["duration"] == pytest.approx(len(output_pcm) * 1e9 / (2 * output_rate), abs=1000)
        assert root["start_ns"] + root["duration"] == pytest.approx(
            max(p["start_ns"] + p["duration"] for p in children.values()), abs=1
        )
        if metadata["interrupted"] and metadata["partial"]:
            cutoff = min(t for t in interruptions if t >= agent["start_ns"])
            assert response["start_ns"] + response["duration"] == pytest.approx(cutoff, abs=1)
            if index + 1 < len(roots):
                next_response = next(
                    p
                    for p in payloads
                    if p["name"] == "nova sonic response" and p["parent_id"] == roots[index + 1]["span_id"]
                )
                assert response["start_ns"] + response["duration"] < next_response["start_ns"]


def test_serialized_shared_payload_budget_keeps_timing(monkeypatch, llmobs):
    monkeypatch.setattr(sonic_module, "LLMOBS_AUDIO_INLINE_MAX_BYTES", 22000)
    state = speech_state()
    output_chunk(state, 3_000_000_000)
    state.received("contentEnd", {"contentId": "output", "stopReason": "END_TURN"}, 3_050_000_000)
    state.finish()
    _, _, response = serialized_turn(llmobs)
    assert audio(response["meta"]["input"]["messages"][-1]) == (16000, b"\x01\x00" * 6400)
    assert "audio_parts" not in response["meta"]["output"]["messages"][0]
    assert response["meta"]["metadata"]["output_audio_omitted_reason"] == "payload_limit"


def test_serialized_invalid_input_retains_provider_speech_boundaries(llmobs):
    state = speech_state(dict(INPUT, sampleSizeBits=8))
    output_chunk(state, 3_000_000_000)
    state.received("contentEnd", {"contentId": "output", "stopReason": "END_TURN"}, 3_050_000_000)
    state.finish()
    _, _, response = serialized_turn(llmobs)
    assert "audio_parts" not in response["meta"]["input"]["messages"][-1]
    assert audio(response["meta"]["output"]["messages"][0]) == (24000, b"\x02\x00" * 2400)
