from unittest.mock import patch

import pytest

from ddtrace.appsec._ai_guard._context import reset_aiguard_context_active
from ddtrace.appsec._ai_guard._context import set_aiguard_context_active
from ddtrace.appsec._ai_guard._openai import _convert_openai_messages
from ddtrace.appsec._ai_guard._openai import _convert_openai_response
from ddtrace.appsec.ai_guard import AIGuardAbortError
from tests.appsec.ai_guard.utils import mock_evaluate_response
from tests.appsec.ai_guard.utils import override_ai_guard_config


# ---------------------------------------------------------------------------
# Message conversion unit tests
# ---------------------------------------------------------------------------


class TestConvertOpenAIMessages:
    def test_basic_user_message(self):
        messages = [{"role": "user", "content": "Hello"}]
        result = _convert_openai_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello"

    def test_system_and_user_messages(self):
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hi"},
        ]
        result = _convert_openai_messages(messages)
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful"
        assert result[1]["role"] == "user"
        assert result[1]["content"] == "Hi"

    def test_assistant_with_tool_calls(self):
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"location": "NYC"}',
                        },
                    }
                ],
            }
        ]
        result = _convert_openai_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert "content" not in result[0]
        assert len(result[0]["tool_calls"]) == 1
        tc = result[0]["tool_calls"][0]
        assert tc["id"] == "call_123"
        assert tc["function"]["name"] == "get_weather"
        assert tc["function"]["arguments"] == '{"location": "NYC"}'

    def test_tool_message(self):
        messages = [{"role": "tool", "tool_call_id": "call_123", "content": "Sunny, 72F"}]
        result = _convert_openai_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "call_123"
        assert result[0]["content"] == "Sunny, 72F"

    def test_full_conversation(self):
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "NYC"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "72F and sunny"},
            {"role": "assistant", "content": "It's 72F and sunny in NYC."},
        ]
        result = _convert_openai_messages(messages)
        assert len(result) == 5
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"
        assert result[2]["tool_calls"][0]["function"]["name"] == "get_weather"
        assert result[3]["role"] == "tool"
        assert result[3]["tool_call_id"] == "call_1"
        assert result[4]["role"] == "assistant"
        assert result[4]["content"] == "It's 72F and sunny in NYC."

    def test_object_style_messages(self):
        """Messages can be SDK objects with attribute access instead of dict."""

        class MockMessage:
            def __init__(self, role, content, tool_calls=None, tool_call_id=None):
                self.role = role
                self.content = content
                self.tool_calls = tool_calls
                self.tool_call_id = tool_call_id

        messages = [MockMessage(role="user", content="Hello from object")]
        result = _convert_openai_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello from object"

    def test_empty_messages(self):
        assert _convert_openai_messages([]) == []

    def test_malformed_message_skipped(self):
        """Messages that raise during conversion are silently skipped."""
        messages = [None, {"role": "user", "content": "Valid"}]
        result = _convert_openai_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"


class TestConvertOpenAIResponse:
    def test_basic_response(self):
        class MockMessage:
            role = "assistant"
            content = "Hello!"
            tool_calls = None

        class MockChoice:
            message = MockMessage()

        class MockResp:
            choices = [MockChoice()]

        result = _convert_openai_response(MockResp())
        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Hello!"

    def test_response_with_tool_calls(self):
        class MockFunction:
            name = "get_weather"
            arguments = '{"city": "NYC"}'

        class MockToolCall:
            id = "call_1"
            function = MockFunction()

        class MockMessage:
            role = "assistant"
            content = None
            tool_calls = [MockToolCall()]

        class MockChoice:
            message = MockMessage()

        class MockResp:
            choices = [MockChoice()]

        result = _convert_openai_response(MockResp())
        assert len(result) == 1
        assert "content" not in result[0]
        assert result[0]["tool_calls"][0]["id"] == "call_1"
        assert result[0]["tool_calls"][0]["function"]["name"] == "get_weather"


