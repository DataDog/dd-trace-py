"""Real SDK model boundaries over a deterministic socket; no provider credentials."""

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from ddtrace.contrib.internal.openai._realtime import _usage_metrics
from ddtrace.contrib.internal.openai_agents import _realtime
from ddtrace.contrib.internal.openai_agents.patch import patch
from ddtrace.contrib.internal.openai_agents.patch import unpatch
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.llmobs._utils import assert_llmobs_span_data


sdk = pytest.importorskip("agents.realtime.openai_realtime")


class Socket:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.sent = []
        self.processed = None
        self.fail_send = False
        self.send_gate = None
        self.close_error = None
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.processed is not None and not self.processed.done():
            self.processed.set_result(None)
        value, self.processed = await self.queue.get()
        if value is None:
            raise StopAsyncIteration
        return json.dumps(value)

    async def feed(self, event):
        event.setdefault("event_id", "test-event")
        done = asyncio.get_running_loop().create_future()
        await self.queue.put((event, done))
        await asyncio.wait_for(done, 2)

    async def send(self, value):
        if self.send_gate:
            await self.send_gate.wait()
        if self.fail_send:
            raise RuntimeError("send failed")
        self.sent.append(json.loads(value))

    async def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


@pytest.fixture
def socket(monkeypatch):
    socket = Socket()

    async def connect(*args, **kwargs):
        return socket

    monkeypatch.setattr(sdk.websockets, "connect", connect)
    return socket


async def connect(socket, options=None):
    model = sdk.OpenAIRealtimeWebSocketModel()
    await model.connect({"api_key": "fake", "initial_model_settings": {"tracing": None}, **(options or {})})
    return model


def user_event(text):
    if hasattr(getattr(sdk, "_ConversionHelper", None), "convert_user_input_to_item_create"):
        return sdk._ConversionHelper.convert_user_input_to_item_create(sdk.RealtimeModelSendUserInput(user_input=text))
    return sdk.RealtimeModelSendRawMessage(
        message={
            "type": "conversation.item.create",
            "other_data": {
                "item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
            },
        }
    )


def created(response_id="r1"):
    return {"type": "response.created", "response": {"id": response_id, "object": "realtime.response"}}


def done(response_id="r1", output=None, usage=None):
    return {
        "type": "response.done",
        "response": {
            "id": response_id,
            "object": "realtime.response",
            "status": "completed",
            "model": "gpt-realtime",
            "output": output or [],
            "usage": usage,
        },
    }


def output_item(item_id="a1", transcript="hello", parts=None):
    return {
        "id": item_id,
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": parts or [{"type": "output_audio", "transcript": transcript}],
    }


def retrieved(item_id="a1", audio=b"\0\0" * 24, transcript="hello"):
    return {
        "type": "conversation.item.retrieved",
        "event_id": "server-generated-id",
        "item": {
            "id": item_id,
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_audio", "audio": base64.b64encode(audio).decode(), "transcript": transcript}],
        },
    }


def truncate_event(item_id, milliseconds):
    cls = getattr(sdk, "OpenAIConversationItemTruncateEvent", None)
    if cls:
        return cls(type="conversation.item.truncate", item_id=item_id, content_index=0, audio_end_ms=milliseconds)
    return sdk.RealtimeModelSendRawMessage(
        message={
            "type": "conversation.item.truncate",
            "other_data": {"item_id": item_id, "content_index": 0, "audio_end_ms": milliseconds},
        }
    )


async def bounded_output(socket, item_id="a1", milliseconds=100):
    await socket.feed(
        {"type": "conversation.item.truncated", "item_id": item_id, "content_index": 0, "audio_end_ms": milliseconds}
    )


def llm_span(test_spans):
    spans = [span for trace in test_spans.pop_traces() for span in trace]
    matches = [s for s in spans if s.resource == "createRealtimeResponse"]
    assert len(matches) == 1, [(s.resource, s.finished) for s in spans]
    return matches[0], spans


@pytest.mark.asyncio
async def test_realtime_websocket_payload(agents, openai_agents_llmobs, test_spans, socket):
    model = await connect(socket)
    await socket.feed(created())
    await socket.feed({"type": "response.output_text.done", "response_id": "r1", "text": "hello"})
    await socket.feed(done(usage={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}))
    await model.close()
    span, spans = llm_span(test_spans)
    assert len(spans) == 2
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name="gpt-realtime",
        model_provider="openai",
        output_messages=[{"role": "assistant", "content": "hello"}],
        metrics={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
    )


@pytest.mark.asyncio
async def test_realtime_send_success_failure_and_false(agents, openai_agents_llmobs, socket):
    model = await connect(socket)
    state = model._dd_realtime_state
    event = user_event("sent")
    if hasattr(model, "_send_raw_message_if"):
        assert await model._send_raw_message_if(event, lambda: False) is False
        assert not state._pending_input.text
        assert await model._send_raw_message_if(event, lambda: True) is True
    else:
        await model._send_raw_message(event)
    assert state._pending_input.text == "sent"
    socket.fail_send = True
    with pytest.raises(RuntimeError, match="send failed"):
        await model._send_raw_message(event)
    assert state._pending_input.text == "sent"
    await model.close()


@pytest.mark.asyncio
async def test_realtime_snapshot_send_before_await(agents, openai_agents_llmobs, socket):
    model = await connect(socket)
    event = user_event("original")
    socket.send_gate = asyncio.Event()
    task = asyncio.create_task(model._send_raw_message(event))
    await asyncio.sleep(0)
    if hasattr(event, "item"):
        event.item.content[0].text = "mutated"
    else:
        event.message["other_data"]["item"]["content"][0]["text"] = "mutated"
    socket.send_gate.set()
    await task
    assert model._dd_realtime_state._pending_input.text == "original"
    await model.close()


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_sideband_playback_and_retrieval(agents, openai_agents_llmobs, test_spans, socket):
    model = await connect(socket, {"call_id": "call-test"})
    state = model._dd_realtime_state
    assert state.sideband and state._session_id == "call-test"
    await socket.feed(created())
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    await socket.feed(done(output=[output_item()]))
    assert not [e for e in socket.sent if e["type"] == "conversation.item.retrieve"]
    await model._send_raw_message(
        sdk.OpenAIConversationItemRetrieveEvent(type="conversation.item.retrieve", item_id="a1", event_id="app-audio")
    )
    requests = [e for e in socket.sent if e["type"] == "conversation.item.retrieve"]
    assert len(requests) == 1
    assert "r1" in state.entries
    await socket.feed(retrieved())
    assert "r1" in state.entries
    await socket.feed({"type": "output_audio_buffer.stopped", "response_id": "r1"})
    await model.close()
    span, spans = llm_span(test_spans)
    data = _get_llmobs_data_metastruct(span)
    assert data["session_id"] == "call-test"
    assert data["meta"]["output"]["messages"][0]["audio_parts"][0]["mime_type"] == "audio/wav"
    assert_llmobs_span_data(
        data,
        span_kind="llm",
        model_provider="openai",
        model_name="gpt-realtime",
        output_messages=[{"role": "assistant", "content": "hello", "audio_parts": mock.ANY}],
    )
    assert len([s for s in spans if s.resource == "createRealtimeAgentSpeech"]) == 1
    assert not state.items and not state.requests and not state.tasks


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_explicit_url_controls_identity(agents, openai_agents_llmobs, socket):
    model = await connect(socket, {"call_id": "ignored", "url": "wss://api.openai.com/v1/realtime?call_id=effective"})
    assert model._dd_realtime_state._session_id == "effective"
    await model.close()


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_success_visible_and_owned_error_filtered(agents, openai_agents_llmobs, socket):
    model = await connect(socket, {"call_id": "call-test"})
    seen = []

    class Listener:
        async def on_event(self, event):
            if hasattr(event, "data"):
                seen.append(event.data)

    model.add_listener(Listener())
    await socket.feed(created())
    await socket.feed(done(output=[output_item()]))
    await bounded_output(socket)
    await asyncio.sleep(0)
    request = next(e for e in socket.sent if e["type"] == "conversation.item.retrieve")
    error = {
        "type": "error",
        "event_id": "server-error",
        "error": {
            "event_id": request["event_id"],
            "type": "invalid_request_error",
            "code": "missing",
            "message": "gone",
        },
    }
    await socket.feed(error)
    assert error not in seen
    reply = retrieved()
    await socket.feed(reply)
    assert reply in seen
    app_error = {**error, "error": {**error["error"], "event_id": "application-request"}}
    await socket.feed(app_error)
    assert app_error in seen
    await model.close()


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_application_listener_claims_before_fallback(agents, openai_agents_llmobs, socket):
    model = await connect(socket, {"call_id": "call-test"})
    # The SDK awaits raw listeners before parsing/transcription retrieval. The
    # listener's send is allowed to suspend for arbitrarily many loop turns.
    gate = asyncio.Event()
    started = asyncio.Event()

    class Listener:
        async def on_event(self, event):
            if getattr(event, "data", {}).get("type") == "conversation.item.input_audio_transcription.completed":
                started.set()
                await gate.wait()
                await model._send_raw_message(
                    sdk.OpenAIConversationItemRetrieveEvent(
                        type="conversation.item.retrieve", item_id="u1", event_id="app-user"
                    )
                )

    model.add_listener(Listener())
    await socket.feed({"type": "input_audio_buffer.speech_started", "item_id": "u1", "audio_start_ms": 0})
    await socket.feed({"type": "input_audio_buffer.speech_stopped", "item_id": "u1", "audio_end_ms": 100})
    await socket.feed({"type": "input_audio_buffer.committed", "item_id": "u1", "previous_item_id": ""})
    transcript = {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": "u1",
        "event_id": "e1",
        "content_index": 0,
        "transcript": "hi",
        "usage": {"type": "duration", "seconds": 1},
    }
    feeding = asyncio.create_task(socket.feed(transcript))
    await started.wait()
    await asyncio.sleep(0)
    assert not [e for e in socket.sent if e["type"] == "conversation.item.retrieve"]
    gate.set()
    await feeding
    await asyncio.sleep(0)
    assert len([e for e in socket.sent if e["type"] == "conversation.item.retrieve"]) == 1
    await model.close()


