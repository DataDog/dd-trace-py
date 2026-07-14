"""Streaming response-evaluation AI Guard tests for the OpenAI SDK.

All "buffered" cases run with ``DD_AI_GUARD_ANALYZE_STREAM_RESPONSES_ENABLED=true``
(via the ``*_buffered`` fixtures). The flag-off contract pins live alongside the
non-streaming tests in ``test_openai.py`` / ``test_responses.py`` and are kept
unchanged.
"""

from unittest.mock import patch

import pytest

from ddtrace.aiguard import AIGuardAbortError
from ddtrace.aiguard._listener import _make_openai_stream_wrappers
from ddtrace.aiguard._streaming import BufferedAIGuardAsyncStream
from ddtrace.aiguard._streaming import BufferedAIGuardStream
from tests.aiguard.utils import mock_evaluate_response
from tests.aiguard.utils import override_ai_guard_config


CHAT_MODEL = "gpt-3.5-turbo"
RESPONSES_MODEL = "gpt-4o-mini"


def _user_messages(content="Hello"):
    return [{"role": "user", "content": content}]


# ---------------------------------------------------------------------------
# Chat Completions — sync
# ---------------------------------------------------------------------------


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_chat_stream_sync_buffered_allow(mock_execute_request, openai_client_stream_buffered):
    """ALLOW: create(stream=True) returns a BufferedAIGuardStream; chunks replay after 2 evals."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = openai_client_stream_buffered.chat.completions.create(
        model=CHAT_MODEL, messages=_user_messages(), stream=True
    )
    assert isinstance(stream, BufferedAIGuardStream)
    chunks = list(stream)

    assert chunks  # buffered chunks were replayed
    assert mock_execute_request.call_count == 2  # before-hook + buffered response


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_chat_stream_sync_buffered_block(mock_execute_request, openai_client_stream_buffered, decision):
    """Block on the response: AIGuardAbortError raised, ZERO chunks delivered."""
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response(decision)]

    stream = openai_client_stream_buffered.chat.completions.create(
        model=CHAT_MODEL, messages=_user_messages(), stream=True
    )
    seen = []
    with pytest.raises(AIGuardAbortError):
        for chunk in stream:
            seen.append(chunk)

    assert seen == []  # security invariant: nothing reaches the caller before ALLOW
    assert mock_execute_request.call_count == 2


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_chat_stream_sync_block_disabled_replays(mock_execute_request, openai_client_stream_buffered):
    """``_ai_guard_block=False`` + DENY: no raise, chunks replayed."""
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response("DENY", block=True)]

    with override_ai_guard_config(dict(_ai_guard_block=False)):
        stream = openai_client_stream_buffered.chat.completions.create(
            model=CHAT_MODEL, messages=_user_messages(), stream=True
        )
        chunks = list(stream)

    assert chunks
    assert mock_execute_request.call_count == 2


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_chat_stream_collision_passthrough(mock_execute_request, openai_client_stream_buffered, aiguard_active_context):
    """Inside an active framework context: live passthrough, no evaluation."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = openai_client_stream_buffered.chat.completions.create(
        model=CHAT_MODEL, messages=_user_messages(), stream=True
    )
    list(stream)

    mock_execute_request.assert_not_called()


@patch("ddtrace.aiguard.integrations._openai_chat_streaming.openai_construct_message_from_streamed_chunks")
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_chat_stream_reconstruction_failure_fails_open(
    mock_execute_request, mock_reconstruct, openai_client_stream_buffered
):
    """A reconstruction error must NOT break the stream: chunks replay, response eval skipped."""
    mock_reconstruct.side_effect = RuntimeError("boom")
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = openai_client_stream_buffered.chat.completions.create(
        model=CHAT_MODEL, messages=_user_messages(), stream=True
    )
    chunks = list(stream)

    assert chunks  # replayed despite reconstruction failure
    assert mock_execute_request.call_count == 1  # only the before-hook ran


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_chat_stream_flag_off_not_buffered(mock_execute_request, openai_client_stream):
    """Flag off: create(stream=True) is NOT a BufferedAIGuardStream (today's behavior)."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = openai_client_stream.chat.completions.create(model=CHAT_MODEL, messages=_user_messages(), stream=True)
    assert not isinstance(stream, BufferedAIGuardStream)
    list(stream)

    assert mock_execute_request.call_count == 1  # before-hook only


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_chat_stream_tool_calls_are_evaluated(mock_execute_request, openai_client_stream_tool_calls_buffered):
    """A streamed tool_call must survive reconstruction and be evaluated, not silently dropped."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = openai_client_stream_tool_calls_buffered.chat.completions.create(
        model=CHAT_MODEL, messages=_user_messages(), stream=True
    )
    list(stream)

    assert mock_execute_request.call_count == 2  # before-hook + buffered response
    # The response evaluation (2nd call) must carry the reconstructed tool call.
    response_messages = mock_execute_request.call_args_list[1].args[1]["data"]["attributes"]["messages"]
    tool_calls = [tc for msg in response_messages for tc in (msg.get("tool_calls") or [])]
    assert [tc["function"]["name"] for tc in tool_calls] == ["get_weather"]
    assert tool_calls[0]["function"]["arguments"] == '{"city": "Paris"}'


