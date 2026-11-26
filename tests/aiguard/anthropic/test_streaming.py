"""Streaming AI Guard tests for the Anthropic SDK."""

import contextlib
import json
from unittest.mock import patch

import httpx
import pytest

from ddtrace.aiguard import AIGuardAbortError
from ddtrace.aiguard._streaming import BufferedEvents
from tests.aiguard.utils import mock_evaluate_response
from tests.aiguard.utils import override_ai_guard_config


CHAT_MODEL = "claude-3-opus-20240229"
CHAT_PARAMS = dict(model=CHAT_MODEL, max_tokens=256)


def _user_messages(content="Hello"):
    return [{"role": "user", "content": content}]


# ---------------------------------------------------------------------------
# Sync streaming
# ---------------------------------------------------------------------------


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_stream_sync_allow(mock_execute_request, anthropic_client_stream):
    """ALLOW: stream context manager yields expected text deltas."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with anthropic_client_stream.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
        chunks = list(stream.text_stream)

    assert "".join(chunks) == "ok"
    # Only the before-hook fires for streams (after-hook is gated by is_streaming_operation).
    assert mock_execute_request.call_count == 1


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_stream_sync_block_before_first_delta(mock_execute_request, anthropic_client_stream):
    """DENY at before-model raises before the first delta is yielded."""
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    with pytest.raises(AIGuardAbortError):
        anthropic_client_stream.messages.stream(messages=_user_messages(), **CHAT_PARAMS)

    mock_execute_request.assert_called_once()


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_create_stream_true_sync_allow(mock_execute_request, anthropic_client_stream):
    """``Messages.create(stream=True)`` returns an iterator; before-hook fires once."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = anthropic_client_stream.messages.create(messages=_user_messages(), stream=True, **CHAT_PARAMS)
    list(stream)
    assert mock_execute_request.call_count == 1


# ---------------------------------------------------------------------------
# Async streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_stream_async_allow(mock_execute_request, async_anthropic_client_stream):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    async with async_anthropic_client_stream.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
        chunks = []
        async for text in stream.text_stream:
            chunks.append(text)

    assert "".join(chunks) == "ok"
    assert mock_execute_request.call_count == 1


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_stream_async_block_before_first_delta(mock_execute_request, async_anthropic_client_stream):
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    with pytest.raises(AIGuardAbortError):
        async_anthropic_client_stream.messages.stream(messages=_user_messages(), **CHAT_PARAMS)

    mock_execute_request.assert_called_once()