@pytest.mark.asyncio
async def test_realtime_close_cancel_before_teardown(agents, openai_agents_llmobs, socket, monkeypatch):
    model = await connect(socket)
    if not hasattr(model, "_cancel_response_create_tasks"):
        await model.close()
        pytest.skip("SDK predates cancellable response-create teardown")
    original = model._cancel_response_create_tasks
    gate = asyncio.Event()
    monkeypatch.setattr(model, "_cancel_response_create_tasks", gate.wait)
    close = asyncio.create_task(model.close())
    await asyncio.sleep(0)
    close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close
    assert model._websocket is socket
    assert not model._dd_realtime_state._closed
    monkeypatch.setattr(model, "_cancel_response_create_tasks", original)
    await socket.feed(created())
    await model.close()
    assert model._dd_realtime_state._closed


@pytest.mark.asyncio
async def test_realtime_eof_finalizes_once(agents, openai_agents_llmobs, test_spans, socket):
    model = await connect(socket)
    await socket.feed(created())
    await socket.queue.put((None, None))
    await model._websocket_task
    state = model._dd_realtime_state
    assert state._closed
    await model.close()
    llm_span(test_spans)
    assert not state.entries


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_reconnect_and_failed_override(agents, openai_agents_llmobs, socket, monkeypatch):
    model = await connect(socket, {"call_id": "first"})
    old = model._dd_realtime_state
    await model.close()
    socket.closed = False
    await model.connect({"api_key": "fake", "initial_model_settings": {"tracing": None}, "call_id": "second"})
    assert model._dd_realtime_state is not old
    assert model._dd_realtime_state._session_id == "second"
    await model.close()
    assert not old.items


@pytest.mark.parametrize("setting", ["false", "0"])
@pytest.mark.asyncio
async def test_realtime_disabled(agents, openai_agents_llmobs, socket, monkeypatch, setting):
    monkeypatch.setenv("DD_OPENAI_REALTIME_ENABLED", setting)
    model = await connect(socket)
    assert getattr(model, "_dd_realtime_state", None) is None
    await model.close()


@pytest.mark.asyncio
async def test_realtime_llmobs_disabled(agents, socket):
    model = await connect(socket)
    assert getattr(model, "_dd_realtime_state", None) is None
    await model.close()


@pytest.mark.asyncio
async def test_realtime_patch_unpatch_repatch(agents, openai_agents_llmobs, socket):
    patch()
    method = sdk.OpenAIRealtimeWebSocketModel.connect
    assert hasattr(method, "__wrapped__")
    assert not hasattr(method.__wrapped__, "__wrapped__")
    unpatch()
    assert not hasattr(sdk.OpenAIRealtimeWebSocketModel.connect, "__wrapped__")
    patch()
    model = await connect(socket)
    await model.close()


@pytest.mark.parametrize("value", [True, False, -1, 1.5, "2", float("nan"), 1 << 53])
@pytest.mark.asyncio
async def test_realtime_usage_invalid(value):
    assert (
        _usage_metrics(
            {
                "input_tokens": value,
                "output_tokens": value,
                "total_tokens": value,
                "input_token_details": {"audio_tokens": value},
            }
        )
        is None
    )


@pytest.mark.asyncio
async def test_realtime_usage_inclusive_zero_and_cache():
    assert _usage_metrics(
        {
            "input_tokens": 5000,
            "output_tokens": 1300,
            "total_tokens": 6300,
            "input_token_details": {
                "audio_tokens": 4800,
                "text_tokens": 200,
                "cached_tokens": 4000,
                "cached_tokens_details": {"audio_tokens": 3900, "text_tokens": 100},
            },
            "output_token_details": {"audio_tokens": 1200, "text_tokens": 0},
        }
    ) == {
        "input_tokens": 5000,
        "output_tokens": 1300,
        "total_tokens": 6300,
        "input_audio_tokens": 4800,
        "input_text_tokens": 200,
        "cache_read_input_tokens": 4000,
        "cache_audio_read_tokens": 3900,
        "cache_text_read_tokens": 100,
        "output_audio_tokens": 1200,
        "output_text_tokens": 0,
    }


@pytest.mark.parametrize("retrieval", [True, False])
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_partial_or_oversize_clip_keeps_transcript(
    agents, openai_agents_llmobs, test_spans, socket, monkeypatch, retrieval
):
    monkeypatch.setenv("DD_OPENAI_REALTIME_SIDEBAND_AUDIO_RETRIEVAL_ENABLED", str(retrieval).lower())
    monkeypatch.setattr(_realtime, "_AUDIO_ACCUM_MAX_BYTES", 8)
    model = await connect(socket, {"call_id": "bounded"})
    await socket.feed(created())
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    await socket.feed(done(output=[output_item()]))
    await asyncio.sleep(0)
    await socket.feed(retrieved(audio=b"\0" * 10))
    await socket.feed({"type": "output_audio_buffer.stopped", "response_id": "r1"})
    await model.close()
    span, _ = llm_span(test_spans)
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span), span_kind="llm", output_messages=[{"role": "assistant", "content": "hello"}]
    )
    assert not model._dd_realtime_state.items


