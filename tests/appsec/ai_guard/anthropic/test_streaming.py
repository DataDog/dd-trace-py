"""Streaming AI Guard tests for the Anthropic SDK."""

from unittest.mock import patch

import pytest

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
        chunks = [text for text in stream.text_stream]

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
# Collision suppression on streaming
# ---------------------------------------------------------------------------


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_stream_collision_short_circuit(mock_execute_request, anthropic_client_stream, aiguard_active_context):
    """Stream nested inside an active framework AI Guard context skips the
    before-hook and still yields text normally.
    """
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with anthropic_client_stream.messages.stream(messages=_user_messages(), **CHAT_PARAMS) as stream:
        chunks = [text for text in stream.text_stream]

    assert "".join(chunks) == "ok"
    mock_execute_request.assert_not_called()