# ---------------------------------------------------------------------------
# After-hook NOT dispatched on streaming
# ---------------------------------------------------------------------------


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_after_hook_not_dispatched_on_stream(mock_execute_request, anthropic_client_stream):
    """Streaming responses only trigger ``.before`` — never ``.after``."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with anthropic_client_stream.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
        for _ in stream.text_stream:
            pass

    assert mock_execute_request.call_count == 1


# ---------------------------------------------------------------------------
# Beta streaming
# ---------------------------------------------------------------------------


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_beta_stream_sync_allow(mock_execute_request, anthropic_client_stream):
    pytest.importorskip("anthropic.resources.beta.messages.messages")
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with anthropic_client_stream.beta.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
        for _ in stream.text_stream:
            pass

    assert mock_execute_request.call_count == 1


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_beta_stream_async_allow(mock_execute_request, async_anthropic_client_stream):
    pytest.importorskip("anthropic.resources.beta.messages.messages")
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    async with async_anthropic_client_stream.beta.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
        async for _ in stream.text_stream:
            pass

    assert mock_execute_request.call_count == 1


# ---------------------------------------------------------------------------
# Response evaluation (DD_AI_GUARD_ANALYZE_STREAM_RESPONSES_ENABLED=True)
# ---------------------------------------------------------------------------


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_stream_sync_response_allow(mock_execute_request, anthropic_client_stream_buffered):
    """Buffered: ALLOW on response delivers full text after 2 evaluations."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with anthropic_client_stream_buffered.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
        chunks = list(stream.text_stream)

    assert "".join(chunks) == "ok"
    assert mock_execute_request.call_count == 2


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_stream_sync_response_block(mock_execute_request, anthropic_client_stream_buffered):
    """Buffered: DENY on the response evaluation raises AIGuardAbortError."""
    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),  # before-hook: allow
        mock_evaluate_response("DENY"),  # response evaluation: block
    ]

    with pytest.raises(AIGuardAbortError):
        with anthropic_client_stream_buffered.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
            list(stream.text_stream)

    assert mock_execute_request.call_count == 2


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_create_stream_true_buffered_allow(mock_execute_request, anthropic_client_stream_buffered):
    """``Messages.create(stream=True)`` with buffering ALLOW delivers text after 2 evals."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = anthropic_client_stream_buffered.messages.create(messages=_user_messages(), stream=True, **CHAT_PARAMS)
    list(stream)
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_stream_async_response_allow(mock_execute_request, async_anthropic_client_stream_buffered):
    """Async buffered: ALLOW on response delivers full text after 2 evaluations."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    async with async_anthropic_client_stream_buffered.messages.stream(
        messages=_user_messages(), **CHAT_PARAMS
    ) as stream:
        chunks = []
        async for text in stream.text_stream:
            chunks.append(text)

    assert "".join(chunks) == "ok"
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_stream_async_response_block(mock_execute_request, async_anthropic_client_stream_buffered):
    """Async buffered: DENY on response evaluation raises AIGuardAbortError."""
    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),  # before-hook: allow
        mock_evaluate_response("DENY"),  # response evaluation: block
    ]

    with pytest.raises(AIGuardAbortError):
        async with async_anthropic_client_stream_buffered.messages.stream(
            messages=_user_messages(), **CHAT_PARAMS
        ) as stream:
            async for _ in stream.text_stream:
                pass

    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_create_stream_true_async_buffered_allow(mock_execute_request, async_anthropic_client_stream_buffered):
    """``AsyncMessages.create(stream=True)`` (async_wrapper path) ALLOW delivers text after 2 evals."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = await async_anthropic_client_stream_buffered.messages.create(
        messages=_user_messages(), stream=True, **CHAT_PARAMS
    )
    chunks = [chunk async for chunk in stream]
    assert chunks
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_create_stream_true_async_buffered_block(mock_execute_request, async_anthropic_client_stream_buffered):
    """``AsyncMessages.create(stream=True)`` (async_wrapper path) DENY raises before any chunk."""
    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),  # before-hook: allow
        mock_evaluate_response("DENY"),  # response evaluation: block
    ]

    stream = await async_anthropic_client_stream_buffered.messages.create(
        messages=_user_messages(), stream=True, **CHAT_PARAMS
    )
    with pytest.raises(AIGuardAbortError):
        async for _ in stream:
            pass

    assert mock_execute_request.call_count == 2


# Patched at the source ``_construct_message`` (resolved at call time inside
# ``reconstruct_anthropic``) rather than the wrapper-captured ``reconstruct_anthropic``,
# since the wrappers are installed during fixture setup before this patch applies.
@patch("ddtrace.aiguard.integrations._anthropic_streaming._construct_message", side_effect=RuntimeError("boom"))
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_stream_reconstruction_failure_fails_open(
    mock_execute_request, _mock_reconstruct, anthropic_client_stream_buffered
):
    """A reconstruction error must NOT break the stream: chunks are replayed and the
    response evaluation is skipped (only the before-hook eval fires).
    """
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with anthropic_client_stream_buffered.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
        chunks = list(stream.text_stream)

    assert "".join(chunks) == "ok"
    # Reconstruction blew up before evaluate(); only the before-hook evaluation ran.
    assert mock_execute_request.call_count == 1


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_events_not_buffered_when_flag_off(mock_execute_request, anthropic_client_stream):
    """Default (flag=False): the events of the SDK stream are not buffered."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    result = anthropic_client_stream.messages.create(messages=_user_messages(), stream=True, **CHAT_PARAMS)
    assert not isinstance(result._iterator, BufferedEvents)
    list(result)


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_events_buffered_when_flag_on(mock_execute_request, anthropic_client_stream_buffered):
    """With flag=True: the events of the SDK stream go through the AI Guard buffer."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    result = anthropic_client_stream_buffered.messages.create(messages=_user_messages(), stream=True, **CHAT_PARAMS)
    assert isinstance(result._iterator, BufferedEvents)
    list(result)


# ---------------------------------------------------------------------------
# Reads after a caught response block
# ---------------------------------------------------------------------------


def _block_then_allow():
    """Verdicts: the prompt is allowed, the response is blocked, and any later evaluation would allow."""
    return [mock_evaluate_response("ALLOW"), mock_evaluate_response("DENY")] + [
        mock_evaluate_response("ALLOW") for _ in range(3)
    ]


_SYNC_READS = {
    "iterate": lambda stream: list(stream),
    "next": lambda stream: next(stream, None),
    "text_stream": lambda stream: list(stream.text_stream),
    "get_final_message": lambda stream: stream.get_final_message(),
    "get_final_text": lambda stream: stream.get_final_text(),
    "until_done": lambda stream: stream.until_done(),
    "current_message_snapshot": lambda stream: stream.current_message_snapshot,
}


async def _async_iterate(stream):
    return [chunk async for chunk in stream]


async def _async_next(stream):
    """Like next(stream, None) for an async stream."""
    try:
        return await stream.__anext__()
    except StopAsyncIteration:
        return None


async def _async_text_stream(stream):
    return [text async for text in stream.text_stream]


async def _async_snapshot(stream):
    return stream.current_message_snapshot


_ASYNC_READS = {
    "iterate": _async_iterate,
    "next": _async_next,
    "text_stream": _async_text_stream,
    "get_final_message": lambda stream: stream.get_final_message(),
    "get_final_text": lambda stream: stream.get_final_text(),
    "until_done": lambda stream: stream.until_done(),
    "current_message_snapshot": _async_snapshot,
}


def _read_after_block(read, stream):
    """The SDK received no event of a blocked stream: a read returns nothing, or the SDK has no message."""
    try:
        return read(stream)
    except AssertionError:
        return None


async def _async_read_after_block(read, stream):
    try:
        return await read(stream)
    except AssertionError:
        return None


@pytest.mark.parametrize("read", list(_SYNC_READS))
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_stream_sync_read_after_caught_block(
    mock_execute_request, anthropic_sdk_buffered, anthropic_client_stream_buffered, read
):
    """Once the caller catches the block, no later read returns the blocked answer or evaluates it again."""
    mock_execute_request.side_effect = _block_then_allow()

    with anthropic_client_stream_buffered.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
        with pytest.raises(anthropic_sdk_buffered.APIStatusError) as caught:
            for _ in stream:
                pass
        result = _read_after_block(_SYNC_READS[read], stream)

    assert isinstance(caught.value, AIGuardAbortError)
    assert result in (None, [])
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("read", list(_ASYNC_READS))
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_stream_async_read_after_caught_block(
    mock_execute_request, anthropic_sdk_buffered, async_anthropic_client_stream_buffered, read
):
    """Async twin: once the caller catches the block, no later read returns the blocked answer."""
    mock_execute_request.side_effect = _block_then_allow()

    async with async_anthropic_client_stream_buffered.messages.stream(
        messages=_user_messages(), **CHAT_PARAMS
    ) as stream:
        with pytest.raises(anthropic_sdk_buffered.APIStatusError) as caught:
            async for _ in stream:
                pass
        result = await _async_read_after_block(_ASYNC_READS[read], stream)

    assert isinstance(caught.value, AIGuardAbortError)
    assert result in (None, [])
    assert mock_execute_request.call_count == 2


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_create_stream_true_sync_read_after_caught_block(
    mock_execute_request, anthropic_sdk_buffered, anthropic_client_stream_buffered
):
    """Iterating a blocked create(stream=True) stream again raises the block instead of yielding nothing."""
    mock_execute_request.side_effect = _block_then_allow()

    stream = anthropic_client_stream_buffered.messages.create(messages=_user_messages(), stream=True, **CHAT_PARAMS)
    with pytest.raises(anthropic_sdk_buffered.APIStatusError):
        list(stream)
    with pytest.raises(AIGuardAbortError):
        list(stream)

    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_create_stream_true_async_read_after_caught_block(
    mock_execute_request, anthropic_sdk_buffered, async_anthropic_client_stream_buffered
):
    """Iterating a blocked async create(stream=True) stream again raises the block without a new evaluation."""
    mock_execute_request.side_effect = _block_then_allow()

    stream = await async_anthropic_client_stream_buffered.messages.create(
        messages=_user_messages(), stream=True, **CHAT_PARAMS
    )
    with pytest.raises(anthropic_sdk_buffered.APIStatusError):
        await _async_iterate(stream)
    with pytest.raises(AIGuardAbortError):
        await _async_iterate(stream)

    assert mock_execute_request.call_count == 2


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_stream_sync_snapshot_after_allow(mock_execute_request, anthropic_client_stream_buffered):
    """An allowed response keeps its message snapshot."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with anthropic_client_stream_buffered.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
        list(stream)
        snapshot = stream.current_message_snapshot

    assert snapshot.content[0].text == "ok"
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_stream_async_snapshot_after_allow(mock_execute_request, async_anthropic_client_stream_buffered):
    """An allowed async response keeps its message snapshot."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    async with async_anthropic_client_stream_buffered.messages.stream(
        messages=_user_messages(), **CHAT_PARAMS
    ) as stream:
        await _async_iterate(stream)
        snapshot = stream.current_message_snapshot

    assert snapshot.content[0].text == "ok"
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["flag_off", "claim"])
@pytest.mark.parametrize("api", ["sync", "async"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_stream_snapshot_passthrough(mock_execute_request, request, api, mode):
    """A pass-through stream (analysis off at first use, or a framework claim) returns the SDK snapshot."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")
    # The buffered fixtures turn stream analysis on, so request them before turning it off.
    client = request.getfixturevalue(
        "anthropic_client_stream_buffered" if api == "sync" else "async_anthropic_client_stream_buffered"
    )
    if mode == "claim":
        request.getfixturevalue("aiguard_active_context")
        analysis = contextlib.nullcontext()
    else:
        analysis = override_ai_guard_config(dict(_ai_guard_analyze_stream_responses_enabled=False))

    with analysis:
        if api == "sync":
            with client.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
                list(stream)
                snapshot = stream.current_message_snapshot
        else:
            async with client.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
                await _async_iterate(stream)
                snapshot = stream.current_message_snapshot

    assert snapshot.content[0].text == "ok"
    # Only the prompt is evaluated with the flag off; a framework claim skips the prompt too.
    assert mock_execute_request.call_count == (1 if mode == "flag_off" else 0)