@pytest.mark.parametrize("size,has_audio", [(8, True), (10, False)])
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_audio_byte_boundary(
    agents, openai_agents_llmobs, test_spans, socket, monkeypatch, size, has_audio
):
    monkeypatch.setattr(_realtime, "_AUDIO_ACCUM_MAX_BYTES", 8)
    model = await connect(socket, {"call_id": "bounded"})
    await socket.feed(created())
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    await socket.feed(done(output=[output_item()]))
    await socket.feed(retrieved(audio=b"\0" * size))
    await socket.feed({"type": "output_audio_buffer.stopped", "response_id": "r1"})
    await model.close()
    span, _ = llm_span(test_spans)
    message = _get_llmobs_data_metastruct(span)["meta"]["output"]["messages"][0]
    assert bool(message.get("audio_parts")) is has_audio


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_multipart_truncation_discards_later_parts(agents, openai_agents_llmobs, test_spans, socket):
    model = await connect(socket, {"call_id": "truncated"})
    await socket.feed(created())
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    await socket.feed(done(output=[output_item("a1"), output_item("a2", "unheard")]))
    await socket.feed(retrieved("a2", b"\2\0" * 48, "unheard"))
    await socket.feed({"type": "conversation.item.truncated", "item_id": "a1", "content_index": 0, "audio_end_ms": 1})
    await socket.feed(retrieved("a1", b"\1\0" * 48, "heard"))
    await socket.feed({"type": "output_audio_buffer.cleared", "response_id": "r1"})
    await model.close()
    span, _ = llm_span(test_spans)
    message = _get_llmobs_data_metastruct(span)["meta"]["output"]["messages"][0]
    assert message["content"] == "heard"
    raw = base64.b64decode(message["audio_parts"][0]["content"])
    assert raw[-48:] == b"\1\0" * 24
    assert len(raw) == 44 + 48


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_missing_item_omits_whole_clip(agents, openai_agents_llmobs, test_spans, socket):
    model = await connect(socket, {"call_id": "partial"})
    await socket.feed(created())
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    await socket.feed(done(output=[output_item("a1"), output_item("a2")]))
    await socket.feed(retrieved("a1"))
    await socket.feed({"type": "output_audio_buffer.stopped", "response_id": "r1"})
    await model.close()
    span, _ = llm_span(test_spans)
    message = _get_llmobs_data_metastruct(span)["meta"]["output"]["messages"][0]
    assert "audio_parts" not in message


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_failed_retrieval_not_resurrected(agents, openai_agents_llmobs, socket):
    model = await connect(socket, {"call_id": "failure"})
    await socket.feed(created())
    await socket.feed(done(output=[output_item("a1"), output_item("a2")]))
    await bounded_output(socket)
    await asyncio.sleep(0)
    request = next(e for e in socket.sent if e.get("item_id") == "a1")
    await socket.feed({"type": "error", "error": {"event_id": request["event_id"], "message": "gone"}})
    await socket.feed({"type": "response.output_item.done", "response_id": "r1", "item": output_item("a1")})
    await asyncio.sleep(0)
    assert len([e for e in socket.sent if e.get("item_id") == "a1"]) == 1
    await model.close()


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_retrieval_disabled_does_not_send(agents, openai_agents_llmobs, socket, monkeypatch):
    monkeypatch.setenv("DD_OPENAI_REALTIME_SIDEBAND_AUDIO_RETRIEVAL_ENABLED", "false")
    model = await connect(socket, {"call_id": "disabled"})
    await socket.feed(created())
    await socket.feed(done(output=[output_item()]))
    await asyncio.sleep(0)
    assert not [e for e in socket.sent if e["type"] == "conversation.item.retrieve"]
    assert not model._dd_realtime_state.items["a1"].wanted
    await model.close()


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_turn_limit_releases_evicted_data(agents, openai_agents_llmobs, socket, monkeypatch):
    monkeypatch.setattr(_realtime, "_MAX_TURNS", 2)
    model = await connect(socket, {"call_id": "bounded"})
    for index in range(3):
        await socket.feed(created(str(index)))
        await socket.feed(done(str(index), [output_item(str(index))]))
        await socket.feed(retrieved(str(index)))
    state = model._dd_realtime_state
    assert len(state.entries) == 2
    assert "0" not in state.items
    await model.close()
    assert not state.entries and not state.items and state._cache_bytes() == 0


@pytest.mark.asyncio
async def test_realtime_transcription_timer_without_next_event(
    agents, openai_agents_llmobs, test_spans, socket, monkeypatch
):
    monkeypatch.setattr(_realtime, "_TRANSCRIPT_WAIT", 0.01)
    model = await connect(socket)
    state = model._dd_realtime_state
    state._input_transcription_enabled = True
    expired = asyncio.Event()
    transcript_timeout = state._transcript_timeout

    def on_timeout(entry: _realtime._Playback) -> None:
        transcript_timeout(entry)
        expired.set()

    monkeypatch.setattr(state, "_transcript_timeout", on_timeout)
    await socket.feed({"type": "input_audio_buffer.committed", "item_id": "u1", "previous_item_id": ""})
    await socket.feed(created())
    await socket.feed(done())
    await asyncio.wait_for(expired.wait(), 2)
    assert not state.entries
    await model.close()
    llm_span(test_spans)


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_cache_admission_before_decode(agents, openai_agents_llmobs, socket, monkeypatch):
    monkeypatch.setattr(_realtime, "_MAX_CACHE_BYTES", 4)
    model = await connect(socket, {"call_id": "bounded"})
    await socket.feed(created())
    await socket.feed(done(output=[output_item()]))
    with mock.patch.object(_realtime.base64, "b64decode", side_effect=AssertionError("decoded before admission")):
        await socket.feed(retrieved(audio=b"\0" * 10))
    state = model._dd_realtime_state
    assert not state.items["a1"].parts
    assert state._cache_bytes() == 0
    await model.close()


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_shared_playback_window(agents, openai_agents_llmobs, test_spans, socket, monkeypatch):
    now = [1_000_000_000]
    monkeypatch.setattr(_realtime.time, "time_ns", lambda: now[0])
    model = await connect(socket, {"call_id": "shared"})
    await socket.feed(created("r1"))
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    await socket.feed(done("r1", [output_item("a1")]))
    await socket.feed(retrieved("a1"))
    await socket.feed(created("r2"))
    await socket.feed(done("r2", [output_item("a2")]))
    await socket.feed(retrieved("a2"))
    now[0] += 2_000_000
    await socket.feed({"type": "output_audio_buffer.stopped", "response_id": "r2"})
    await model.close()
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    llms = [s for s in spans if s.resource == "createRealtimeResponse"]
    assert len(llms) == 2
    for span in llms:
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span), span_kind="llm", metadata={"agent_speech_timing": "audio_duration"}
        )
    assert len([s for s in spans if s.resource == "createRealtimeAgentSpeech"]) == 2


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_runner_tools_with_openai_also_patched(agents, openai, openai_agents_llmobs, test_spans, socket):
    from agents.realtime import RealtimeAgent
    from agents.realtime import RealtimeRunner

    completed = asyncio.Event()

    @agents.function_tool
    async def weather(city: str) -> str:
        """Return the weather for a city."""
        completed.set()
        return "sunny in " + city

    model = sdk.OpenAIRealtimeWebSocketModel()
    runner = RealtimeRunner(
        RealtimeAgent(name="Weather", tools=[weather]), model=model, config={"tracing_disabled": True}
    )
    async with await runner.run(model_config={"api_key": "fake"}) as session:
        await session.send_message("weather in Paris")
        await socket.feed(created())
        tool = {
            "type": "function_call",
            "id": "item-tool",
            "call_id": "call-weather",
            "name": "weather",
            "arguments": '{"city":"Paris"}',
            "status": "completed",
        }
        await socket.feed({"type": "response.output_item.done", "response_id": "r1", "output_index": 0, "item": tool})
        await asyncio.wait_for(completed.wait(), 2)
        await socket.feed(done(output=[tool]))
        for _ in range(100):
            if any(e.get("item", {}).get("type") == "function_call_output" for e in socket.sent):
                break
            await asyncio.sleep(0.001)
        assert any(e.get("item", {}).get("call_id") == "call-weather" for e in socket.sent)
        await socket.feed(created("r2"))
        await socket.feed({"type": "response.output_text.done", "response_id": "r2", "text": "sunny"})
        await socket.feed(done("r2"))
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    llms = [s for s in spans if s.resource == "createRealtimeResponse"]
    assert len(llms) == 2
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(llms[0]),
        span_kind="llm",
        output_messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"name": "weather", "arguments": {"city": "Paris"}, "tool_id": "call-weather", "type": "function"}
                ],
            }
        ],
    )
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(llms[1]),
        span_kind="llm",
        input_messages=[
            {
                "role": "user",
                "content": "",
                "tool_results": [
                    {
                        "name": "weather",
                        "result": "sunny in Paris",
                        "tool_id": "call-weather",
                        "type": "function_call_output",
                    }
                ],
            }
        ],
    )


