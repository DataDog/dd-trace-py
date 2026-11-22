import asyncio
import base64
from contextlib import ExitStack
from contextlib import nullcontext
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
from ddtrace.contrib.internal.aws_sdk_bedrock_runtime._stream import OutputProxy
from ddtrace.contrib.internal.aws_sdk_bedrock_runtime.patch import unpatch
from ddtrace.internal import core
from ddtrace.internal.settings.standalone import standalone_config
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._constants import PROPAGATED_LLMOBS_TRACE_ID_KEY
from ddtrace.llmobs._constants import PROPAGATED_ML_APP_KEY
from ddtrace.llmobs._constants import PROPAGATED_PARENT_AGENT_ID_KEY
from ddtrace.llmobs._constants import PROPAGATED_PARENT_AGENT_NAME_KEY
from ddtrace.llmobs._constants import PROPAGATED_PARENT_ID_KEY
from ddtrace.llmobs._constants import PROPAGATED_SAMPLE_RATE
from ddtrace.llmobs._constants import PROPAGATED_SAMPLING_DECISION
from ddtrace.llmobs._integrations import aws_sdk_bedrock_runtime_utils as sonic_module
from ddtrace.llmobs._integrations.aws_sdk_bedrock_runtime import AwsSdkBedrockRuntimeIntegration
from ddtrace.llmobs._integrations.aws_sdk_bedrock_runtime_utils import SonicState
from ddtrace.llmobs._routing import RoutingTarget
from ddtrace.llmobs._routing import get_routing_context
from ddtrace.llmobs._utils import get_llmobs_trace_id
from ddtrace.llmobs._writer import LLMObsSpanWriter
from ddtrace.trace import Context
from tests.contrib.aws_sdk_bedrock_runtime.test_sonic import INPUT
from tests.contrib.aws_sdk_bedrock_runtime.test_sonic import MODEL
from tests.contrib.aws_sdk_bedrock_runtime.test_sonic import OUTPUT
from tests.contrib.aws_sdk_bedrock_runtime.test_sonic import MockReceiver
from tests.contrib.aws_sdk_bedrock_runtime.test_sonic import audio
from tests.contrib.aws_sdk_bedrock_runtime.test_sonic import event
from tests.utils import override_global_config


@pytest.fixture
def llmobs(monkeypatch, tracer, request):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(standalone_config, "apm_tracing_enabled", False)
    LLMObs.disable()
    with override_global_config(
        {"_llmobs_ml_app": "sonic-test", "_dd_api_key": "not-a-real-key", **getattr(request, "param", {})}
    ):
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
    receiver = MockReceiver(events + [None])
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
    response = next(span for span in emitted if span["name"] == "nova sonic response")
    assert response["metrics"] == {}
    assert response["meta"]["input"]["messages"][0]["content"] == ""
    assert response["meta"]["output"]["messages"][0]["content"] == ""


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
    state = SonicState(
        AwsSdkBedrockRuntimeIntegration(config.aws_sdk_bedrock_runtime),
        MODEL,
        Context(),
        LLMObs._instance._llmobs_context_provider,
    )
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
    state = SonicState(
        AwsSdkBedrockRuntimeIntegration(config.aws_sdk_bedrock_runtime),
        MODEL,
        Context(),
        LLMObs._instance._llmobs_context_provider,
    )
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
    responses = [p for p in payloads if p["name"] == "nova sonic response"]
    final_usage = [r["event"]["usageEvent"] for r in capture["events"] if "usageEvent" in r["event"]][-1]
    for direction in ("input", "output"):
        audio_key = direction + "_audio_tokens"
        assert (
            sum(p["metrics"][audio_key] for p in responses)
            == final_usage["details"]["total"][direction]["speechTokens"]
        )
        assert all(0 <= p["metrics"][audio_key] <= p["metrics"][direction + "_tokens"] for p in responses)
    for direction in ("input", "output"):
        text_key = direction + "_text_tokens"
        assert (
            sum(p["metrics"][text_key] for p in responses) == final_usage["details"]["total"][direction]["textTokens"]
        )
    assert sum(p["metrics"]["total_tokens"] for p in responses) == capture["input_tokens"] + capture["output_tokens"]
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


