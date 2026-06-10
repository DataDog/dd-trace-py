"""Streaming AI Guard tests for the Anthropic SDK."""

from unittest.mock import patch

import pytest

from ddtrace.appsec._ai_guard._streaming import BufferedAIGuardStream
from ddtrace.appsec.ai_guard import AIGuardAbortError
from tests.appsec.ai_guard.utils import mock_evaluate_response


CHAT_MODEL = "claude-3-opus-20240229"
CHAT_PARAMS = dict(model=CHAT_MODEL, max_tokens=256)


def _user_messages(content="Hello"):
    return [{"role": "user", "content": content}]


# ---------------------------------------------------------------------------
# Sync streaming
# ---------------------------------------------------------------------------


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_stream_sync_allow(mock_execute_request, anthropic_client_stream):
    """ALLOW: stream context manager yields expected text deltas."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with anthropic_client_stream.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
        chunks = list(stream.text_stream)

    assert "".join(chunks) == "ok"
    # Only the before-hook fires for streams (after-hook is gated by is_streaming_operation).
    assert mock_execute_request.call_count == 1


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_stream_sync_block_before_first_delta(mock_execute_request, anthropic_client_stream):
    """DENY at before-model raises before the first delta is yielded."""
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    with pytest.raises(AIGuardAbortError):
        anthropic_client_stream.messages.stream(messages=_user_messages(), **CHAT_PARAMS)

    mock_execute_request.assert_called_once()


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
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
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_stream_async_allow(mock_execute_request, async_anthropic_client_stream):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    async with async_anthropic_client_stream.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
        chunks = []
        async for text in stream.text_stream:
            chunks.append(text)

    assert "".join(chunks) == "ok"
    assert mock_execute_request.call_count == 1


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_stream_async_block_before_first_delta(mock_execute_request, async_anthropic_client_stream):
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    with pytest.raises(AIGuardAbortError):
        async_anthropic_client_stream.messages.stream(messages=_user_messages(), **CHAT_PARAMS)

    mock_execute_request.assert_called_once()


# ---------------------------------------------------------------------------
# After-hook NOT dispatched on streaming
# ---------------------------------------------------------------------------


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
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


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_beta_stream_sync_allow(mock_execute_request, anthropic_client_stream):
    pytest.importorskip("anthropic.resources.beta.messages.messages")
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with anthropic_client_stream.beta.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
        for _ in stream.text_stream:
            pass

    assert mock_execute_request.call_count == 1


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
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


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_stream_sync_response_allow(mock_execute_request, anthropic_client_stream_buffered):
    """Buffered: ALLOW on response delivers full text after 2 evaluations."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with anthropic_client_stream_buffered.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
        chunks = list(stream.text_stream)

    assert "".join(chunks) == "ok"
    assert mock_execute_request.call_count == 2


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
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


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_create_stream_true_buffered_allow(mock_execute_request, anthropic_client_stream_buffered):
    """``Messages.create(stream=True)`` with buffering ALLOW delivers text after 2 evals."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = anthropic_client_stream_buffered.messages.create(messages=_user_messages(), stream=True, **CHAT_PARAMS)
    list(stream)
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
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
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
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
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
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
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
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


@patch("ddtrace.appsec._ai_guard._listener.reconstruct_anthropic", side_effect=RuntimeError("boom"))
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
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


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_wrappers_not_installed_when_flag_off(mock_execute_request, anthropic_client_stream):
    """Default (flag=False): stream is NOT wrapped in a BufferedAIGuardStream."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    result = anthropic_client_stream.messages.create(messages=_user_messages(), stream=True, **CHAT_PARAMS)
    assert not isinstance(result, BufferedAIGuardStream)
    list(result)


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_wrappers_installed_when_flag_on(mock_execute_request, anthropic_client_stream_buffered):
    """With flag=True: Messages.create(stream=True) returns a BufferedAIGuardStream."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    result = anthropic_client_stream_buffered.messages.create(messages=_user_messages(), stream=True, **CHAT_PARAMS)
    assert isinstance(result, BufferedAIGuardStream)
    list(result)


# ---------------------------------------------------------------------------
# Collision suppression on streaming
# ---------------------------------------------------------------------------


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_stream_collision_short_circuit(mock_execute_request, anthropic_client_stream, aiguard_active_context):
    """Stream nested inside an active framework AI Guard context skips the
    before-hook and still yields text normally.
    """
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with anthropic_client_stream.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
        chunks = list(stream.text_stream)

    assert "".join(chunks) == "ok"
    mock_execute_request.assert_not_called()