@pytest.mark.subprocess(env={"DD_LLMOBS_ENABLED": "false"})
def test_realtime_fresh_auto_import():
    from agents.realtime.openai_realtime import OpenAIRealtimeWebSocketModel

    import ddtrace.auto  # noqa: F401

    assert hasattr(OpenAIRealtimeWebSocketModel.connect, "__wrapped__")
    assert hasattr(OpenAIRealtimeWebSocketModel._handle_ws_event, "__wrapped__")


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.parametrize(
    "codec,rate,expected_bytes", [("audio/pcm", 24000, 48), ("audio/pcmu", 8000, 96), ("audio/pcma", 8000, 96)]
)
@pytest.mark.asyncio
async def test_realtime_format_snapshot(agents, openai_agents_llmobs, test_spans, socket, codec, rate, expected_bytes):
    model = await connect(socket, {"call_id": "codec"})
    event = created()
    event["response"]["audio"] = {"output": {"format": {"type": codec, "rate": rate}}}
    await socket.feed(event)
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    await socket.feed(done(output=[output_item()]))
    await socket.feed(
        {"type": "session.updated", "session": {"audio": {"output": {"format": {"type": "audio/pcm", "rate": 48000}}}}}
    )
    await socket.feed(retrieved(audio=b"\0" * 48))
    await socket.feed({"type": "output_audio_buffer.stopped", "response_id": "r1"})
    await model.close()
    span, _ = llm_span(test_spans)
    part = _get_llmobs_data_metastruct(span)["meta"]["output"]["messages"][0]["audio_parts"][0]
    raw = base64.b64decode(part["content"])
    assert len(raw) == 44 + expected_bytes
    assert int.from_bytes(raw[24:28], "little") == rate


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_cancels_owned_retrieval(agents, openai_agents_llmobs, socket):
    model = await connect(socket, {"call_id": "cancel"})
    socket.send_gate = asyncio.Event()
    await socket.feed(created())
    await socket.feed(done(output=[output_item()]))
    await bounded_output(socket)
    await asyncio.sleep(0)
    tasks = tuple(model._dd_realtime_state.tasks)
    assert tasks
    await model.close()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert all(t.cancelled() for t in tasks)
    assert not model._dd_realtime_state.requests


@pytest.mark.asyncio
async def test_realtime_failed_connect_preserves_exception(agents, openai_agents_llmobs, socket, monkeypatch):
    error = RuntimeError("connect failed")

    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(sdk.websockets, "connect", fail)
    model = sdk.OpenAIRealtimeWebSocketModel()
    with pytest.raises(RuntimeError) as raised:
        await model.connect({"api_key": "fake", "url": "wss://api.openai.com/v1/realtime?call_id=failed"})
    assert raised.value is error
    assert model._dd_realtime_state._closed
    assert not model._dd_realtime_state.entries


