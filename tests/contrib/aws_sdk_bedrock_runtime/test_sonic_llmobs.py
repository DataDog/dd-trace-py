import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import aws_sdk_bedrock_runtime
from aws_sdk_bedrock_runtime.client import AsyncBedrockRuntimeClient
from aws_sdk_bedrock_runtime.models import InvokeModelWithBidirectionalStreamOperationInput
import pytest
from smithy_core.aio.eventstream import DuplexEventStream

from ddtrace import patch as patch_integrations
from ddtrace.contrib.internal.aws_sdk_bedrock_runtime._stream import DuplexProxy
from ddtrace.contrib.internal.aws_sdk_bedrock_runtime.patch import unpatch
from ddtrace.internal import core
from ddtrace.internal.settings.standalone import standalone_config
from ddtrace.llmobs import LLMObs
from tests.contrib.aws_sdk_bedrock_runtime.test_sonic import INPUT
from tests.contrib.aws_sdk_bedrock_runtime.test_sonic import MODEL
from tests.contrib.aws_sdk_bedrock_runtime.test_sonic import OUTPUT
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
async def test_patched_sdk_emits_real_llmobs_tree(monkeypatch, llmobs):
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
    with LLMObs.workflow(name="application") as outer:
        async with AsyncBedrockRuntimeClient() as client:
            stream = await client.invoke_model_with_bidirectional_stream(
                InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL)
            )
            assert isinstance(stream, DuplexProxy)
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
    assert len(emitted) == 5
    by_name = {span["name"]: span for span in emitted}
    root = by_name["nova sonic audio turn"]
    assert root["parent_id"] == str(outer.span_id)
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
