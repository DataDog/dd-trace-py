import asyncio
import contextvars
import threading
from unittest.mock import patch

import pytest

from ddtrace.appsec._ai_guard._context import _ai_guard_active
from ddtrace.appsec._ai_guard._context import is_aiguard_context_active
from ddtrace.appsec._ai_guard._context import reset_aiguard_context_active
from ddtrace.appsec._ai_guard._context import set_aiguard_context_active
from ddtrace.appsec._ai_guard._openai import _convert_openai_messages
from ddtrace.appsec._ai_guard._openai import _convert_openai_response
from ddtrace.appsec.ai_guard import AIGuardAbortError
from tests.appsec.ai_guard.utils import mock_evaluate_response
from tests.appsec.ai_guard.utils import override_ai_guard_config


CHAT_PROMPT = "When do you use 'whom' instead of 'who'?"
CHAT_MODEL = "gpt-3.5-turbo"
# NOTE: request params (``stream=False``, ``temperature=0.0`` as float, and
# message key order ``content`` before ``role``) are chosen so the OpenAI SDK
# serializes a body that byte-matches the 200-response testagent VCR cassette
# at tests/appsec/_cassettes/openai/openai_chat_completions_post_caac525c.json.
# The dd-apm-test-agent VCR derives the cassette hash from a sorted-keys JSON
# dump, so key order does not matter for matching — but float/int type does
# (``0.0`` vs ``0`` hash differently, picking different cassette responses).
CHAT_PARAMS = dict(model=CHAT_MODEL, max_tokens=256, n=1, temperature=0.0, stream=False)