@pytest.mark.asyncio
async def test_realtime_close_error_after_teardown(agents, openai_agents_llmobs, test_spans, socket):
    model = await connect(socket)
    if not hasattr(model, "_reset_connection_state"):
        await model.close()
        pytest.skip("SDK predates close error cleanup")
    await socket.feed(created())
    socket.close_error = RuntimeError("close failed")
    with pytest.raises(RuntimeError, match="close failed"):
        await model.close()
    assert model._dd_realtime_state._closed
    llm_span(test_spans)


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_response_order_overrides_event_arrival(agents, openai_agents_llmobs, test_spans, socket):
    model = await connect(socket, {"call_id": "ordered"})
    await socket.feed(created())
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    for index, item_id in [(1, "a2"), (0, "a1")]:
        await socket.feed(
            {
                "type": "response.output_item.added",
                "response_id": "r1",
                "output_index": index,
                "item": output_item(item_id),
            }
        )
    await socket.feed(retrieved("a2", b"\2\0", "second"))
    await socket.feed(retrieved("a1", b"\1\0", "first"))
    await socket.feed(done(output=[output_item("a1", "first"), output_item("a2", "second")]))
    await socket.feed({"type": "output_audio_buffer.stopped", "response_id": "r1"})
    await model.close()
    span, _ = llm_span(test_spans)
    message = _get_llmobs_data_metastruct(span)["meta"]["output"]["messages"][0]
    assert message["content"] == "firstsecond"
    assert base64.b64decode(message["audio_parts"][0]["content"])[44:] == b"\1\0\2\0"


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
async def test_realtime_provisional_snapshot_omits_audio(agents, openai_agents_llmobs, test_spans, socket):
    model = await connect(socket, {"call_id": "review-partial"})
    await socket.feed(created())
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    item = output_item()
    item["status"] = "in_progress"
    await socket.feed({"type": "response.output_item.added", "response_id": "r1", "output_index": 0, "item": item})
    reply = retrieved(audio=b"\1\0" * 24, transcript="partial")
    reply["item"]["status"] = "in_progress"
    await socket.feed(reply)
    await socket.feed(done(output=[output_item(transcript="full transcript")]))
    await socket.feed({"type": "output_audio_buffer.stopped", "response_id": "r1"})
    await model.close()
    span, _ = llm_span(test_spans)
    message = _get_llmobs_data_metastruct(span)["meta"]["output"]["messages"][0]
    assert not message.get("audio_parts"), "an in-progress snapshot was exported as the complete final clip"


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
async def test_realtime_bounds_backpressured_retrieval_tasks(agents, openai_agents_llmobs, socket, monkeypatch):
    monkeypatch.setattr(_realtime, "_MAX_TURNS", 2)
    model = await connect(socket, {"call_id": "review-tasks"})
    socket.send_gate = asyncio.Event()
    try:
        for index in range(7):
            await socket.feed(created(str(index)))
            await socket.feed(done(str(index), [output_item(str(index))]))
            await bounded_output(socket, str(index))
            await asyncio.sleep(0)
        assert len(model._dd_realtime_state.tasks) == 2, "evicted items leave backpressured retrieval tasks running"
    finally:
        tasks = tuple(model._dd_realtime_state.tasks)
        await model.close()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_realtime_websocket_holds_for_late_truncation(agents, openai_agents_llmobs, test_spans, socket):
    model = await connect(socket)
    settings = model._get_session_config({})
    config = settings.model_dump(exclude_none=True)
    config.update(id="session-test", object="realtime.session")
    await socket.feed({"type": "session.created", "session": config})
    raw = b"\0\0" * 96000
    for response_id in ("warmup", "actual"):
        await socket.feed(created(response_id))
        await socket.feed(
            {
                "type": "response.output_audio.delta"
                if hasattr(sdk, "OpenAIRealtimeSIPModel")
                else "response.audio.delta",
                "response_id": response_id,
                "item_id": response_id,
                "output_index": 0,
                "content_index": 0,
                "delta": base64.b64encode(raw).decode(),
            }
        )
        if response_id == "warmup":
            await model._send_raw_message(truncate_event(response_id, 0))
        await socket.feed(done(response_id))
    try:
        assert "actual" in model._dd_realtime_state.entries
        await model._send_raw_message(truncate_event("actual", 100))
        await socket.feed(
            {"type": "conversation.item.truncated", "item_id": "actual", "content_index": 0, "audio_end_ms": 100}
        )
    finally:
        await model.close()
    spans = [span for trace in test_spans.pop_traces() for span in trace]
    llms = [span for span in spans if span.resource == "createRealtimeResponse"]
    assert len(llms) == 2
    message = _get_llmobs_data_metastruct(llms[-1])["meta"]["output"]["messages"][0]
    assert len(base64.b64decode(message["audio_parts"][0]["content"])) == 44 + 4800
    speech = [span for span in spans if span.resource == "createRealtimeAgentSpeech"]
    assert speech[-1].duration == pytest.approx(0.1, abs=1e-6)


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
async def test_realtime_manual_sideband_speech_precedes_commit(
    agents, openai_agents_llmobs, test_spans, socket, monkeypatch
):
    now = [10_000_000_000]
    monkeypatch.setattr(_realtime.time, "time_ns", lambda: now[0])
    model = await connect(socket, {"call_id": "review-manual"})
    await socket.feed({"type": "input_audio_buffer.committed", "item_id": "u1", "previous_item_id": ""})
    await socket.feed(created())
    now[0] = 12_000_000_000
    reply = retrieved("u1", b"\0\0" * 24000, "hello")
    reply["item"]["role"] = "user"
    reply["item"]["content"][0]["type"] = "input_audio"
    await socket.feed(reply)
    now[0] = 13_000_000_000
    await socket.feed(done())
    await model.close()
    _, spans = llm_span(test_spans)
    speech = next(s for s in spans if s.resource == "createRealtimeUserSpeech")
    assert speech.start_ns == 9_000_000_000, (
        "manual caller speech used retrieval arrival instead of commit minus duration"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
async def test_realtime_late_owned_error_is_filtered_after_eviction(agents, openai_agents_llmobs, socket):
    model = await connect(socket, {"call_id": "review-late-error"})
    seen = []

    class Listener:
        async def on_event(self, event):
            if hasattr(event, "data"):
                seen.append(event.data)

    model.add_listener(Listener())
    await socket.feed(created())
    await socket.feed(done(output=[output_item()]))
    await bounded_output(socket)
    await asyncio.sleep(0)
    request = next(e for e in socket.sent if e["type"] == "conversation.item.retrieve")
    model._dd_realtime_state._release(model._dd_realtime_state.entries["r1"], True)
    error = {
        "type": "error",
        "event_id": "late-server-error",
        "error": {
            "event_id": request["event_id"],
            "type": "invalid_request_error",
            "code": "missing",
            "message": "gone",
        },
    }
    await socket.feed(error)
    await model.close()
    assert error not in seen, "a late tracer-owned request error escaped after its item was evicted"


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
async def test_realtime_item_admission_after_owner_eviction(
    agents, openai_agents_llmobs, test_spans, socket, monkeypatch
):
    monkeypatch.setattr(_realtime, "_MAX_ITEMS", 2)
    model = await connect(socket, {"call_id": "review-item-limit"})
    await socket.feed(created())
    # Three items establishes the orphan; further admission would make the
    # current eviction loop spin. Stop before that unbounded loop in this probe.
    await socket.feed(done(output=[output_item(str(i), str(i)) for i in range(3)]))
    try:
        state = model._dd_realtime_state
        assert all(i.entry is None or i.entry.id in state.entries for i in state.items.values()), (
            "an item was admitted after its owner was evicted"
        )
    finally:
        await model.close()
    span, _ = llm_span(test_spans)
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span), span_kind="llm", output_messages=[{"role": "assistant", "content": "012"}]
    )


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.parametrize("audio_tokens", [None, 20, 1000000])
async def test_realtime_unsafe_retrieval_keeps_transport_open(agents, openai_agents_llmobs, audio_tokens, monkeypatch):
    import json

    from websockets.asyncio.server import serve
    from websockets.exceptions import ConnectionClosed

    websocket_connect = sdk.websockets.connect

    async def connect_with_limit(*args: Any, **kwargs: Any) -> Any:
        # Older SDKs ignore max_size in TransportConfig and use an unlimited socket.
        kwargs["max_size"] = 2048
        return await websocket_connect(*args, **kwargs)

    monkeypatch.setattr(sdk.websockets, "connect", connect_with_limit)
    requested = asyncio.Event()
    closed = asyncio.Event()
    codes = []

    async def handler(ws):
        produced = False
        try:
            async for payload in ws:
                event = json.loads(payload)
                if event.get("type") == "session.update" and not produced:
                    produced = True
                    start = created()
                    start["event_id"] = "created"
                    finish = done(
                        output=[output_item()], usage={"output_token_details": {"audio_tokens": audio_tokens}}
                    )
                    finish["event_id"] = "done"
                    await ws.send(json.dumps(start))
                    await ws.send(json.dumps(finish))
                elif event.get("type") == "conversation.item.retrieve":
                    requested.set()
                    await ws.send(json.dumps(retrieved(audio=b"\0" * 3000)))
        except ConnectionClosed as exc:
            codes.append(exc.rcvd.code if exc.rcvd else exc.sent.code if exc.sent else None)
        finally:
            closed.set()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        model = sdk.OpenAIRealtimeWebSocketModel()
        await model.connect(
            {
                "api_key": "fake",
                "call_id": "bounded",
                "url": f"ws://127.0.0.1:{port}?call_id=bounded",
                "initial_model_settings": {"tracing": None},
            }
        )
        try:
            try:
                await asyncio.wait_for(requested.wait(), 0.2)
            except asyncio.TimeoutError:
                pass
            if requested.is_set():
                await asyncio.wait_for(closed.wait(), 2)
            assert not closed.is_set(), f"tracer retrieval closed the application transport with code(s) {codes}"
        finally:
            await model.close()


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.parametrize("bounded", [True, False])
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.asyncio
async def test_realtime_application_retrieval_error_releases_pending(
    agents, openai_agents_llmobs, test_spans, socket, monkeypatch, bounded, enabled
):
    monkeypatch.setenv("DD_OPENAI_REALTIME_SIDEBAND_AUDIO_RETRIEVAL_ENABLED", str(enabled).lower())
    model = await connect(socket, {"call_id": "app-error"})
    seen = []

    class Listener:
        async def on_event(self, event):
            data = getattr(event, "data", {})
            seen.append(data)
            if data.get("type") == "conversation.item.input_audio_transcription.completed":
                await model._send_raw_message(
                    sdk.OpenAIConversationItemRetrieveEvent(
                        type="conversation.item.retrieve", item_id="u1", event_id="app-user"
                    )
                )

    model.add_listener(Listener())
    if bounded:
        await socket.feed({"type": "input_audio_buffer.speech_started", "item_id": "u1", "audio_start_ms": 0})
        await socket.feed({"type": "input_audio_buffer.speech_stopped", "item_id": "u1", "audio_end_ms": 100})
    await socket.feed({"type": "input_audio_buffer.committed", "item_id": "u1", "previous_item_id": ""})
    await socket.feed(created())
    await socket.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "u1",
            "content_index": 0,
            "transcript": "hi",
            "usage": {"type": "duration", "seconds": 0.1},
        }
    )
    await socket.feed(done())
    requests = [e for e in socket.sent if e["type"] == "conversation.item.retrieve"]
    assert len(requests) == 1 and requests[0]["event_id"] == "app-user"
    error = {
        "type": "error",
        "error": {"type": "invalid_request_error", "code": "missing", "event_id": "app-user", "message": "gone"},
    }
    await socket.feed(error)
    assert error in seen
    await asyncio.sleep(0)
    requests = [e for e in socket.sent if e["type"] == "conversation.item.retrieve"]
    assert len(requests) == (2 if bounded and enabled else 1)
    if bounded and enabled:
        reply = retrieved("u1", b"\0\0" * 24, "hi")
        reply["item"]["role"] = "user"
        reply["item"]["content"][0]["type"] = "input_audio"
        await socket.feed(reply)
    assert not model._dd_realtime_state.entries
    await model.close()
    span, _ = llm_span(test_spans)
    assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind="llm")


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_reply_before_send_completion_does_not_rearm_pending(
    agents, openai_agents_llmobs, test_spans, socket, monkeypatch
):
    model = await connect(socket, {"call_id": "reply-race"})
    await socket.feed(created())
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    await socket.feed(done(output=[output_item()]))
    replied = asyncio.Event()
    gate = asyncio.Event()

    async def send_then_drain(value):
        socket.sent.append(json.loads(value))
        await socket.feed(retrieved())
        replied.set()
        await gate.wait()

    monkeypatch.setattr(socket, "send", send_then_drain)
    task = asyncio.create_task(
        model._send_raw_message(
            sdk.OpenAIConversationItemRetrieveEvent(
                type="conversation.item.retrieve", item_id="a1", event_id="app-audio"
            )
        )
    )
    await asyncio.wait_for(replied.wait(), 2)
    await socket.feed({"type": "output_audio_buffer.stopped", "response_id": "r1"})
    assert model._dd_realtime_state.entries
    gate.set()
    await task
    assert not model._dd_realtime_state.entries
    await model.close()
    llm_span(test_spans)


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.parametrize("field", ["max_size", "max_message_size"])
@pytest.mark.parametrize("limit,expected", [(1000, 0), (20000, 1)])
@pytest.mark.asyncio
async def test_realtime_retrieval_respects_protocol_limit_properties(
    agents, openai_agents_llmobs, socket, field, limit, expected
):
    model = await connect(socket, {"call_id": "protocol-limit"})
    socket.protocol = SimpleNamespace(**{field: limit})
    await socket.feed(created())
    await socket.feed(done(output=[output_item()]))
    await bounded_output(socket, milliseconds=1)
    await asyncio.sleep(0)
    assert len([e for e in socket.sent if e["type"] == "conversation.item.retrieve"]) == expected
    await model.close()


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.parametrize("mime,wave_bytes", [("audio/pcm", 44 + 48), ("audio/pcmu", 44 + 16)])
@pytest.mark.parametrize("over", [False, True])
@pytest.mark.asyncio
async def test_realtime_retrieval_admits_final_encoded_wav_budget(
    agents, openai_agents_llmobs, socket, monkeypatch, mime, wave_bytes, over
):
    budget = 4 * ((wave_bytes + 2) // 3)
    monkeypatch.setattr(_realtime, "_AUDIO_ACCUM_MAX_BYTES", budget - int(over))
    model = await connect(socket, {"call_id": "media-limit"})
    event = created()
    event["response"]["audio"] = {"output": {"format": {"type": mime}}}
    await socket.feed(event)
    await socket.feed(done(output=[output_item()]))
    await bounded_output(socket, milliseconds=1)
    await asyncio.sleep(0)
    assert len([e for e in socket.sent if e["type"] == "conversation.item.retrieve"]) == int(not over)
    await model.close()


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.parametrize("transcript", ["final", ""])
@pytest.mark.asyncio
async def test_realtime_provisional_snapshot_refreshes_after_bounded_completion(
    agents, openai_agents_llmobs, test_spans, socket, transcript
):
    model = await connect(socket, {"call_id": "refresh"})
    await socket.feed(created())
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    item = output_item()
    item["status"] = "in_progress"
    await socket.feed({"type": "response.output_item.added", "response_id": "r1", "output_index": 0, "item": item})
    partial = retrieved(audio=b"\1\0" * 1200, transcript="partial")
    partial["item"]["status"] = "in_progress"
    await socket.feed(partial)
    assert not [e for e in socket.sent if e["type"] == "conversation.item.retrieve"]
    await socket.feed(done(output=[output_item(transcript="generated full text")]))
    await bounded_output(socket, milliseconds=100)
    await asyncio.sleep(0)
    assert len([e for e in socket.sent if e["type"] == "conversation.item.retrieve"]) == 1
    await socket.feed(retrieved(audio=b"\2\0" * 2400, transcript=transcript))
    await socket.feed({"type": "output_audio_buffer.cleared", "response_id": "r1"})
    await model.close()
    span, _ = llm_span(test_spans)
    message = _get_llmobs_data_metastruct(span)["meta"]["output"]["messages"][0]
    assert message["content"] == transcript
    assert base64.b64decode(message["audio_parts"][0]["content"])[44:] == b"\2\0" * 2400


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.asyncio
async def test_realtime_manual_inflight_close_root_contains_generation(
    agents, openai_agents_llmobs, test_spans, socket, monkeypatch
):
    now = [10_000_000_000]
    monkeypatch.setattr(_realtime.time, "time_ns", lambda: now[0])
    model = await connect(socket, {"call_id": "manual-inflight"})
    await socket.feed({"type": "input_audio_buffer.committed", "item_id": "u1", "previous_item_id": ""})
    await socket.feed(created())
    now[0] = 12_000_000_000
    reply = retrieved("u1", b"\0\0" * 24000, "hi")
    reply["item"]["role"] = "user"
    reply["item"]["content"][0]["type"] = "input_audio"
    await socket.feed(reply)
    await model.close()
    span, spans = llm_span(test_spans)
    root = next(s for s in spans if s.resource == "createRealtimeTurn")
    assert root.start_ns == 9_000_000_000
    assert root.start_ns + root.duration_ns >= span.start_ns + span.duration_ns - 1000
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        input_messages=[{"role": "user", "content": "hi", "audio_parts": mock.ANY}],
    )