# ---------------------------------------------------------------------------
# Streaming tool runner: the loop body catches the block of a streamed answer
# ---------------------------------------------------------------------------


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _streamed_message(block, stop_reason, provider_error=False):
    """The SSE body of an assistant message with a single text or tool_use block.

    With provider_error, the provider sends an error event right after the block instead of ending the message.
    """
    if block["type"] == "text":
        start, delta = {"type": "text", "text": ""}, {"type": "text_delta", "text": block["text"]}
    else:
        start = dict(block, input={})
        delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
    message = {
        "id": "msg_runner",
        "type": "message",
        "role": "assistant",
        "model": CHAT_MODEL,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 0},
    }
    message_delta = {"stop_reason": stop_reason, "stop_sequence": None}
    events = [
        _sse("message_start", {"type": "message_start", "message": message}),
        _sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": start}),
        _sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
        _sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ]
    if provider_error:
        events.append(_sse("error", {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}))
    else:
        events += [
            _sse("message_delta", {"type": "message_delta", "delta": message_delta, "usage": {"output_tokens": 1}}),
            _sse("message_stop", {"type": "message_stop"}),
        ]
    return b"".join(events)


class _RunnerTransport(httpx.BaseTransport, httpx.AsyncBaseTransport):
    """Streams a turn that calls delete_records, then a final text turn."""

    def __init__(self, provider_error=False):
        tool_use = {"type": "tool_use", "id": "toolu_runner", "name": "delete_records", "input": {"table": "users"}}
        self._bodies = [
            _streamed_message(tool_use, "tool_use", provider_error=provider_error),
            _streamed_message({"type": "text", "text": "done"}, "end_turn"),
        ]

    def _response(self):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=self._bodies.pop(0))

    def handle_request(self, request):
        return self._response()

    async def handle_async_request(self, request):
        return self._response()


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streaming_tool_runner_sync_after_caught_block(mock_execute_request, anthropic_sdk_buffered):
    """A runner loop that catches the block of a streamed answer never runs the tool calls of that answer."""
    if not hasattr(anthropic_sdk_buffered, "beta_tool"):
        pytest.skip("the streaming tool runner needs the beta tool helpers")
    mock_execute_request.side_effect = _block_then_allow()
    deleted = []

    @anthropic_sdk_buffered.beta_tool
    def delete_records(table: str) -> str:
        """Delete every record of a table."""
        deleted.append(table)
        return "deleted"

    client = anthropic_sdk_buffered.Anthropic(
        api_key="<not-a-real-key>", http_client=httpx.Client(transport=_RunnerTransport())
    )
    runner = client.beta.messages.tool_runner(
        messages=_user_messages("Clean up"), tools=[delete_records], stream=True, **CHAT_PARAMS
    )
    caught = []
    # The SDK has no final message for the blocked stream, so the runner stops before running any tool.
    with pytest.raises(AssertionError):
        for stream in runner:
            try:
                for _ in stream:
                    pass
            except anthropic_sdk_buffered.APIStatusError as e:
                caught.append(e)

    assert len(caught) == 1
    assert deleted == []
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_streaming_tool_runner_async_after_caught_block(mock_execute_request, anthropic_sdk_buffered):
    """Async twin: a runner loop that catches the block never runs the tool calls of the blocked answer."""
    if not hasattr(anthropic_sdk_buffered, "beta_async_tool"):
        pytest.skip("the streaming tool runner needs the beta tool helpers")
    mock_execute_request.side_effect = _block_then_allow()
    deleted = []

    @anthropic_sdk_buffered.beta_async_tool
    async def delete_records(table: str) -> str:
        """Delete every record of a table."""
        deleted.append(table)
        return "deleted"

    client = anthropic_sdk_buffered.AsyncAnthropic(
        api_key="<not-a-real-key>", http_client=httpx.AsyncClient(transport=_RunnerTransport())
    )
    runner = client.beta.messages.tool_runner(
        messages=_user_messages("Clean up"), tools=[delete_records], stream=True, **CHAT_PARAMS
    )
    caught = []
    with pytest.raises(AssertionError):
        async for stream in runner:
            try:
                async for _ in stream:
                    pass
            except anthropic_sdk_buffered.APIStatusError as e:
                caught.append(e)

    assert len(caught) == 1
    assert deleted == []
    assert mock_execute_request.call_count == 2