# ---------------------------------------------------------------------------
# Chat Completions — async
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_chat_stream_async_buffered_allow(mock_execute_request, async_openai_client_stream_buffered):
    """Async ALLOW: AsyncCompletions.create (async_wrapper path) returns BufferedAIGuardAsyncStream."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = await async_openai_client_stream_buffered.chat.completions.create(
        model=CHAT_MODEL, messages=_user_messages(), stream=True
    )
    assert isinstance(stream, BufferedAIGuardAsyncStream)
    chunks = [chunk async for chunk in stream]

    assert chunks
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_chat_stream_async_buffered_block(mock_execute_request, async_openai_client_stream_buffered):
    """Async block: AIGuardAbortError raised, zero chunks delivered."""
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response("DENY")]

    stream = await async_openai_client_stream_buffered.chat.completions.create(
        model=CHAT_MODEL, messages=_user_messages(), stream=True
    )
    seen = []
    with pytest.raises(AIGuardAbortError):
        async for chunk in stream:
            seen.append(chunk)

    assert seen == []
    assert mock_execute_request.call_count == 2


# ---------------------------------------------------------------------------
# Responses API
# ---------------------------------------------------------------------------


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_responses_stream_sync_buffered_allow(mock_execute_request, openai_responses_stream_client_buffered):
    """ALLOW: responses.create(stream=True) buffers; before-hook (lifted) + response eval."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = openai_responses_stream_client_buffered.responses.create(model=RESPONSES_MODEL, input="Hello", stream=True)
    assert isinstance(stream, BufferedAIGuardStream)
    events = list(stream)

    assert events
    assert mock_execute_request.call_count == 2


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_responses_stream_sync_buffered_block(mock_execute_request, openai_responses_stream_client_buffered):
    """Block on response snapshot: AIGuardAbortError raised, zero events delivered."""
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response("DENY")]

    stream = openai_responses_stream_client_buffered.responses.create(model=RESPONSES_MODEL, input="Hello", stream=True)
    seen = []
    with pytest.raises(AIGuardAbortError):
        for event in stream:
            seen.append(event)

    assert seen == []
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_responses_stream_async_buffered_allow(
    mock_execute_request, async_openai_responses_stream_client_buffered
):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = await async_openai_responses_stream_client_buffered.responses.create(
        model=RESPONSES_MODEL, input="Hello", stream=True
    )
    assert isinstance(stream, BufferedAIGuardAsyncStream)
    events = [event async for event in stream]

    assert events
    assert mock_execute_request.call_count == 2


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_responses_stream_flag_off_not_buffered(mock_execute_request, openai_responses_stream_client):
    """Flag off: streaming Responses are skipped entirely (no wrapper, no eval)."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = openai_responses_stream_client.responses.create(model=RESPONSES_MODEL, input="Hello", stream=True)
    assert not isinstance(stream, BufferedAIGuardStream)
    list(stream)

    mock_execute_request.assert_not_called()


# ---------------------------------------------------------------------------
# Raw response helper (chat.completions.with_raw_response)
# ---------------------------------------------------------------------------


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_raw_response_stream_sync_buffered_allow(mock_execute_request, openai_client_stream_buffered):
    """ALLOW: with_raw_response.create(stream=True).parse() yields a buffered stream."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    raw = openai_client_stream_buffered.chat.completions.with_raw_response.create(
        model=CHAT_MODEL, messages=_user_messages(), stream=True
    )
    stream = raw.parse()
    assert isinstance(stream, BufferedAIGuardStream)
    chunks = list(stream)

    assert chunks
    assert mock_execute_request.call_count == 2  # contrib raw .before + buffered response


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_raw_response_stream_sync_buffered_block(mock_execute_request, openai_client_stream_buffered):
    """Block on raw-response stream: AIGuardAbortError, zero chunks delivered."""
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response("DENY")]

    raw = openai_client_stream_buffered.chat.completions.with_raw_response.create(
        model=CHAT_MODEL, messages=_user_messages(), stream=True
    )
    stream = raw.parse()
    seen = []
    with pytest.raises(AIGuardAbortError):
        for chunk in stream:
            seen.append(chunk)

    assert seen == []
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_raw_response_stream_async_buffered_allow(mock_execute_request, async_openai_client_stream_buffered):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    raw = await async_openai_client_stream_buffered.chat.completions.with_raw_response.create(
        model=CHAT_MODEL, messages=_user_messages(), stream=True
    )
    stream = raw.parse()
    if hasattr(stream, "__await__"):
        stream = await stream
    assert isinstance(stream, BufferedAIGuardAsyncStream)
    chunks = [chunk async for chunk in stream]

    assert chunks
    assert mock_execute_request.call_count == 2