@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.parametrize("older_snapshot", [True, False])
@pytest.mark.asyncio
async def test_realtime_final_input_transcript_survives_inflight_close(
    agents, openai_agents_llmobs, test_spans, socket, older_snapshot
):
    model = await connect(socket, {"call_id": "final-input"})
    await socket.feed({"type": "input_audio_buffer.committed", "item_id": "u1", "previous_item_id": ""})
    await socket.feed(created())
    await socket.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "u1",
            "content_index": 0,
            "transcript": "known final",
            "usage": {"type": "duration", "seconds": 0.1},
        }
    )
    if older_snapshot:
        reply = retrieved("u1", b"\0\0" * 24, "")
        reply["item"]["role"] = "user"
        reply["item"]["content"][0]["type"] = "input_audio"
        await socket.feed(reply)
    await model.close()
    span, spans = llm_span(test_spans)
    expected = {"role": "user", "content": "known final"}
    if older_snapshot:
        expected["audio_parts"] = mock.ANY
    assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind="llm", input_messages=[expected])
    root = next(s for s in spans if s.resource == "createRealtimeTurn")
    assert_llmobs_span_data(_get_llmobs_data_metastruct(root), span_kind="workflow", input_value="known final")


@pytest.mark.asyncio
async def test_realtime_websocket_inflight_close_preserves_transcript(agents, openai_agents_llmobs, test_spans, socket):
    model = await connect(socket)
    await socket.feed({"type": "input_audio_buffer.committed", "item_id": "u1", "previous_item_id": ""})
    await socket.feed(created())
    await socket.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "u1",
            "content_index": 0,
            "transcript": "known final",
            "usage": {"type": "duration", "seconds": 0.1},
        }
    )
    await model.close()
    span, _ = llm_span(test_spans)
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span), span_kind="llm", input_messages=[{"role": "user", "content": "known final"}]
    )


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.parametrize("invalid", ["cache", "malformed", "oversize"])
async def test_realtime_rejected_bounded_reply_does_not_retry(
    agents, openai_agents_llmobs, socket, monkeypatch, invalid
):
    model = await connect(socket, {"call_id": "rejected"})
    await socket.feed(created())
    await socket.feed(done(output=[output_item()]))
    await bounded_output(socket)
    await asyncio.sleep(0)
    requests = [e for e in socket.sent if e["type"] == "conversation.item.retrieve"]
    assert len(requests) == 1
    reply = retrieved()
    if invalid == "cache":
        monkeypatch.setattr(_realtime, "_MAX_CACHE_BYTES", 1)
    elif invalid == "malformed":
        reply["item"]["content"][0]["audio"] = "!invalid!"
    else:
        monkeypatch.setattr(_realtime, "_AUDIO_ACCUM_MAX_BYTES", 1)
    await socket.feed(reply)
    await asyncio.sleep(0)
    assert not model._dd_realtime_state.items["a1"].wanted
    assert len([e for e in socket.sent if e["type"] == "conversation.item.retrieve"]) == len(requests)
    await model.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.parametrize("response_at", ["speech_started", "speech_stopped"])