# ---------------------------------------------------------------------------
# Collision suppression on streaming
# ---------------------------------------------------------------------------


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_stream_collision_short_circuit(mock_execute_request, anthropic_client_stream, aiguard_active_context):
    """Stream nested inside an active framework AI Guard context skips the
    before-hook and still yields text normally.
    """
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with anthropic_client_stream.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
        chunks = list(stream.text_stream)

    assert "".join(chunks) == "ok"
    mock_execute_request.assert_not_called()


# ---------------------------------------------------------------------------
# Provider error mid-stream: the partial answer was never evaluated
# ---------------------------------------------------------------------------


def _prompt_allowed_answer_blocked():
    return [mock_evaluate_response("ALLOW")] + [mock_evaluate_response("DENY") for _ in range(3)]


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_stream_sync_provider_error_never_returns_the_partial_answer(mock_execute_request, anthropic_sdk_buffered):
    """A caught provider error leaves the SDK without the partial answer, which AI Guard never evaluated."""
    mock_execute_request.side_effect = _prompt_allowed_answer_blocked()
    client = anthropic_sdk_buffered.Anthropic(
        api_key="<not-a-real-key>", http_client=httpx.Client(transport=_RunnerTransport(provider_error=True))
    )

    with client.messages.stream(messages=_user_messages("Clean up"), **CHAT_PARAMS) as stream:
        with pytest.raises(anthropic_sdk_buffered.APIStatusError) as caught:
            for _ in stream:
                pass
        final = _read_after_block(lambda s: s.get_final_message(), stream)

    assert not isinstance(caught.value, AIGuardAbortError)
    assert final is None
    assert mock_execute_request.call_count == 1


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_stream_async_provider_error_never_returns_the_partial_answer(
    mock_execute_request, anthropic_sdk_buffered
):
    """Async twin: a caught provider error leaves the SDK without the partial answer."""
    mock_execute_request.side_effect = _prompt_allowed_answer_blocked()
    client = anthropic_sdk_buffered.AsyncAnthropic(
        api_key="<not-a-real-key>", http_client=httpx.AsyncClient(transport=_RunnerTransport(provider_error=True))
    )

    async with client.messages.stream(messages=_user_messages("Clean up"), **CHAT_PARAMS) as stream:
        with pytest.raises(anthropic_sdk_buffered.APIStatusError) as caught:
            async for _ in stream:
                pass
        final = await _async_read_after_block(lambda s: s.get_final_message(), stream)

    assert not isinstance(caught.value, AIGuardAbortError)
    assert final is None
    assert mock_execute_request.call_count == 1


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streaming_tool_runner_after_caught_provider_error(mock_execute_request, anthropic_sdk_buffered):
    """A runner loop that catches a mid-stream provider error never runs the tool calls of the partial answer."""
    if not hasattr(anthropic_sdk_buffered, "beta_tool"):
        pytest.skip("the streaming tool runner needs the beta tool helpers")
    mock_execute_request.side_effect = _prompt_allowed_answer_blocked()
    deleted = []

    @anthropic_sdk_buffered.beta_tool
    def delete_records(table: str) -> str:
        """Delete every record of a table."""
        deleted.append(table)
        return "deleted"

    client = anthropic_sdk_buffered.Anthropic(
        api_key="<not-a-real-key>", http_client=httpx.Client(transport=_RunnerTransport(provider_error=True))
    )
    runner = client.beta.messages.tool_runner(
        messages=_user_messages("Clean up"), tools=[delete_records], stream=True, **CHAT_PARAMS
    )
    with pytest.raises(AssertionError):
        for stream in runner:
            try:
                for _ in stream:
                    pass
            except anthropic_sdk_buffered.APIStatusError:
                pass

    assert deleted == []