def _user_messages(content=CHAT_PROMPT):
    return [{"content": content, "role": "user"}]


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
# Chat completions (sync) — before-model allow / block
# ---------------------------------------------------------------------------


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_sync_allow(mock_execute_request, openai_client):
    """ALLOW: before + after evaluations fire, response returned from VCR."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    resp = openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    assert resp is not None
    # Before + after evaluations
    assert mock_execute_request.call_count == 2


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_sync_block(mock_execute_request, openai_client, decision):
    """DENY/ABORT before-model: AIGuardAbortError raised, OpenAI never called."""
    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(AIGuardAbortError):
        openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    mock_execute_request.assert_called_once()


# ---------------------------------------------------------------------------
# Chat completions (async) — before-model allow / block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_allow(mock_execute_request, async_openai_client):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    resp = await async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    assert resp is not None
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_block(mock_execute_request, async_openai_client, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(AIGuardAbortError):
        await async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    mock_execute_request.assert_called_once()


# ---------------------------------------------------------------------------
# After-model evaluation: ALLOW before, DENY after
# ---------------------------------------------------------------------------


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_sync_after_block(mock_execute_request, openai_client):
    """After-model DENY: first ALLOW (before), second DENY (after) -> error after response."""
    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),
        mock_evaluate_response("DENY"),
    ]

    with pytest.raises(AIGuardAbortError):
        openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_after_block(mock_execute_request, async_openai_client):
    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),
        mock_evaluate_response("DENY"),
    ]

    with pytest.raises(AIGuardAbortError):
        await async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    assert mock_execute_request.call_count == 2


# ---------------------------------------------------------------------------
# DD_AI_GUARD_BLOCK=false
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_sync_block_config_disabled(mock_execute_request, openai_client, decision):
    """When _ai_guard_block=False, DENY/ABORT should NOT raise AIGuardAbortError."""
    mock_execute_request.return_value = mock_evaluate_response(decision, block=True)

    with override_ai_guard_config(dict(_ai_guard_block=False)):
        resp = openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)
        assert resp is not None
        mock_execute_request.assert_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_block_config_disabled(mock_execute_request, async_openai_client, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision, block=True)

    with override_ai_guard_config(dict(_ai_guard_block=False)):
        resp = await async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)
        assert resp is not None
        mock_execute_request.assert_called()


# ---------------------------------------------------------------------------
# Streaming: before-model runs, after-model skipped.
#
# Uses the ``*_stream`` fixtures that mock the httpx transport — ddtrace's
# openai patch mutates streaming requests by injecting
# ``stream_options={"include_usage": True}`` before the HTTP call, and no
# 200-response testagent VCR cassette matches that mutated body. The AI
# Guard dispatch path runs before the mutation so the behaviour under test
# is unaffected.
# ---------------------------------------------------------------------------


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_streaming_sync_allow(mock_execute_request, openai_client_stream):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = openai_client_stream.chat.completions.create(model=CHAT_MODEL, messages=_user_messages(), stream=True)
    for _ in stream:
        pass

    # Only the before-model evaluation; after-model is skipped for streaming
    mock_execute_request.assert_called_once()


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_streaming_sync_block(mock_execute_request, openai_client_stream, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(AIGuardAbortError):
        openai_client_stream.chat.completions.create(model=CHAT_MODEL, messages=_user_messages(), stream=True)

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_streaming_async_allow(mock_execute_request, async_openai_client_stream):
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = await async_openai_client_stream.chat.completions.create(
        model=CHAT_MODEL, messages=_user_messages(), stream=True
    )
    async for _ in stream:
        pass

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_streaming_async_block(mock_execute_request, async_openai_client_stream, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(AIGuardAbortError):
        await async_openai_client_stream.chat.completions.create(
            model=CHAT_MODEL, messages=_user_messages(), stream=True
        )

    mock_execute_request.assert_called_once()


# ---------------------------------------------------------------------------
# Collision avoidance: when a framework has already claimed evaluation
# ---------------------------------------------------------------------------


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_collision_avoidance_skips_evaluation(mock_execute_request, openai_client):
    """When _ai_guard_active is True, evaluate is NOT called."""
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    token = set_aiguard_context_active()
    try:
        resp = openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)
        assert resp is not None
        mock_execute_request.assert_not_called()
    finally:
        reset_aiguard_context_active(token)


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_collision_avoidance_async_skips_evaluation(mock_execute_request, async_openai_client):
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    token = set_aiguard_context_active()
    try:
        resp = await async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)
        assert resp is not None
        mock_execute_request.assert_not_called()
    finally:
        reset_aiguard_context_active(token)


def test_reset_same_context_restores_previous_value():
    assert is_aiguard_context_active() is False
    token = set_aiguard_context_active()
    assert is_aiguard_context_active() is True
    reset_aiguard_context_active(token)
    assert is_aiguard_context_active() is False


# ---------------------------------------------------------------------------
# Before-model skip when the last message is not role="user"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "messages",
    [
        pytest.param(
            [{"role": "system", "content": "You are helpful."}],
            id="last-system",
        ),
        pytest.param(
            [
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"},
            ],
            id="last-assistant",
        ),
        pytest.param(
            [
                {"role": "user", "content": "What's the weather?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "72F and sunny"},
            ],
            id="last-tool",
        ),
    ],
)
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_before_skips_when_last_message_not_user(mock_execute_request, openai_client_mock, messages):
    """Before-model evaluation MUST be skipped when the last message is not
    role="user". The after-model evaluation still fires for the response.

    Uses the mock-transport client so arbitrary message shapes don't depend
    on a matching testagent VCR cassette.
    """
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    resp = openai_client_mock.chat.completions.create(messages=messages, **CHAT_PARAMS)

    assert resp is not None
    # Only the after-model evaluation fired — before was skipped.
    assert mock_execute_request.call_count == 1


# ---------------------------------------------------------------------------
# Fail-open: client.evaluate raises a non-abort exception
# ---------------------------------------------------------------------------


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_sync_fails_open_on_non_abort_error(mock_execute_request, openai_client):
    """If client.evaluate raises anything other than AIGuardAbortError, the
    OpenAI call MUST proceed (fail-open) — a broken AI Guard service must
    never break user applications.
    """
    mock_execute_request.side_effect = RuntimeError("ai-guard service unreachable")

    resp = openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    assert resp is not None
    # evaluate() was attempted — twice (before + after), both fail open.
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_fails_open_on_non_abort_error(mock_execute_request, async_openai_client):
    mock_execute_request.side_effect = RuntimeError("ai-guard service unreachable")

    resp = await async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    assert resp is not None
    assert mock_execute_request.call_count == 2


# ---------------------------------------------------------------------------
# Concurrent contexts: _ai_guard_active in one task must not bleed into another
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_concurrent_tasks_isolate_ai_guard_active(mock_execute_request, async_openai_client):
    """Two coroutines run concurrently: one holds _ai_guard_active=True (as a
    framework integration would), the other must still evaluate. ContextVar
    semantics guarantee this — this test pins the guarantee so a future refactor
    to a thread-local or module-global flag fails loudly.
    """
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    active_entered = asyncio.Event()
    release_active = asyncio.Event()

    async def _framework_task():
        token = set_aiguard_context_active()
        try:
            active_entered.set()
            await release_active.wait()
        finally:
            reset_aiguard_context_active(token)

    async def _provider_task():
        await active_entered.wait()
        try:
            # _ai_guard_active is True in the other task's Context, but MUST be
            # False here — so evaluate() must fire.
            assert is_aiguard_context_active() is False
            resp = await async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)
            assert resp is not None
        finally:
            release_active.set()

    await asyncio.gather(_framework_task(), _provider_task())
    # Provider task ran a full before+after evaluation.
    assert mock_execute_request.call_count == 2
    # And the flag is back to False in the caller's Context.
    assert is_aiguard_context_active() is False


def test_concurrent_threads_isolate_ai_guard_active():
    """contextvars are copied-per-thread at thread start (see PEP 567). A thread
    that sets _ai_guard_active=True MUST NOT affect the main thread's value.
    """
    barrier = threading.Barrier(2)
    observed_in_main = []

    def _worker():
        set_aiguard_context_active()
        barrier.wait()  # release main; worker exits with active=True in its own Context
        barrier.wait()  # hold until main has observed

    t = threading.Thread(target=_worker)
    t.start()
    barrier.wait()
    observed_in_main.append(is_aiguard_context_active())
    barrier.wait()
    t.join()

    assert observed_in_main == [False]
    assert is_aiguard_context_active() is False


def test_reset_cross_context_does_not_raise():
    # Simulates LangChain stream iteration: set() happens in the outer Context,
    # but reset() runs inside context.run() — a copied Context. ContextVar.reset
    # would raise ValueError for a foreign token; the helper must degrade to
    # clearing the flag in the current Context copy instead.
    token = set_aiguard_context_active()
    try:
        ctx = contextvars.copy_context()

        def _reset_in_child_context():
            assert _ai_guard_active.get() is True
            reset_aiguard_context_active(token)
            assert _ai_guard_active.get() is False

        ctx.run(_reset_in_child_context)
        assert is_aiguard_context_active() is True
    finally:
        reset_aiguard_context_active(token)
    assert is_aiguard_context_active() is False