# ---------------------------------------------------------------------------
# Before-model evaluation tests
# ---------------------------------------------------------------------------


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_sync_before_allow(mock_execute_request, openai_client):
    """Before-model ALLOW: evaluate called (before + after), response returned normally."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    resp = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Hello"}],
    )
    assert resp is not None
    # Both before-model and after-model evaluations fire
    assert mock_execute_request.call_count == 2


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_sync_before_block(mock_execute_request, openai_client, decision):
    """Before-model DENY/ABORT: AIGuardAbortError raised, func() never called."""
    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(AIGuardAbortError):
        openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
        )
    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_before_allow(mock_execute_request, async_openai_client):
    """Async before-model ALLOW: evaluate called (before + after), response returned."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    resp = await async_openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Hello"}],
    )
    assert resp is not None
    # Both before-model and after-model evaluations fire
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_before_block(mock_execute_request, async_openai_client, decision):
    """Async before-model DENY/ABORT: AIGuardAbortError raised."""
    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(AIGuardAbortError):
        await async_openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
        )
    mock_execute_request.assert_called_once()


# ---------------------------------------------------------------------------
# After-model evaluation tests
# ---------------------------------------------------------------------------


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_sync_after_allow(mock_execute_request, openai_client):
    """After-model ALLOW: two evaluate calls (before + after), response returned."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    resp = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Hello"}],
    )
    assert resp is not None
    # Before + after evaluations
    assert mock_execute_request.call_count == 2


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_sync_after_block(mock_execute_request, openai_client):
    """After-model DENY: first ALLOW (before), second DENY (after) -> error after response."""
    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),
        mock_evaluate_response("DENY"),
    ]

    with pytest.raises(AIGuardAbortError):
        openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
        )
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_after_allow(mock_execute_request, async_openai_client):
    """Async after-model ALLOW: two evaluate calls, response returned."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    resp = await async_openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Hello"}],
    )
    assert resp is not None
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_after_block(mock_execute_request, async_openai_client):
    """Async after-model: first ALLOW, second DENY -> error."""
    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),
        mock_evaluate_response("DENY"),
    ]

    with pytest.raises(AIGuardAbortError):
        await async_openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
        )
    assert mock_execute_request.call_count == 2


# ---------------------------------------------------------------------------
# Collision avoidance tests
# ---------------------------------------------------------------------------


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_collision_avoidance_skips_evaluation(mock_execute_request, openai_client):
    """When _ai_guard_active is True, evaluate is NOT called."""
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    token = set_aiguard_context_active()
    try:
        resp = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert resp is not None
        mock_execute_request.assert_not_called()
    finally:
        reset_aiguard_context_active(token)


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_collision_avoidance_async_skips_evaluation(mock_execute_request, async_openai_client):
    """Async collision avoidance: evaluate is NOT called when flag is active."""
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    token = set_aiguard_context_active()
    try:
        resp = await async_openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert resp is not None
        mock_execute_request.assert_not_called()
    finally:
        reset_aiguard_context_active(token)


# ---------------------------------------------------------------------------
# DD_AI_GUARD_BLOCK=false tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_sync_block_config_disabled(mock_execute_request, openai_client, decision):
    """When _ai_guard_block=False, DENY/ABORT should NOT raise AIGuardAbortError."""
    mock_execute_request.return_value = mock_evaluate_response(decision, block=True)

    with override_ai_guard_config(dict(_ai_guard_block=False)):
        resp = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert resp is not None
        mock_execute_request.assert_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_block_config_disabled(mock_execute_request, async_openai_client, decision):
    """Async: when _ai_guard_block=False, no AIGuardAbortError."""
    mock_execute_request.return_value = mock_evaluate_response(decision, block=True)

    with override_ai_guard_config(dict(_ai_guard_block=False)):
        resp = await async_openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert resp is not None
        mock_execute_request.assert_called()


# ---------------------------------------------------------------------------
# Streaming tests (before-model only, after-model skipped for streaming)
# ---------------------------------------------------------------------------


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_streaming_before_allow(mock_execute_request, openai_client):
    """Streaming: before-model works, after-model is skipped."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Hello"}],
        stream=True,
    )
    for _ in stream:
        pass
    # Only before-model evaluation, after-model skipped for streaming
    mock_execute_request.assert_called_once()


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_streaming_before_block(mock_execute_request, openai_client, decision):
    """Streaming before-model block: AIGuardAbortError raised before stream starts."""
    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(AIGuardAbortError):
        openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
            stream=True,
        )
    mock_execute_request.assert_called_once()


# ---------------------------------------------------------------------------
# Non-user-message skipping
# ---------------------------------------------------------------------------


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_skips_when_last_message_not_user(mock_execute_request, openai_client):
    """Before-model skips evaluation when the last message is not from 'user'."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ],
    )
    # Before-model skipped (last message is assistant), only after-model runs
    mock_execute_request.assert_called_once()