# ---------------------------------------------------------------------------
# anthropic.stream.created listener
# ---------------------------------------------------------------------------


class _SentRequest:
    def __init__(self, path):
        self.url = type("_URL", (), {"path": path})()
        self.content = json.dumps({"messages": _user_messages()}).encode()


class _StreamResponse:
    def __init__(self, path):
        self.request = _SentRequest(path)


@pytest.mark.parametrize(
    "path,buffered",
    [
        ("/v1/messages", True),
        ("/model/anthropic.claude-3-5-sonnet/invoke-with-response-stream", True),
        ("/projects/p/locations/us-east5/publishers/anthropic/models/claude-3-5-sonnet:streamRawPredict", True),
        ("/v1/messages/count_tokens", False),
        ("/v1/complete", False),
    ],
    ids=["anthropic", "bedrock", "vertex", "count_tokens", "completions"],
)
def test_stream_listener_buffers_messages_streams_of_every_client(path, buffered):
    from ddtrace.aiguard._listener import _anthropic_stream_created
    from ddtrace.contrib.internal._sdk_streams import StreamEvents

    events = StreamEvents(_StreamResponse(path), iter(["chunk"]), is_async=False)
    with override_ai_guard_config(dict(_ai_guard_analyze_stream_responses_enabled=True)):
        _anthropic_stream_created(object(), events)

    assert isinstance(events.iterator, BufferedEvents) is buffered