@pytest.mark.parametrize("late_chunk", ["valid", "invalid_base64", "odd_bytes", "changed_rate", "unsupported_format"])
def test_serialized_late_audio_preserves_interrupted_turn(llmobs, late_chunk):
    state = speech_state()
    output_chunk(state, 3_000_000_000, samples=48000)
    state.received(
        "contentStart",
        {
            "contentId": "control",
            "type": "TEXT",
            "role": "ASSISTANT",
            "additionalModelFields": {"generationStage": "FINAL"},
        },
        3_200_000_000,
    )
    state.received("textOutput", {"contentId": "control", "content": '{"interrupted":true}'}, 3_250_000_000)
    configuration = dict(OUTPUT)
    raw = b"\x03\x00" * 2400
    if late_chunk == "changed_rate":
        configuration["sampleRateHertz"] = 48000
    elif late_chunk == "unsupported_format":
        configuration["mediaType"] = "audio/mpeg"
    elif late_chunk == "odd_bytes":
        raw = b"odd"
    content = "invalid!" if late_chunk == "invalid_base64" else base64.b64encode(raw).decode()
    state.received(
        "contentStart",
        {"contentId": "late-audio", "type": "AUDIO", "role": "ASSISTANT", "audioOutputConfiguration": configuration},
        3_300_000_000,
    )
    state.received("audioOutput", {"contentId": "late-audio", "content": content}, 3_350_000_000)
    state.finish()
    root, phases, response = serialized_turn(llmobs)
    for span in (root, phases["agent speech"], response):
        assert span["start_ns"] + span["duration"] == 3_250_000_000
    message = response["meta"]["output"]["messages"][0]
    assert audio(message) == (24000, b"\x02\x00" * 6000)
    metadata = response["meta"]["metadata"]
    assert metadata["interrupted"]
    assert "output_audio_omitted_reason" not in metadata
    assert metadata["generated_output_bytes"] == 96000 + (0 if late_chunk == "invalid_base64" else len(raw))


@pytest.mark.asyncio
@pytest.mark.parametrize("caller_kind", ["workflow", "agent"])
@pytest.mark.parametrize("llmobs", [{"_llmobs_sampling_rules": '[{"sample_rate":0.5}]'}], indirect=True)
async def test_caller_snapshot_survives_export_and_restores_receiver(monkeypatch, llmobs, tracer, caller_kind):
    events = [
        event("contentStart", {"contentId": "answer", "role": "ASSISTANT", "type": "TEXT"}),
        event("textOutput", {"contentId": "answer", "content": "answer"}),
        event("contentEnd", {"contentId": "answer"}),
    ]
    await mock_stream(monkeypatch, events)
    async with AsyncBedrockRuntimeClient() as client:
        with getattr(LLMObs, caller_kind)(name="caller", agent_service="tenant-a") as caller:
            expected_trace = get_llmobs_trace_id(caller)
            stream = await client.invoke_model_with_bidirectional_stream(
                InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL)
            )
        assert caller._get_struct_tag(LLMOBS_STRUCT.KEY) is None
        caller_event = next(call.args[0] for call in llmobs.enqueue.call_args_list if call.args[0]["name"] == "caller")
        with LLMObs.workflow(name="receiver", agent_service="tenant-b") as receiver:
            provider = LLMObs._instance._llmobs_context_provider
            _, output = await stream.await_output()
            assert [item async for item in output] == events
            assert provider.active() is receiver
            with LLMObs.task(name="after"):
                pass
        await stream.close()
    emitted = [call.args[0] for call in llmobs.enqueue.call_args_list]
    root = next(span for span in emitted if span["name"] == "nova sonic audio turn")
    response = next(span for span in emitted if span["name"] == "nova sonic response")
    after = next(span for span in emitted if span["name"] == "after")
    assert root["parent_id"] == str(caller.span_id)
    assert response["parent_id"] == root["span_id"]
    for span in (root, response):
        assert span["trace_id"] == expected_trace
        assert "agent_service:tenant-a" in span["tags"]
        assert span["_dd"]["sample_rate"] == caller_event["_dd"]["sample_rate"] == "0.5"
        assert span["_dd"]["sampling_decision"] == caller_event["_dd"]["sampling_decision"]
        if caller_kind == "agent":
            assert span["meta"]["agent_attribution"]["pagent_name"] == "caller"
            assert span["meta"]["agent_attribution"]["pagent_span_id"] == str(caller.span_id)
    assert after["parent_id"] == str(receiver.span_id)
    assert "agent_service:tenant-b" in after["tags"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_emission", [False, True])
async def test_unparented_stream_does_not_adopt_or_displace_receiver(monkeypatch, llmobs, fail_emission):
    events = [event("contentStart", {"contentId": "answer", "role": "ASSISTANT", "type": "TEXT"})]
    await mock_stream(monkeypatch, events)
    async with AsyncBedrockRuntimeClient() as client:
        stream = await client.invoke_model_with_bidirectional_stream(
            InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL)
        )
        if fail_emission:
            original = AwsSdkBedrockRuntimeIntegration.start_span

            def start_span(self, name, *args):
                if name == "nova sonic response":
                    raise RuntimeError("annotation failed")
                return original(self, name, *args)

            monkeypatch.setattr(AwsSdkBedrockRuntimeIntegration, "start_span", start_span)
        with LLMObs.workflow(name="receiver", agent_service="receiver-app") as receiver:
            _, output = await stream.await_output()
            assert [item async for item in output] == events
            assert LLMObs._instance._llmobs_context_provider.active() is receiver
        await stream.close()
    root = next(
        call.args[0] for call in llmobs.enqueue.call_args_list if call.args[0]["name"] == "nova sonic audio turn"
    )
    assert root["parent_id"] == "undefined"
    assert "agent_service:sonic-test" in root["tags"]