async def test_realtime_input_binds_at_commit_after_interleaved_response(
    agents, openai_agents_llmobs, test_spans, socket, response_at
):
    model = await connect(socket, {"call_id": "interleaved-input"})
    for kind, offset in [("speech_started", {"audio_start_ms": 100}), ("speech_stopped", {"audio_end_ms": 200})]:
        await socket.feed({"type": "input_audio_buffer." + kind, "item_id": "u2", **offset})
        if kind == response_at:
            await socket.feed(created("tool-response"))
    await socket.feed({"type": "input_audio_buffer.committed", "item_id": "u2", "previous_item_id": ""})
    await socket.feed(created("caller-response"))
    state = model._dd_realtime_state
    previous = state.entries["tool-response"].turn
    caller = state.entries["caller-response"].turn
    try:
        await socket.feed(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "u2",
                "content_index": 0,
                "transcript": "new speech",
                "usage": {"type": "duration", "seconds": 0.1},
            }
        )
        reply = retrieved("u2", transcript="new speech")
        reply["item"]["role"] = "user"
        reply["item"]["content"][0]["type"] = "input_audio"
        await socket.feed(reply)
        assert previous.input.transcript != "new speech"
        assert not previous.input.audio.chunks
        assert caller.input.transcript == "new speech"
        assert caller.input.audio.total_decoded_bytes == 48
        assert state.items["u2"].raw_byte_bound == 4800
        await socket.feed(done("tool-response"))
        await socket.feed(done("caller-response"))
    finally:
        await model.close()
    messages = _get_llmobs_data_metastruct(caller.span)["meta"]["input"]["messages"]
    assert messages[0]["content"] == "new speech"
    assert messages[0]["audio_parts"]
    assert all(
        message.get("content") != "new speech" and not message.get("audio_parts")
        for message in _get_llmobs_data_metastruct(previous.span)["meta"].get("input", {}).get("messages", [])
    )


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
async def test_realtime_uncommitted_vad_offsets_bounded_and_cleared(agents, openai_agents_llmobs, socket, monkeypatch):
    monkeypatch.setattr(_realtime, "_MAX_ITEMS", 2)
    model = await connect(socket, {"call_id": "orphan-vad"})
    state = model._dd_realtime_state
    for index in range(3):
        await socket.feed({"type": "input_audio_buffer.speech_started", "item_id": str(index), "audio_start_ms": index})
    assert list(state.input_offsets) == ["1", "2"]
    assert not state.items
    await socket.feed({"type": "input_audio_buffer.cleared"})
    assert not state.input_offsets
    await socket.feed({"type": "input_audio_buffer.speech_started", "item_id": "pending", "audio_start_ms": 10})
    await model.close()
    assert not state.input_offsets


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.parametrize("legacy", [False, True])
async def test_realtime_sideband_vad_preserves_barge_in_overlap(
    agents, openai_agents_llmobs, test_spans, socket, monkeypatch, legacy
):
    now = [10_000_000_000]
    monkeypatch.setattr(_realtime.time, "time_ns", lambda: now[0])
    model = await connect(socket, {"call_id": "vad-overlap"})
    session = {"id": "session-test", "object": "realtime.session", "model": "gpt-realtime"}
    detection = {"type": "server_vad", "silence_duration_ms": 200}
    if legacy:
        session["turn_detection"] = detection
    else:
        session["audio"] = {"input": {"turn_detection": detection}}
    await socket.feed({"type": "session.updated", "session": session})
    await socket.feed(created("r1"))
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    now[0] = 11_000_000_000
    await socket.feed(done("r1", [output_item()]))
    now[0] = 12_000_000_000
    await socket.feed({"type": "input_audio_buffer.speech_started", "item_id": "u2", "audio_start_ms": 9000})
    await socket.feed({"type": "output_audio_buffer.cleared", "response_id": "r1"})
    await bounded_output(socket, milliseconds=2000)
    await socket.feed(retrieved(audio=b"\0\0" * 48000))
    now[0] = 13_000_000_000
    await socket.feed({"type": "input_audio_buffer.speech_stopped", "item_id": "u2", "audio_end_ms": 10300})
    await socket.feed({"type": "input_audio_buffer.committed", "item_id": "u2", "previous_item_id": "a1"})
    now[0] = 13_100_000_000
    await socket.feed(created("r2"))
    second_model = model._dd_realtime_state.entries["r2"].turn.span
    reply = retrieved("u2", b"\0\0" * 31200, "change the time")
    reply["item"]["role"] = "user"
    reply["item"]["content"][0]["type"] = "input_audio"
    await socket.feed(reply)
    now[0] = 14_000_000_000
    await socket.feed(done("r2"))
    await model.close()
    spans = [span for trace in test_spans.pop_traces() for span in trace]
    caller = next(s for s in spans if s.resource == "createRealtimeUserSpeech")
    assistant = next(s for s in spans if s.resource == "createRealtimeAgentSpeech")
    assert caller.start_ns == 11_700_000_000
    assert caller.start_ns + caller.duration_ns == 12_800_000_000
    assert assistant.start_ns + assistant.duration_ns - caller.start_ns == 300_000_000
    assert second_model.start_ns == 12_800_000_000
    assert_llmobs_span_data(_get_llmobs_data_metastruct(second_model), span_kind="llm")
    audio = _get_llmobs_data_metastruct(second_model)["meta"]["input"]["messages"][0]["audio_parts"][0]
    assert len(base64.b64decode(audio["content"])) == 44 + 62400


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
async def test_realtime_sideband_vad_stop_updates_preserve_first_onset(
    agents, openai_agents_llmobs, test_spans, socket, monkeypatch
):
    now = [10_000_000_000]
    monkeypatch.setattr(_realtime.time, "time_ns", lambda: now[0])
    model = await connect(socket, {"call_id": "vad-updates"})
    await socket.feed({"type": "input_audio_buffer.speech_started", "item_id": "u1", "audio_start_ms": 0})
    now[0] = 11_000_000_000
    await socket.feed({"type": "input_audio_buffer.speech_stopped", "item_id": "u1", "audio_end_ms": 1300})
    now[0] = 12_000_000_000
    await socket.feed({"type": "input_audio_buffer.speech_stopped", "item_id": "u1", "audio_end_ms": 2300})
    now[0] = 13_000_000_000
    for end in [2300, 2000, -1, True, float("inf")]:
        await socket.feed({"type": "input_audio_buffer.speech_stopped", "item_id": "u1", "audio_end_ms": end})
    await socket.feed({"type": "input_audio_buffer.speech_stopped", "item_id": "stale", "audio_end_ms": 9000})
    await socket.feed({"type": "input_audio_buffer.committed", "item_id": "u1", "previous_item_id": ""})
    await socket.feed(created())
    turn = model._dd_realtime_state.entries["r1"].turn
    assert turn.input.speech_start_ns == 9_700_000_000
    assert turn.input.speech_end_ns == 12_000_000_000
    assert turn.span.start_ns == 12_000_000_000
    assert model._dd_realtime_state.items["u1"].raw_byte_bound == 110400
    await model.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.parametrize("invalid", [-1, True, float("inf"), "unknown"])
async def test_realtime_sideband_invalid_vad_duration_keeps_observed_onset(
    agents, openai_agents_llmobs, socket, monkeypatch, invalid
):
    now = [10_000_000_000]
    monkeypatch.setattr(_realtime.time, "time_ns", lambda: now[0])
    model = await connect(socket, {"call_id": "invalid-vad"})
    await socket.feed({"type": "input_audio_buffer.speech_started", "item_id": "u1", "audio_start_ms": invalid})
    now[0] = 11_000_000_000
    await socket.feed({"type": "input_audio_buffer.speech_stopped", "item_id": "u1", "audio_end_ms": 1300})
    await socket.feed({"type": "input_audio_buffer.committed", "item_id": "u1", "previous_item_id": ""})
    await socket.feed(created())
    turn = model._dd_realtime_state.entries["r1"].turn
    assert turn.input.speech_start_ns == 10_000_000_000
    assert turn.input.speech_end_ns == 11_000_000_000
    assert model._dd_realtime_state.items["u1"].raw_byte_bound is None
    await model.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.parametrize("generation_ready", [False, True])