# ---------------------------------------------------------------------------
# Plain-generator streams (OpenAI SDK <1.6)
#
# SDKs older than 1.6 never produce a TracedStream — the contrib returns a raw
# (async) generator. These must still be buffered+evaluated, not forwarded live.
# Tested by driving ``_make_openai_stream_wrappers`` directly so the case is
# covered regardless of the installed SDK version in the test matrix.
# ---------------------------------------------------------------------------


def _record_eval():
    evaluated = []

    def reconstruct(chunks):
        return {"reconstructed": list(chunks)}

    def evaluate_after(client, kwargs, resp):
        evaluated.append(resp)

    return evaluated, reconstruct, evaluate_after


def test_plain_generator_sync_buffered():
    """A sync plain generator (SDK <1.6) is wrapped, evaluated once, then replayed."""
    evaluated, reconstruct, evaluate_after = _record_eval()
    sync_wrapper, _ = _make_openai_stream_wrappers(object(), reconstruct, evaluate_after)

    fake_chunks = ["a", "b", "c"]

    def func(*args, **kwargs):
        def gen():
            yield from fake_chunks

        return gen()

    with override_ai_guard_config(dict(_ai_guard_analyze_stream_responses_enabled=True)):
        result = sync_wrapper(func, None, (), {"stream": True})
        assert isinstance(result, BufferedAIGuardStream)
        replayed = list(result)  # drains, evaluates, then replays
    assert replayed == fake_chunks
    assert evaluated == [{"reconstructed": fake_chunks}]  # evaluated before replay


def test_plain_generator_sync_dispatches_async_for_async_generator():
    """A sync method returning an async generator dispatches to the async buffer."""
    _, reconstruct, evaluate_after = _record_eval()
    sync_wrapper, _ = _make_openai_stream_wrappers(object(), reconstruct, evaluate_after)

    async def agen():
        yield "z"

    result = sync_wrapper(lambda *a, **k: agen(), None, (), {"stream": True})
    assert isinstance(result, BufferedAIGuardAsyncStream)


@pytest.mark.asyncio
async def test_plain_generator_async_buffered():
    """An async plain generator (SDK <1.6) is wrapped, evaluated once, then replayed."""
    evaluated, reconstruct, evaluate_after = _record_eval()
    _, async_wrapper = _make_openai_stream_wrappers(object(), reconstruct, evaluate_after)

    fake_chunks = ["x", "y"]

    async def func(*args, **kwargs):
        async def agen():
            for chunk in fake_chunks:
                yield chunk

        return agen()

    with override_ai_guard_config(dict(_ai_guard_analyze_stream_responses_enabled=True)):
        result = await async_wrapper(func, None, (), {"stream": True})
        assert isinstance(result, BufferedAIGuardAsyncStream)
        collected = [chunk async for chunk in result]
    assert collected == fake_chunks
    assert evaluated == [{"reconstructed": fake_chunks}]


def test_non_stream_result_not_wrapped():
    """A non-stream return value (not a TracedStream, not a generator) passes through unwrapped."""
    _, reconstruct, evaluate_after = _record_eval()
    sync_wrapper, _ = _make_openai_stream_wrappers(object(), reconstruct, evaluate_after)

    sentinel = object()
    result = sync_wrapper(lambda *a, **k: sentinel, None, (), {})
    assert result is sentinel