@pytest.mark.asyncio
async def test_propagated_caller_snapshot_is_independent(monkeypatch, llmobs, tracer):
    events = [event("contentStart", {"contentId": "answer", "role": "ASSISTANT", "type": "TEXT"})]
    await mock_stream(monkeypatch, events)
    context = Context(trace_id=123, span_id=456)
    context._meta.update(
        {
            PROPAGATED_LLMOBS_TRACE_ID_KEY: str(int("ef017ddb6db557ea44fb6ce732fd0687", 16)),
            PROPAGATED_PARENT_ID_KEY: "456",
            PROPAGATED_ML_APP_KEY: "remote-app",
            PROPAGATED_SAMPLE_RATE: "0.25",
            PROPAGATED_SAMPLING_DECISION: "0",
            PROPAGATED_PARENT_AGENT_ID_KEY: "111",
            PROPAGATED_PARENT_AGENT_NAME_KEY: "remote-agent",
        }
    )
    LLMObs.activate_distributed_headers(
        {
            "x-datadog-trace-id": "123",
            "x-datadog-parent-id": "456",
            "x-datadog-sampling-priority": "1",
            "x-datadog-tags": ",".join(f"{key}={value}" for key, value in context._meta.items()),
        }
    )
    provider = LLMObs._instance._llmobs_context_provider
    propagated_context = provider.active()
    async with AsyncBedrockRuntimeClient() as client:
        with tracer.trace("local-request"):
            stream = await client.invoke_model_with_bidirectional_stream(
                InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL)
            )
        propagated_context._meta.clear()
        provider.activate(None)
        _, output = await stream.await_output()
        assert [item async for item in output] == events
        assert provider.active() is None
        await stream.close()
    emitted = [call.args[0] for call in llmobs.enqueue.call_args_list]
    root = next(span for span in emitted if span["name"] == "nova sonic audio turn")
    assert root["parent_id"] == "456"
    for span in emitted:
        assert span["trace_id"] == "ef017ddb6db557ea44fb6ce732fd0687"
        assert "agent_service:remote-app" in span["tags"]
        assert span["meta"]["agent_attribution"]["pagent_span_id"] == "111"
        assert span["meta"]["agent_attribution"]["pagent_name"] == "remote-agent"
        assert span["_dd"]["sample_rate"] == "0.25"
        assert span["_dd"]["sampling_decision"] == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize("half", ["input", "output", "duplex"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_context_entry_error_finalizes_once(llmobs, half, cancelled):
    state = speech_state()
    output_chunk(state, 3_000_000_000)
    error = asyncio.CancelledError() if cancelled else RuntimeError("context entry failed")
    wrapped = MagicMock()
    wrapped.__aenter__ = AsyncMock(side_effect=error)
    wrapped.input_stream = MagicMock()
    wrapped.output_stream = None
    proxy = {"input": InputProxy, "output": OutputProxy, "duplex": DuplexProxy}[half](wrapped, state)
    with pytest.raises(type(error)) as caught:
        async with proxy:
            pytest.fail("context body must not execute")
    assert caught.value is error
    assert state.closed
    _, phases, _ = serialized_turn(llmobs)
    assert all(span["status"] == "error" for span in phases.values())
    count = llmobs.enqueue.call_count
    state.finish()
    assert llmobs.enqueue.call_count == count
    wrapped.__aexit__.assert_not_called()


@pytest.mark.parametrize("late_end", ["output", "late-audio"])
def test_barge_in_keeps_unfinished_input_and_ignores_late_end(llmobs, late_end):
    state = speech_state()
    output_chunk(state, 3_000_000_000, samples=48000)
    state.received("userSpeechStart", {"inputAudioOffsetMs": 600}, 3_200_000_000)
    state.received(
        "contentStart",
        {
            "contentId": "control",
            "type": "TEXT",
            "role": "ASSISTANT",
            "additionalModelFields": {"generationStage": "FINAL"},
        },
        3_210_000_000,
    )
    state.received("textOutput", {"contentId": "control", "content": '{"interrupted":true}'}, 3_250_000_000)
    state.received(
        "contentStart",
        {
            "contentId": "late-audio",
            "type": "AUDIO",
            "role": "ASSISTANT",
            "audioOutputConfiguration": OUTPUT,
        },
        3_300_000_000,
    )
    state.received(
        "audioOutput", {"contentId": "late-audio", "content": base64.b64encode(bytes(4800)).decode()}, 3_350_000_000
    )
    state.received("contentEnd", {"contentId": late_end, "stopReason": "END_TURN"}, 3_400_000_000)
    assert not llmobs.enqueue.called
    assert state.pending.windows == [{"start_ms": 600}]
    state.received("userSpeechEnd", {"inputAudioOffsetMs": 900}, 3_500_000_000)
    state.received(
        "contentStart",
        {
            "contentId": "next-output",
            "type": "AUDIO",
            "role": "ASSISTANT",
            "audioOutputConfiguration": OUTPUT,
        },
        4_000_000_000,
    )
    state.received(
        "audioOutput", {"contentId": "next-output", "content": base64.b64encode(bytes(4800)).decode()}, 4_000_000_000
    )
    state.received("contentEnd", {"contentId": "next-output", "stopReason": "END_TURN"}, 4_100_000_000)
    state.finish()
    root, phases, response = serialized_turn(llmobs)
    for span in (root, phases["agent speech"], response):
        assert span["start_ns"] + span["duration"] == 3_250_000_000
    assert audio(response["meta"]["output"]["messages"][0]) == (24000, b"\x02\x00" * 6000)
    payloads = [call.args[0] for call in llmobs.enqueue.call_args_list]
    responses = [span for span in payloads if span["name"] == "nova sonic response"]
    assert len(responses) == 2
    assert responses[1]["meta"]["metadata"]["speech_windows"][0]["end_ms"] == 900
    assert audio(responses[1]["meta"]["input"]["messages"][-1])[1] == b"\x01\x00" * 4800


@pytest.mark.parametrize("limit", [4096, 22000, 32768])
def test_configured_event_budget_preserves_transcripts(monkeypatch, llmobs, limit):
    with override_global_config({"_llmobs_event_size_limit": limit}):
        state = speech_state()
        state.sent(
            "promptStart",
            {
                "toolConfiguration": {
                    "tools": [
                        {
                            "toolSpec": {
                                "name": "lookup",
                                "description": "D" * 400,
                                "inputSchema": {"json": {"type": "object"}},
                            }
                        }
                    ]
                }
            },
            2_800_000_000,
        )
        state.current.user_text = "user transcript"
        state.current.final_text = "assistant transcript"
        output_chunk(state, 3_000_000_000, samples=24000)
        state.received("contentEnd", {"contentId": "output", "stopReason": "END_TURN"}, 3_050_000_000)
        state.finish()
        _, _, response = serialized_turn(llmobs)
        assert response["meta"]["tool_definitions"][0]["name"] == "lookup"
        assert response["meta"]["metadata"]["finish_reason"] == "END_TURN"
        assert len(json.dumps(response)) < limit
        # Exercise the writer's real size enforcement, not just its mocked enqueue boundary.
        queued = MagicMock()
        monkeypatch.setattr(llmobs, "_enqueue", queued)
        LLMObsSpanWriter.enqueue(llmobs, response)
        actual = queued.call_args.args[0]
        assert actual["meta"]["input"]["messages"][-1]["content"] == "user transcript"
        assert actual["meta"]["output"]["messages"][0]["content"] == "assistant transcript"
        assert "audio_parts" not in actual["meta"]["output"]["messages"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
@pytest.mark.parametrize("llmobs", [{"_llmobs_sampling_rules": '[{"sample_rate":0.5}]'}], indirect=True)
async def test_open_stream_keeps_context_across_llmobs_restart(monkeypatch, llmobs, tracer, started):
    events = [
        event("contentStart", {"contentId": "text", "role": "ASSISTANT", "type": "TEXT"}),
        event("textOutput", {"contentId": "text", "content": "answer"}),
        event("contentEnd", {"contentId": "text"}),
    ]
    await mock_stream(monkeypatch, events)
    async with AsyncBedrockRuntimeClient() as client:
        with LLMObs.workflow(name="caller", agent_service="caller-app") as caller:
            trace_id = get_llmobs_trace_id(caller)
            stream = await client.invoke_model_with_bidirectional_stream(
                InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL)
            )
        caller_event = next(c.args[0] for c in llmobs.enqueue.call_args_list if c.args[0]["name"] == "caller")
        old_provider = LLMObs._instance._llmobs_context_provider
        _, output = await stream.await_output()
        if started:
            assert await output.receive() is events[0]
        LLMObs.disable()
        LLMObs.enable(integrations_enabled=False, _tracer=tracer, agentless_enabled=False)
        writer = LLMObs._instance._llmobs_span_writer
        writer.stop()
        monkeypatch.setattr(writer, "enqueue", MagicMock())
        provider = LLMObs._instance._llmobs_context_provider
        assert provider is not old_provider
        with LLMObs.workflow(name="receiver", agent_service="receiver-app") as receiver:
            assert old_provider.active() is provider.active() is receiver
            assert [item async for item in output] == events[int(started) :]
            assert provider.active() is receiver
        await stream.close()
    payloads = [call.args[0] for call in writer.enqueue.call_args_list]
    root = next(s for s in payloads if s["name"] == "nova sonic audio turn")
    llm = next(s for s in payloads if s["name"] == "nova sonic response")
    assert root["parent_id"] == str(caller.span_id)
    assert llm["parent_id"] == root["span_id"]
    for span in (root, llm):
        assert span["trace_id"] == trace_id
        assert "agent_service:caller-app" in span["tags"]
        assert span["_dd"]["sample_rate"] == caller_event["_dd"]["sample_rate"] == "0.5"
        assert span["_dd"]["sampling_decision"] == caller_event["_dd"]["sampling_decision"]


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["close", "error", "cancelled"])
async def test_unreported_usage_stays_unknown_on_partial_response(monkeypatch, llmobs, finish):
    events = [
        event("contentStart", {"contentId": "text", "type": "TEXT", "role": "ASSISTANT"}),
        event("textOutput", {"contentId": "text", "content": "partial"}),
    ]
    error = asyncio.CancelledError() if finish == "cancelled" else RuntimeError("stream failed")
    await mock_stream(monkeypatch, events if finish == "close" else events + [error])
    async with AsyncBedrockRuntimeClient() as client:
        stream = await client.invoke_model_with_bidirectional_stream(
            InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL)
        )
        _, receiver = await stream.await_output()
        for expected in events:
            assert await receiver.receive() is expected
        if finish == "close":
            await stream.close()
        else:
            with pytest.raises(type(error)) as caught:
                await receiver.receive()
            assert caught.value is error
    response = next(c.args[0] for c in llmobs.enqueue.call_args_list if c.args[0]["name"] == "nova sonic response")
    assert response["metrics"] == {}
    assert response["meta"]["input"]["messages"][0]["content"] == ""
    assert response["meta"]["output"]["messages"][0]["content"] == "partial"