@pytest.mark.parametrize("clip_ms", [800, 1000])
async def test_realtime_cleared_continuation_anchors_complete_played_prefix(
    agents, openai_agents_llmobs, test_spans, socket, monkeypatch, generation_ready, clip_ms
):
    now = [10_000_000_000]
    monkeypatch.setattr(_realtime.time, "time_ns", lambda: now[0])
    model = await connect(socket, {"call_id": "truncated-continuation"})
    await socket.feed(created("r1"))
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    now[0] = 11_000_000_000
    await socket.feed(done("r1", [output_item("a1", "preamble")]))
    await socket.feed(created("r2"))
    second = model._dd_realtime_state.entries["r2"].turn.span
    now[0] = 12_000_000_000 if generation_ready else 19_500_000_000
    await socket.feed(done("r2", [output_item("a2", "answer")]))
    now[0] = 20_000_000_000
    await socket.feed({"type": "output_audio_buffer.cleared", "response_id": "r2"})
    await bounded_output(socket, "a2", milliseconds=1000)
    await socket.feed(retrieved("a2", b"\0\0" * (24 * clip_ms), "heard"))
    await model.close()
    spans = [span for trace in test_spans.pop_traces() for span in trace]
    speech = [span for span in spans if span.resource == "createRealtimeAgentSpeech"]
    trusted = generation_ready and clip_ms == 1000
    assert len(speech) == int(trusted)
    if trusted:
        assert speech[0].start_ns == 19_000_000_000
        assert speech[0].duration_ns == 1_000_000_000
        assert speech[0].parent_id == second.parent_id
    message = _get_llmobs_data_metastruct(second)["meta"]["output"]["messages"][0]
    assert len(base64.b64decode(message["audio_parts"][0]["content"])) == 44 + 48 * clip_ms


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.parametrize("speed", [None, 0.25, 1.0, 1.5])
@pytest.mark.parametrize("mime,rate,width", [("audio/pcm", 24000, 2), ("audio/pcmu", 8000, 1)])
async def test_realtime_completed_reply_uses_reported_audio_usage(
    agents, openai_agents_llmobs, test_spans, socket, speed, mime, rate, width
):
    model = await connect(socket, {"call_id": "complete-output"})
    output = {"format": {"type": mime}}
    if speed is not None:
        output["speed"] = speed
    await socket.feed({"type": "session.updated", "session": {"id": "session-test", "audio": {"output": output}}})
    await socket.feed(created())
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    await socket.feed(done(output=[output_item()], usage={"output_token_details": {"audio_tokens": 20}}))
    await asyncio.sleep(0)
    requests = [e for e in socket.sent if e["type"] == "conversation.item.retrieve"]
    assert len(requests) == 1
    raw = b"\0" * (int(rate / (speed or 1)) * width)
    await socket.feed(retrieved(audio=raw, transcript="hello"))
    await socket.feed({"type": "output_audio_buffer.stopped", "response_id": "r1"})
    await model.close()
    span, _ = llm_span(test_spans)
    message = _get_llmobs_data_metastruct(span)["meta"]["output"]["messages"][0]
    assert message["content"] == "hello"
    assert len(base64.b64decode(message["audio_parts"][0]["content"])) == 44 + len(raw) * (2 if width == 1 else 1)


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
async def test_realtime_output_usage_budget_is_shared_by_response_items(
    agents, openai_agents_llmobs, test_spans, socket, monkeypatch
):
    monkeypatch.setattr(_realtime, "_AUDIO_ACCUM_MAX_BYTES", 70000)
    model = await connect(socket, {"call_id": "aggregate-usage"})
    await socket.feed({"type": "session.updated", "session": {"id": "session-test", "audio": {"output": {"speed": 1}}}})
    await socket.feed(created())
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    await socket.feed(
        done(output=[output_item("a1"), output_item("a2")], usage={"output_token_details": {"audio_tokens": 20}})
    )
    await asyncio.sleep(0)
    assert len([e for e in socket.sent if e["type"] == "conversation.item.retrieve"]) == 2
    await socket.feed(retrieved("a1", b"\0\0" * 12000))
    await socket.feed(retrieved("a2", b"\0\0" * 12000))
    await socket.feed({"type": "output_audio_buffer.stopped", "response_id": "r1"})
    await model.close()
    span, _ = llm_span(test_spans)
    part = _get_llmobs_data_metastruct(span)["meta"]["output"]["messages"][0]["audio_parts"][0]
    assert len(base64.b64decode(part["content"])) == 44 + 48000


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.parametrize("tokens", [None, 0, -1, True, 20.5, "20", float("inf"), 10**20])
async def test_realtime_unusable_output_usage_is_omitted_explicitly(
    agents, openai_agents_llmobs, test_spans, socket, tokens
):
    model = await connect(socket, {"call_id": "unknown-usage"})
    await socket.feed(created())
    await socket.feed(done(output=[output_item()], usage={"output_token_details": {"audio_tokens": tokens}}))
    await asyncio.sleep(0)
    assert not [e for e in socket.sent if e["type"] == "conversation.item.retrieve"]
    await model.close()
    span, _ = llm_span(test_spans)
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span), span_kind="llm", metadata={"output_audio_capture": "omitted"}
    )


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.parametrize("response_speed,bound", [(None, 50400), (0.5, 100800), (1.5, 50400)])
async def test_realtime_output_usage_speed_is_snapshotted_per_response(
    agents, openai_agents_llmobs, socket, response_speed, bound
):
    model = await connect(socket, {"call_id": "usage-speed"})
    await socket.feed({"type": "session.updated", "session": {"id": "session-test", "audio": {"output": {"speed": 1}}}})
    event = created()
    if response_speed is not None:
        event["response"]["audio"] = {"output": {"speed": response_speed}}
    await socket.feed(event)
    await socket.feed(
        {"type": "session.updated", "session": {"id": "session-test", "audio": {"output": {"speed": 0.25}}}}
    )
    await socket.feed(done(output=[output_item()], usage={"output_token_details": {"audio_tokens": 20}}))
    assert model._dd_realtime_state.entries["r1"].audio_byte_bound == bound
    await model.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
async def test_realtime_output_usage_does_not_bound_unknown_caller_audio(agents, openai_agents_llmobs, socket):
    model = await connect(socket, {"call_id": "caller-not-output"})
    await socket.feed({"type": "input_audio_buffer.committed", "item_id": "u1", "previous_item_id": ""})
    await socket.feed(created())
    await socket.feed(done(output=[output_item()], usage={"output_token_details": {"audio_tokens": 20}}))
    await asyncio.sleep(0)
    requests = [e for e in socket.sent if e["type"] == "conversation.item.retrieve"]
    assert [e["item_id"] for e in requests] == ["a1"]
    assert not model._dd_realtime_state.items["u1"].wanted
    await model.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
async def test_realtime_usage_capture_places_preamble_and_played_continuation(
    agents, openai_agents_llmobs, test_spans, socket, monkeypatch
):
    now = [10_000_000_000]
    monkeypatch.setattr(_realtime.time, "time_ns", lambda: now[0])
    model = await connect(socket, {"call_id": "complete-preamble"})
    await socket.feed({"type": "session.updated", "session": {"id": "session-test", "audio": {"output": {"speed": 1}}}})
    await socket.feed(created("r1"))
    first = model._dd_realtime_state.entries["r1"].turn.span
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    now[0] = 10_100_000_000
    usage = {"output_token_details": {"audio_tokens": 20}}
    await socket.feed(done("r1", [output_item("a1", "preamble")], usage))
    await socket.feed(retrieved("a1", b"\0\0" * 24000, "preamble"))
    now[0] = 10_200_000_000
    await socket.feed(created("r2"))
    second = model._dd_realtime_state.entries["r2"].turn.span
    now[0] = 10_500_000_000
    await socket.feed(done("r2", [output_item("a2", "answer")], usage))
    now[0] = 12_000_000_000
    await socket.feed({"type": "output_audio_buffer.cleared", "response_id": "r2"})
    await bounded_output(socket, "a2", milliseconds=1000)
    await socket.feed(retrieved("a2", b"\0\0" * 24000, "answer"))
    await model.close()
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    speech = sorted((s for s in spans if s.resource == "createRealtimeAgentSpeech"), key=lambda s: s.start_ns)
    assert [(s.start_ns, s.duration_ns) for s in speech] == [
        (10_000_000_000, 1_000_000_000),
        (11_000_000_000, 1_000_000_000),
    ]
    for span in [first, second]:
        message = _get_llmobs_data_metastruct(span)["meta"]["output"]["messages"][0]
        assert len(base64.b64decode(message["audio_parts"][0]["content"])) == 44 + 48000


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(sdk, "OpenAIRealtimeSIPModel"), reason="SDK predates call attachment")
@pytest.mark.parametrize("cut_ms,transcript", [(500, ""), (1000, "hello")])
async def test_realtime_prefetched_reply_keeps_only_heard_audio_after_barge_in(
    agents, openai_agents_llmobs, test_spans, socket, cut_ms, transcript
):
    model = await connect(socket, {"call_id": "prefetch-then-interrupt"})
    await socket.feed(created())
    await socket.feed({"type": "output_audio_buffer.started", "response_id": "r1"})
    await socket.feed(done(output=[output_item()], usage={"output_token_details": {"audio_tokens": 20}}))
    await asyncio.sleep(0)
    await socket.feed(retrieved(audio=b"\1\0" * 24000, transcript="hello"))
    await socket.feed({"type": "output_audio_buffer.cleared", "response_id": "r1"})
    await bounded_output(socket, milliseconds=cut_ms)
    await model.close()
    span, _ = llm_span(test_spans)
    message = _get_llmobs_data_metastruct(span)["meta"]["output"]["messages"][0]
    assert message["content"] == transcript
    raw = base64.b64decode(message["audio_parts"][0]["content"])
    assert len(raw) == 44 + 48 * cut_ms
    assert raw[44:] == b"\1\0" * (24 * cut_ms)
    assert len([e for e in socket.sent if e["type"] == "conversation.item.retrieve"]) == 1