@pytest.mark.parametrize(
    "kind",
    ["tool", "input", "output", "empty-input", "empty-output", "invalid-input", "invalid-output", "omitted-output"],
)
def test_audio_placeholder_requires_observed_unrepresented_audio(monkeypatch, llmobs, kind):
    state = SonicState(
        AwsSdkBedrockRuntimeIntegration(config.aws_sdk_bedrock_runtime),
        MODEL,
        Context(),
        LLMObs._instance._llmobs_context_provider,
    )
    input_audio = kind == "input" or kind.endswith("-input")
    output_audio = kind == "output" or kind.endswith("-output")
    if kind == "omitted-output":
        monkeypatch.setattr(sonic_module, "MAX_AUDIO", 2)
    content = "" if kind.startswith("empty-") else "invalid!" if kind.startswith("invalid-") else "AAAAAA=="
    if input_audio:
        state.observe(
            event(
                "contentStart",
                {"contentName": "mic", "type": "AUDIO", "role": "USER", "audioInputConfiguration": INPUT},
            ),
            outbound=True,
        )
        state.observe(event("audioInput", {"contentName": "mic", "content": content}), outbound=True)
    if output_audio:
        state.observe(
            event(
                "contentStart",
                {"contentId": "output", "type": "AUDIO", "role": "ASSISTANT", "audioOutputConfiguration": OUTPUT},
            )
        )
        state.observe(event("audioOutput", {"contentId": "output", "content": content}))
    else:
        state.observe(event("contentStart", {"contentId": "tool", "type": "TOOL", "role": "ASSISTANT"}))
        state.observe(
            event("toolUse", {"contentId": "tool", "toolName": "lookup", "toolUseId": "call", "content": "{}"})
        )
    state.finish()
    response = next(c.args[0] for c in llmobs.enqueue.call_args_list if c.args[0]["name"] == "nova sonic response")
    user = response["meta"]["input"]["messages"][-1]
    assistant = response["meta"]["output"]["messages"][0]
    assert user["content"] == ("[audio]" if input_audio and kind != "empty-input" else "")
    assert assistant["content"] == ("[audio]" if kind in ("invalid-output", "omitted-output") else "")
    if kind == "output":
        assert "audio_parts" in assistant
    if kind == "tool" or input_audio:
        assert assistant["tool_calls"][0]["tool_id"] == "call"
    assert response["metrics"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "caller_targets",
    [
        None,
        [{"dd_api_key": "caller-a-key", "dd_site": "datadoghq.eu"}],
        [
            {"dd_api_key": "caller-a-key", "dd_site": "datadoghq.eu"},
            {"dd_api_key": "caller-b-key"},
        ],
    ],
    ids=["default", "single", "dual"],
)
@pytest.mark.parametrize("receiver_routed", [False, True], ids=["receiver-default", "receiver-routed"])
@pytest.mark.parametrize("fail_emission", [False, True], ids=["success", "emission-error"])
async def test_deferred_turn_preserves_caller_routing(
    monkeypatch, llmobs, caller_targets, receiver_routed, fail_emission
):
    clock = SimpleNamespace(now=1_000_000_000)
    monkeypatch.setattr(sonic_module, "time", SimpleNamespace(time_ns=lambda: clock.now))
    timeline = [
        (1_100_000_000, event("userSpeechStart", {"inputAudioOffsetMs": 100, "sessionId": "routing-session"})),
        (2_500_000_000, event("userSpeechEnd", {"inputAudioOffsetMs": 500})),
        (
            2_700_000_000,
            event(
                "contentStart",
                {
                    "contentId": "output",
                    "role": "ASSISTANT",
                    "type": "AUDIO",
                    "audioOutputConfiguration": OUTPUT,
                },
            ),
        ),
        (
            3_000_000_000,
            event("audioOutput", {"contentId": "output", "content": base64.b64encode(bytes(4800)).decode()}),
        ),
        (3_100_000_000, event("contentEnd", {"contentId": "output", "stopReason": "END_TURN"})),
    ]
    await mock_stream(monkeypatch, [item for _, item in timeline])
    # Exercise real span serialization and writer routing; intercept before anything is sent.
    queued = MagicMock()
    monkeypatch.setattr(llmobs, "_enqueue", queued)
    monkeypatch.setattr(llmobs, "enqueue", LLMObsSpanWriter.enqueue.__get__(llmobs))
    if fail_emission:
        original = AwsSdkBedrockRuntimeIntegration.start_span

        def start_span(self, name, *args):
            if name == "agent speech":
                raise RuntimeError("synthetic emission failure")
            return original(self, name, *args)

        monkeypatch.setattr(AwsSdkBedrockRuntimeIntegration, "start_span", start_span)
    async with AsyncBedrockRuntimeClient() as client:
        with LLMObs.routing_context(targets=caller_targets) if caller_targets else nullcontext():
            caller_routing = get_routing_context()
            stream = await client.invoke_model_with_bidirectional_stream(
                InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL)
            )
        if caller_routing is not None:
            # A retained caller dictionary must not be able to change an open stream's destination.
            caller_routing["targets"][0]["api_key"] = "mutated-key"
        await stream.input_stream.send(
            event(
                "contentStart",
                {"contentName": "mic", "role": "USER", "type": "AUDIO", "audioInputConfiguration": INPUT},
            )
        )
        await stream.input_stream.send(
            event("audioInput", {"contentName": "mic", "content": base64.b64encode(bytes(32000)).decode()})
        )
        await stream.input_stream.close()
        with LLMObs.routing_context(dd_api_key="receiver-key") if receiver_routed else nullcontext():
            receiver_routing = get_routing_context()
            with LLMObs.workflow(name="receiver") as receiver:
                _, output = await stream.await_output()
                for now, item in timeline:
                    clock.now = now
                    assert await output.receive() is item
                assert await output.receive() is None
                await stream.close()
                assert get_routing_context() is receiver_routing
                assert LLMObs._instance._llmobs_context_provider.active() is receiver
                with LLMObs.task(name="after"):
                    pass
            assert get_routing_context() is receiver_routing
    assert get_routing_context() is None
    emitted = {call.args[0]["name"]: (call.args[0], call.args[2] or []) for call in queued.call_args_list}
    expected_targets = [RoutingTarget(t["dd_api_key"], t.get("dd_site")) for t in caller_targets or []]
    nova_names = {"nova sonic audio turn", "nova sonic response", "user speech"}
    if not fail_emission:
        nova_names.add("agent speech")
    assert set(emitted) == nova_names | {"receiver", "after"}
    root = emitted["nova sonic audio turn"][0]
    assert root["parent_id"] == "undefined"
    assert root["meta"]["span"]["kind"] == "workflow"
    for name in nova_names:
        span, targets = emitted[name]
        assert targets == expected_targets
        assert span["trace_id"] == root["trace_id"]
        assert span["session_id"] == "routing-session"
        if name != "nova sonic audio turn":
            assert span["parent_id"] == root["span_id"]
        if name != "nova sonic response":
            assert "audio_parts" not in json.dumps(span["meta"])
    response = emitted["nova sonic response"][0]
    assert response["meta"]["span"]["kind"] == "llm"
    assert audio(response["meta"]["input"]["messages"][-1]) == (16000, bytes(12800))
    assert audio(response["meta"]["output"]["messages"][0]) == (24000, bytes(4800))
    assert response["meta"]["metadata"]["ttfa_boundary"] == "speech_end_event_receipt"
    if not fail_emission:
        user, agent = emitted["user speech"][0], emitted["agent speech"][0]
        assert agent["start_ns"] - (user["start_ns"] + user["duration"]) == 500_000_000
    for name in ("receiver", "after"):
        assert emitted[name][1] == ([RoutingTarget("receiver-key")] if receiver_routed else [])
    assert emitted["after"][0]["parent_id"] == emitted["receiver"][0]["span_id"]
    serialized = json.dumps([span for span, _ in emitted.values()])
    assert all(key not in serialized for key in ("caller-a-key", "caller-b-key", "receiver-key", "mutated-key"))
