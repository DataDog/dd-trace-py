"""Integration and unit tests for the AI Guard + OpenAI integration."""

import asyncio
import gc
import threading
from unittest.mock import patch
import warnings

import pytest

import ddtrace.appsec._ai_guard as ai_guard_mod
from ddtrace.appsec._ai_guard._context import is_aiguard_context_active
from ddtrace.appsec._ai_guard._context import reset_aiguard_context_active
from ddtrace.appsec._ai_guard._context import set_aiguard_context_active
from ddtrace.appsec._ai_guard._openai_chat import _convert_openai_messages
from ddtrace.appsec._ai_guard._openai_chat import _convert_openai_response
from ddtrace.appsec._ai_guard._openai_chat import _openai_chat_completion_before
from ddtrace.appsec.ai_guard import AIGuardAbortError
from tests.appsec.ai_guard.openai._span_helpers import assert_block_emits_both_spans
from tests.appsec.ai_guard.utils import mock_evaluate_response
from tests.appsec.ai_guard.utils import override_ai_guard_config


@pytest.fixture
def aiguard_active_context():
    """Mark the current task as under active AI Guard evaluation for the test.

    Always resets on teardown — even if the test raises — so subsequent tests
    do not observe a leaked active counter.
    """
    token = set_aiguard_context_active()
    try:
        yield
    finally:
        reset_aiguard_context_active(token)


@pytest.fixture
def reset_ai_guard_loaded():
    """Save and restore ``ai_guard_mod._AI_GUARD_TO_BE_LOADED`` around each test.

    ``init_ai_guard()`` flips this flag to ``False`` after its first successful
    call (idempotency guard). Tests that exercise registration need to flip it
    back to ``True`` to re-enter the branch; this fixture guarantees the module
    global is restored to its pre-test value regardless of what the test did.
    """
    original = ai_guard_mod._AI_GUARD_TO_BE_LOADED
    try:
        yield
    finally:
        ai_guard_mod._AI_GUARD_TO_BE_LOADED = original


CHAT_PROMPT = "When do you use 'whom' instead of 'who'?"
CHAT_MODEL = "gpt-3.5-turbo"
CHAT_PARAMS = dict(model=CHAT_MODEL, max_tokens=256, n=1, temperature=0.0, stream=False)

TOOL_CALL_MODEL = "gpt-3.5-turbo-0125"
TOOL_CALL_PROMPT = "What is the sum of 1 and 2?"
ADD_TOOL = {
    "type": "function",
    "function": {
        "name": "add",
        "description": (
            "add(a: int, b: int) -> int - Adds a and b.\n"
            "        Args:\n"
            "            a: first int\n"
            "            b: second int"
        ),
        "parameters": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    },
}


def _user_messages(content=CHAT_PROMPT):
    return [{"content": content, "role": "user"}]


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

    def test_legacy_function_call_in_assistant_message(self):
        """Legacy ``function_call`` on an assistant message gets translated to a
        synthetic ``ToolCall`` with id ``fc_0`` since AI Guard's Message schema
        only models ``tool_calls``.
        """
        messages = [
            {
                "role": "assistant",
                "content": None,
                "function_call": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
            }
        ]
        result = _convert_openai_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert "content" not in result[0]
        assert len(result[0]["tool_calls"]) == 1
        tc = result[0]["tool_calls"][0]
        assert tc["id"] == "fc_0"
        assert tc["function"]["name"] == "get_weather"
        assert tc["function"]["arguments"] == '{"city": "Paris"}'

    def test_legacy_function_role_message_paired_to_tool(self):
        """``role="function"`` is rewritten to ``role="tool"`` and its
        ``tool_call_id`` is taken from the FIFO of preceding ``function_call``s.
        """
        messages = [
            {
                "role": "assistant",
                "content": None,
                "function_call": {"name": "get_weather", "arguments": "{}"},
            },
            {"role": "function", "name": "get_weather", "content": "72F and sunny"},
        ]
        result = _convert_openai_messages(messages)
        assert len(result) == 2
        assert result[1]["role"] == "tool"
        assert result[1]["tool_call_id"] == "fc_0"
        assert result[1]["content"] == "72F and sunny"

    def test_legacy_function_call_pairing_fifo_order(self):
        """Multiple ``function_call``s are paired with following ``role="function"``
        results in FIFO order: ``fc_0`` → first result, ``fc_1`` → second.
        """
        messages = [
            {"role": "assistant", "content": None, "function_call": {"name": "fn_a", "arguments": "{}"}},
            {"role": "assistant", "content": None, "function_call": {"name": "fn_b", "arguments": "{}"}},
            {"role": "function", "name": "fn_a", "content": "result_a"},
            {"role": "function", "name": "fn_b", "content": "result_b"},
        ]
        result = _convert_openai_messages(messages)
        assert result[0]["tool_calls"][0]["id"] == "fc_0"
        assert result[1]["tool_calls"][0]["id"] == "fc_1"
        assert result[2]["role"] == "tool"
        assert result[2]["tool_call_id"] == "fc_0"
        assert result[2]["content"] == "result_a"
        assert result[3]["role"] == "tool"
        assert result[3]["tool_call_id"] == "fc_1"
        assert result[3]["content"] == "result_b"

    def test_legacy_function_role_without_pending_pairing(self):
        """A ``role="function"`` message with no preceding ``function_call``
        gets an empty ``tool_call_id`` (matches LiteLLM behavior).
        """
        messages = [{"role": "function", "name": "orphan", "content": "stray"}]
        result = _convert_openai_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == ""
        assert result[0]["content"] == "stray"

    def test_legacy_mixed_tool_calls_and_function_call(self):
        """An assistant message with both modern ``tool_calls`` and legacy
        ``function_call`` keeps both — the legacy one is appended last with a
        synthetic id.
        """
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_modern",
                        "type": "function",
                        "function": {"name": "fn_modern", "arguments": "{}"},
                    }
                ],
                "function_call": {"name": "fn_legacy", "arguments": "{}"},
            }
        ]
        result = _convert_openai_messages(messages)
        assert len(result) == 1
        tool_calls = result[0]["tool_calls"]
        assert len(tool_calls) == 2
        assert tool_calls[0]["id"] == "call_modern"
        assert tool_calls[0]["function"]["name"] == "fn_modern"
        assert tool_calls[1]["id"] == "fc_0"
        assert tool_calls[1]["function"]["name"] == "fn_legacy"


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

    def test_response_with_legacy_function_call(self):
        """Legacy ``function_call`` on a response message is translated to a
        synthetic ``ToolCall`` with id ``fc_<hex>`` (response-side scheme — no
        FIFO needed since each choice is independent).
        """

        class MockFunctionCall:
            name = "get_weather"
            arguments = '{"city": "NYC"}'

        class MockMessage:
            role = "assistant"
            content = None
            tool_calls = None
            function_call = MockFunctionCall()

        class MockChoice:
            message = MockMessage()

        class MockResp:
            choices = [MockChoice()]

        result = _convert_openai_response(MockResp())
        assert len(result) == 1
        assert "content" not in result[0]
        assert len(result[0]["tool_calls"]) == 1
        tc = result[0]["tool_calls"][0]
        assert tc["id"].startswith("fc_")
        assert tc["function"]["name"] == "get_weather"
        assert tc["function"]["arguments"] == '{"city": "NYC"}'


# ---------------------------------------------------------------------------
# Listener input handling — non-list iterables
# ---------------------------------------------------------------------------


def test_before_hook_materializes_iterator_messages():
    """The OpenAI SDK accepts ``messages`` as ``Iterable``, so callers may
    pass a generator. The before-hook must materialize it back into ``kwargs``
    so the SDK still sees the messages after AI Guard reads them.

    Regression: previously the hook iterated the generator into
    ``_convert_openai_messages`` and the SDK then received an exhausted
    iterator (empty request).
    """

    class _RecordingClient:
        def __init__(self):
            self.evaluated = None

        def evaluate(self, messages, options):
            self.evaluated = list(messages)
            return None

    def _gen():
        yield {"role": "user", "content": "Hello"}

    client = _RecordingClient()
    kwargs = {"messages": _gen()}

    result = _openai_chat_completion_before(client, kwargs)

    assert result is None
    # SDK-visible payload: still iterable, with the original message preserved.
    assert isinstance(kwargs["messages"], list)
    assert kwargs["messages"] == [{"role": "user", "content": "Hello"}]
    # AI Guard saw the messages too (would be empty if we materialized after iteration).
    assert client.evaluated and client.evaluated[0]["role"] == "user"


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


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_sync_block_is_openai_compatible_exception(mock_execute_request, openai_client):
    """A blocked request raises an exception that satisfies BOTH the OpenAI
    SDK error hierarchy (``openai.APIError`` / ``openai.UnprocessableEntityError``,
    HTTP 422) and the Datadog ``AIGuardAbortError`` interface.

    Pins the dual-handler contract: existing ``except openai.APIError`` blocks
    keep working unchanged, AND advanced users can branch on
    ``isinstance(e, AIGuardAbortError)`` to read ``action`` / ``reason``.
    """
    import openai

    mock_execute_request.return_value = mock_evaluate_response("DENY")

    with pytest.raises(openai.UnprocessableEntityError) as exc_info:
        openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    err = exc_info.value
    assert isinstance(err, openai.APIError)
    assert isinstance(err, openai.UnprocessableEntityError)
    assert isinstance(err, AIGuardAbortError)
    assert err.status_code == 422
    assert err.action == "DENY"
    # ``reason`` is mirrored from the AI Guard response (may be empty in mocks).
    assert hasattr(err, "reason")
    # __cause__ chains the original AIGuardAbortError so it shows up in tracebacks.
    assert isinstance(err.__cause__, AIGuardAbortError)


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_block_error_class_identity_across_consecutive_blocks(mock_execute_request, openai_client):
    """Two consecutive DENY blocks MUST raise errors of the same class.

    Pins the cache contract in ``_get_openai_abort_error_cls`` so a regression
    that rebuilds the OpenAI-compatible abort class on every block (different
    ``type(e)`` per raise) is caught: user code that captured ``type(err)``
    from a prior block — or a thread race that overwrites the cached class
    after a partial build — would silently stop matching subsequent blocks.
    """
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    with pytest.raises(AIGuardAbortError) as exc_first:
        openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)
    with pytest.raises(AIGuardAbortError) as exc_second:
        openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    assert type(exc_first.value) is type(exc_second.value)
    # And it's the OpenAI-compatible subclass, not bare AIGuardAbortError.
    assert type(exc_first.value).__name__ == "OpenAIAIGuardAbortError"


def test_block_error_class_identity_under_thread_race():
    """Concurrent threads racing through the cold cache MUST all observe the
    same class object.

    Without ``_compound_abort_error_lock``, the unsynchronised check-then-act
    pattern lets two threads pass the ``is None`` check, build their own class
    each, and leave the cache pointing at whichever assignment ran last — the
    other thread keeps a stale class that's never returned again, so a caller's
    ``isinstance(e, cached_cls)`` will start failing intermittently. This test
    resets the shared compound-class cache and races N threads through the
    helper, asserting all returns are ``is``-equal.
    """
    import ddtrace.appsec._ai_guard._common as _common
    import ddtrace.appsec._ai_guard._openai as _openai_mod

    # Sanity: the openai SDK must be importable for this test to be meaningful;
    # if it's not, the helper returns None and there is no class identity to
    # compare.
    if _openai_mod._get_openai_abort_error_cls() is None:
        pytest.skip("openai SDK not importable")

    saved_cls = _common._compound_abort_error_cache.pop("OpenAI", None)

    n = 32
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def _race(idx: int) -> None:
        # Force all threads to enter the helper at the same moment so the
        # check-then-act window is genuinely contended.
        barrier.wait()
        results[idx] = _openai_mod._get_openai_abort_error_cls()

    try:
        threads = [threading.Thread(target=_race, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        if saved_cls is not None:
            _common._compound_abort_error_cache["OpenAI"] = saved_cls

    first = results[0]
    assert first is not None
    distinct_ids = {id(cls) for cls in results}
    assert len(distinct_ids) == 1, (
        f"concurrent _get_openai_abort_error_cls() returned {len(distinct_ids)} distinct class "
        f"objects; the lazy cache build is not thread-safe"
    )


# ---------------------------------------------------------------------------
# Chat completions (async) — before-model allow / block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_allow(mock_execute_request, async_openai_client):
    """ALLOW (async): before + after evaluations fire, response returned from VCR."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    resp = await async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    assert resp is not None
    assert mock_execute_request.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_block(mock_execute_request, async_openai_client, decision):
    """DENY/ABORT before-model (async): AIGuardAbortError raised, OpenAI never called."""
    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(AIGuardAbortError):
        await async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_block_raises_at_await_time(mock_execute_request, async_openai_client):
    """The async wrapper MUST run AI Guard before-evaluation at ``await`` time,
    not at coroutine construction time. Pins ``async def`` semantics: callers
    using ``asyncio.wait_for`` / ``gather`` / ``create_task`` rely on cheap
    construction, with work (and any errors) deferred to scheduling/await.
    """
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    coro = async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)
    try:
        # Construction MUST be side-effect free — no AI Guard call yet.
        mock_execute_request.assert_not_called()

        with pytest.raises(AIGuardAbortError):
            await coro
    finally:
        # In the success branch the await consumed it; in any failure branch
        # close it defensively so we don't leak an unfinished coroutine.
        if hasattr(coro, "close"):
            coro.close()

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_unawaited_coro_does_not_evaluate(mock_execute_request, async_openai_client):
    """If the caller never awaits the returned coroutine, the AI Guard
    evaluation MUST NOT run. Otherwise we'd be evaluating (and billing) a
    request that never happened — and surfacing block errors out of-band of
    the await the caller controls.
    """
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    coro = async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)
    coro.close()  # discard without awaiting

    mock_execute_request.assert_not_called()


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_block_closes_unstarted_sdk_coroutine(mock_execute_request, async_openai_client):
    """When AI Guard blocks at await time, the unstarted SDK coroutine MUST be
    closed so Python doesn't emit ``RuntimeWarning: coroutine ... was never
    awaited`` for it. Force GC inside the ``catch_warnings`` block so any
    unclosed-coroutine warning surfaces while ``simplefilter("error")`` is
    still active — the warning fires from ``coroutine.__del__``, not from
    the raise itself, so a naive context exit would let it leak past the
    assertion.
    """
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        with pytest.raises(AIGuardAbortError):
            await async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)
        gc.collect()

    never_awaited = [
        w for w in recorded if issubclass(w.category, RuntimeWarning) and "was never awaited" in str(w.message)
    ]
    assert not never_awaited, f"Unstarted SDK coroutine leaked: {[str(w.message) for w in never_awaited]}"


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
    """After-model DENY (async): first ALLOW (before), second DENY (after) -> error after response."""
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
    """When _ai_guard_block=False (async), DENY/ABORT should NOT raise AIGuardAbortError."""
    mock_execute_request.return_value = mock_evaluate_response(decision, block=True)

    with override_ai_guard_config(dict(_ai_guard_block=False)):
        resp = await async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)
        assert resp is not None
        mock_execute_request.assert_called()


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_streaming_sync_evaluates_input(mock_execute_request, openai_client_stream):
    """ALLOW: streaming sync call invokes AI Guard once on the input messages."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = openai_client_stream.chat.completions.create(model=CHAT_MODEL, messages=_user_messages(), stream=True)
    for _ in stream:
        pass

    mock_execute_request.assert_called_once()


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_streaming_sync_blocks_on_deny(mock_execute_request, openai_client_stream, decision):
    """DENY/ABORT before-model on streaming: AIGuardAbortError raised by
    ``chat.completions.create`` itself, before any iteration; OpenAI HTTP call
    is never reached.
    """
    import openai

    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(openai.UnprocessableEntityError) as exc_info:
        openai_client_stream.chat.completions.create(model=CHAT_MODEL, messages=_user_messages(), stream=True)

    assert isinstance(exc_info.value, AIGuardAbortError)
    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_streaming_async_evaluates_input(mock_execute_request, async_openai_client_stream):
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
async def test_chat_streaming_async_blocks_on_deny(mock_execute_request, async_openai_client_stream, decision):
    import openai

    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(openai.UnprocessableEntityError) as exc_info:
        await async_openai_client_stream.chat.completions.create(
            model=CHAT_MODEL, messages=_user_messages(), stream=True
        )

    assert isinstance(exc_info.value, AIGuardAbortError)
    mock_execute_request.assert_called_once()


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_streaming_after_hook_not_invoked(mock_execute_request, openai_client_stream):
    """Phase B contract pin: streamed responses are NOT re-evaluated at end-of-stream.

    AI Guard mirrors LangChain's input-only pattern for streaming today; only
    the before-hook fires. A regression that wires up an end-of-stream after
    evaluator (e.g. by lifting the ``not kwargs.get("stream")`` gate at the
    dispatch site) would surface here as a second ``_execute_request`` call.
    """
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = openai_client_stream.chat.completions.create(model=CHAT_MODEL, messages=_user_messages(), stream=True)
    for _ in stream:
        pass

    assert mock_execute_request.call_count == 1


# ---------------------------------------------------------------------------
# Span observability on AI Guard block
#
# AI Guard's before-hook is dispatched *inside* the ``_traced_endpoint``
# generator lifecycle (after the openai/LLMObs span is created). On block,
# the AIGuardAbortError raised by the dispatch flows through the existing
# ``except BaseException`` arm in ``_patched_endpoint`` and the ``g.send((None, err))``
# in ``finally`` finishes the openai span with ``set_exc_info``. Net result:
# both spans are emitted — the AI Guard span (resource ``ai_guard`` with
# ``action`` / ``blocked`` tags) and the LLMObs/openai span (with
# ``error.type`` set to the abort exception). These tests pin that contract.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_block_emits_ai_guard_and_openai_spans(mock_execute_request, openai_client_mock, test_spans, decision):
    """Non-streaming sync block: both AI Guard and openai/LLMObs spans are emitted."""
    import openai

    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(openai.UnprocessableEntityError):
        openai_client_mock.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    assert_block_emits_both_spans(test_spans, decision)


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_block_emits_ai_guard_and_openai_spans(
    mock_execute_request, async_openai_client_mock, test_spans, decision
):
    """Non-streaming async block: both AI Guard and openai/LLMObs spans are emitted."""
    import openai

    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(openai.UnprocessableEntityError):
        await async_openai_client_mock.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    assert_block_emits_both_spans(test_spans, decision)


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_streaming_sync_block_emits_ai_guard_and_openai_spans(
    mock_execute_request, openai_client_stream, test_spans, decision
):
    """Streaming sync block: AI Guard span AND openai LLMObs span are emitted with error info."""
    import openai

    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(openai.UnprocessableEntityError):
        openai_client_stream.chat.completions.create(model=CHAT_MODEL, messages=_user_messages(), stream=True)

    assert_block_emits_both_spans(test_spans, decision)


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_streaming_async_block_emits_ai_guard_and_openai_spans(
    mock_execute_request, async_openai_client_stream, test_spans, decision
):
    """Streaming async block: AI Guard span AND openai LLMObs span are emitted with error info."""
    import openai

    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(openai.UnprocessableEntityError):
        await async_openai_client_stream.chat.completions.create(
            model=CHAT_MODEL, messages=_user_messages(), stream=True
        )

    assert_block_emits_both_spans(test_spans, decision)


# ---------------------------------------------------------------------------
# Collision avoidance: when a framework has already claimed evaluation
# ---------------------------------------------------------------------------


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_collision_avoidance_skips_evaluation(mock_execute_request, openai_client, aiguard_active_context):
    """When _ai_guard_active is True, evaluate is NOT called."""
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    resp = openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)
    assert resp is not None
    mock_execute_request.assert_not_called()


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_collision_avoidance_async_skips_evaluation(mock_execute_request, async_openai_client):
    """Async collision avoidance: when the calling task has already claimed
    AI Guard evaluation, the provider listener MUST NOT call evaluate.

    The active flag is set inline (rather than via a sync fixture) so it lives
    in the same asyncio task that runs the OpenAI call — this mirrors how
    production framework integrations (LangChain, Strands) acquire the flag
    inside the async invocation that owns the LLM call.
    """
    from ddtrace.appsec._ai_guard._context import aiguard_context

    mock_execute_request.return_value = mock_evaluate_response("DENY")

    with aiguard_context():
        resp = await async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    assert resp is not None
    mock_execute_request.assert_not_called()


def test_reset_same_context_restores_previous_value():
    assert is_aiguard_context_active() is False
    token = set_aiguard_context_active()
    assert is_aiguard_context_active() is True
    reset_aiguard_context_active(token)
    assert is_aiguard_context_active() is False


# ---------------------------------------------------------------------------
# Before-model gating by last-message role
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
    ],
)
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_before_skips_when_last_message_not_user_or_tool(mock_execute_request, openai_client_mock, messages):
    """Before-model evaluation MUST be skipped when the last message is not
    role="user" or role="tool". The after-model evaluation still fires for the
    response.

    Uses the mock-transport client so arbitrary message shapes don't depend
    on a matching testagent VCR cassette.
    """
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    resp = openai_client_mock.chat.completions.create(messages=messages, **CHAT_PARAMS)

    assert resp is not None
    # Only the after-model evaluation fired — before was skipped.
    assert mock_execute_request.call_count == 1


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_before_fires_when_last_message_is_tool(mock_execute_request, openai_client_mock):
    """Before-model evaluation MUST fire when the last message is role="tool"
    (tool result feeding back into the next model call). Per the AI Guard spec,
    tool results are evaluated either "after tool" (framework hook) or at
    "next before model" — provider SDKs only have the latter, so this is the
    prevention window for indirect prompt injection in tool output.
    """
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    messages = [
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
    ]
    resp = openai_client_mock.chat.completions.create(messages=messages, **CHAT_PARAMS)

    assert resp is not None
    # Both before-model (tool result) and after-model (response) evaluations fired.
    assert mock_execute_request.call_count == 2
    _, before_payload = mock_execute_request.call_args_list[0].args
    before_messages = before_payload["data"]["attributes"]["messages"]
    assert before_messages[-1]["role"] == "tool"
    assert before_messages[-1]["content"] == "72F and sunny"


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_before_fires_when_last_message_is_function_role(mock_execute_request, openai_client_mock):
    """Legacy ``role="function"`` messages must trigger before-model evaluation
    too. Translation rewrites them to ``role="tool"`` upstream of the role
    gate, so the same prevention window applies for legacy function-calling
    flows (deprecated but still supported on ``openai>=1.102.0``).
    """
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    messages = [
        {"role": "user", "content": "What's the weather?"},
        {
            "role": "assistant",
            "content": None,
            "function_call": {"name": "get_weather", "arguments": "{}"},
        },
        {"role": "function", "name": "get_weather", "content": "72F and sunny"},
    ]
    resp = openai_client_mock.chat.completions.create(messages=messages, **CHAT_PARAMS)

    assert resp is not None
    assert mock_execute_request.call_count == 2
    _, before_payload = mock_execute_request.call_args_list[0].args
    before_messages = before_payload["data"]["attributes"]["messages"]
    # function-role rewritten to tool, paired with the synthetic id from the
    # preceding function_call.
    assert before_messages[-1]["role"] == "tool"
    assert before_messages[-1]["tool_call_id"] == "fc_0"
    assert before_messages[-1]["content"] == "72F and sunny"
    # Assistant message carries the synthesized tool_call.
    assert before_messages[-2]["role"] == "assistant"
    assert before_messages[-2]["tool_calls"][0]["id"] == "fc_0"
    assert before_messages[-2]["tool_calls"][0]["function"]["name"] == "get_weather"


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
    """Async fail-open: a non-abort exception from the AI Guard service must not block the OpenAI call."""
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


@pytest.mark.asyncio
async def test_reentrant_set_reset_in_same_coroutine():
    """A single coroutine that sets, re-sets nested, then resets nested-first
    MUST observe the outer flag's value across every step — including across
    intermediate ``await`` points.

    Pins token-stack semantics (LIFO) for the case where one task wraps a
    nested AI Guard scope mid-evaluation — e.g. a tool callback that re-enters
    the framework's evaluation block. Inner reset MUST restore the outer's
    value (True), not the default (False); otherwise provider-level listeners
    would mistakenly evaluate calls the outer framework already owns.
    """
    assert is_aiguard_context_active() is False
    outer_token = set_aiguard_context_active()
    try:
        await asyncio.sleep(0)
        assert is_aiguard_context_active() is True

        inner_token = set_aiguard_context_active()
        await asyncio.sleep(0)
        assert is_aiguard_context_active() is True
        reset_aiguard_context_active(inner_token)
        # Inner reset returns to the outer's value, not False.
        assert is_aiguard_context_active() is True

        await asyncio.sleep(0)
        assert is_aiguard_context_active() is True
    finally:
        reset_aiguard_context_active(outer_token)
    assert is_aiguard_context_active() is False


# ---------------------------------------------------------------------------
# DD_AI_GUARD_ENABLED — init_ai_guard() registration gating
#
# The flag is evaluated ONCE at ``init_ai_guard()`` time (lazy, idempotent via
# ``_AI_GUARD_TO_BE_LOADED``). There is no runtime re-check in the listeners,
# so DD_AI_GUARD_ENABLED=false must fully short-circuit listener registration.
# These tests pin that contract so a regression that wires the flag through
# listeners-but-keeps-them-registered fails loudly.
# ---------------------------------------------------------------------------


def test_init_ai_guard_disabled_does_not_register_listeners(reset_ai_guard_loaded):
    """DD_AI_GUARD_ENABLED=false: ai_guard_listen() must NOT run."""
    ai_guard_mod._AI_GUARD_TO_BE_LOADED = True
    with patch("ddtrace.appsec._ai_guard._listener.ai_guard_listen") as mock_listen:
        with override_ai_guard_config(dict(_ai_guard_enabled=False)):
            ai_guard_mod.init_ai_guard()
        mock_listen.assert_not_called()
    # Disabled or not, init must mark itself loaded so it never retries.
    assert ai_guard_mod._AI_GUARD_TO_BE_LOADED is False


def test_init_ai_guard_enabled_registers_listeners(reset_ai_guard_loaded):
    """DD_AI_GUARD_ENABLED=true: ai_guard_listen() runs exactly once."""
    ai_guard_mod._AI_GUARD_TO_BE_LOADED = True
    with patch("ddtrace.appsec._ai_guard._listener.ai_guard_listen") as mock_listen:
        with override_ai_guard_config(dict(_ai_guard_enabled=True)):
            ai_guard_mod.init_ai_guard()
        mock_listen.assert_called_once()
    assert ai_guard_mod._AI_GUARD_TO_BE_LOADED is False


def test_init_ai_guard_is_idempotent(reset_ai_guard_loaded):
    """Second call is a no-op regardless of config (_AI_GUARD_TO_BE_LOADED guard)."""
    ai_guard_mod._AI_GUARD_TO_BE_LOADED = False  # simulate already-loaded state
    with patch("ddtrace.appsec._ai_guard._listener.ai_guard_listen") as mock_listen:
        with override_ai_guard_config(dict(_ai_guard_enabled=True)):
            ai_guard_mod.init_ai_guard()
            ai_guard_mod.init_ai_guard()
        mock_listen.assert_not_called()


# ---------------------------------------------------------------------------
# DD_AI_GUARD_BLOCK — explicit true/false coverage
#
# The ``_ai_guard_block=False`` case is already covered above. This section
# pins the explicit True case so the default path is locked under its env-var
# name, and the client-side override semantics (local config beats server
# ``is_blocking_enabled``) are clear in the test suite.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_sync_block_config_enabled(mock_execute_request, openai_client, decision):
    """DD_AI_GUARD_BLOCK=true (default): DENY/ABORT MUST raise AIGuardAbortError."""
    mock_execute_request.return_value = mock_evaluate_response(decision)

    with override_ai_guard_config(dict(_ai_guard_block=True)):
        with pytest.raises(AIGuardAbortError):
            openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)
    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_block_config_enabled(mock_execute_request, async_openai_client, decision):
    mock_execute_request.return_value = mock_evaluate_response(decision)

    with override_ai_guard_config(dict(_ai_guard_block=True)):
        with pytest.raises(AIGuardAbortError):
            await async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)
    mock_execute_request.assert_called_once()


def test_chat_sync_block_server_side_disabled_wins(openai_client):
    """DD_AI_GUARD_BLOCK=true + server response ``is_blocking_enabled=False``:
    the server-side "don't block" hint wins over the local "block on DENY"
    config (see ``AIGuardClient._is_blocking_enabled``). DENY must NOT raise.

    Pins the precedence rule: server ``is_blocking_enabled=False`` short-
    circuits any local Options(block=True) — local config can only *relax*
    blocking, never force it past a server override.
    """
    with patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request") as mock_execute_request:
        mock_execute_request.return_value = mock_evaluate_response("DENY", block=False)
        with override_ai_guard_config(dict(_ai_guard_block=True)):
            resp = openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)
            assert resp is not None


# ---------------------------------------------------------------------------
# Real-integration cassettes — emulate a full OpenAI flow end-to-end.
#
# These tests hit the testagent VCR proxy (``http://localhost:9126/vcr/openai``)
# which replays recorded OpenAI traffic from ``tests/cassettes/openai/``.
# Only the AI Guard API itself is mocked — the OpenAI request serialization,
# HTTP plumbing, and response parsing all exercise the real SDK path.
# ---------------------------------------------------------------------------


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_cassette_real_response_is_evaluated_after_model(mock_execute_request, openai_client):
    """Full OpenAI flow: the after-model evaluation payload contains the
    assistant content parsed out of the real cassette response (not a mock).

    Pins that ``_convert_openai_response`` handles actual SDK response objects
    (``ChatCompletion`` with ``.choices[].message.content``), not just the
    hand-rolled mocks in ``TestConvertOpenAIResponse``.
    """
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    resp = openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    assert resp.choices[0].message.role == "assistant"
    assistant_content = resp.choices[0].message.content
    assert assistant_content  # real cassette body contains the grammar explanation

    # Two calls: before(user-only) and after(user + assistant response).
    assert mock_execute_request.call_count == 2

    before_args = mock_execute_request.call_args_list[0].args
    after_args = mock_execute_request.call_args_list[1].args

    before_messages = before_args[1]["data"]["attributes"]["messages"]
    after_messages = after_args[1]["data"]["attributes"]["messages"]

    assert before_messages == [{"role": "user", "content": CHAT_PROMPT}]
    assert len(after_messages) == 2
    assert after_messages[0] == {"role": "user", "content": CHAT_PROMPT}
    assert after_messages[1]["role"] == "assistant"
    assert after_messages[1]["content"] == assistant_content


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_cassette_tool_call_response_carries_tool_calls_in_after(mock_execute_request, openai_client):
    """Full OpenAI flow with tools: cassette returns ``tool_calls`` and the
    after-model evaluation payload MUST include them on the assistant message.

    Uses the cassette at
    ``tests/cassettes/openai/openai_chat_completions_post_c180b8dd.json`` —
    the request body is byte-matched by the testagent VCR hasher so the
    ``tools`` schema + prompt must be identical to the recording.
    """
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    resp = openai_client.chat.completions.create(
        model=TOOL_CALL_MODEL,
        messages=[{"content": TOOL_CALL_PROMPT, "role": "user"}],
        n=1,
        stream=False,
        temperature=0.7,
        tools=[ADD_TOOL],
    )

    # Sanity: the cassette response returned a tool_call to ``add``.
    tool_calls = resp.choices[0].message.tool_calls
    assert tool_calls and tool_calls[0].function.name == "add"

    # before (user) + after (user + assistant-with-tool_calls)
    assert mock_execute_request.call_count == 2
    _, after_payload = mock_execute_request.call_args_list[1].args
    after_messages = after_payload["data"]["attributes"]["messages"]

    assistant_msg = after_messages[-1]
    assert assistant_msg["role"] == "assistant"
    # Content is None in the cassette response; _convert_openai_response drops
    # missing content so the key MUST be absent (not present-with-None).
    assert "content" not in assistant_msg
    assert assistant_msg["tool_calls"][0]["function"]["name"] == "add"
    assert assistant_msg["tool_calls"][0]["function"]["arguments"] == '{"a": 1, "b": 2}'


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_cassette_real_response_async_matches_sync(mock_execute_request, async_openai_client):
    """Same cassette, async path: sanity-check that the async listener receives
    the same converted payload structure as the sync one.
    """
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    resp = await async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    assert resp.choices[0].message.content
    assert mock_execute_request.call_count == 2
    _, after_payload = mock_execute_request.call_args_list[1].args
    after_messages = after_payload["data"]["attributes"]["messages"]
    assert after_messages[0] == {"role": "user", "content": CHAT_PROMPT}
    assert after_messages[1]["role"] == "assistant"


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_cassette_deny_before_does_not_call_openai(mock_execute_request, openai_client):
    """Before-model DENY must short-circuit BEFORE any HTTP call to OpenAI —
    if it didn't, the testagent VCR would serve the cassette response and
    the after-evaluation would also fire.
    """
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    with pytest.raises(AIGuardAbortError):
        openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    # Exactly one call — the before-evaluation. If OpenAI was hit, the
    # after-hook would have fired a second call.
    mock_execute_request.assert_called_once()


def _find_openai_llm_span(test_spans):
    """Return the openai LLM span (the only non-AI-Guard span)."""
    from ddtrace.appsec._constants import AI_GUARD

    spans = test_spans.spans
    llm_span = next((s for s in spans if s.name != AI_GUARD.RESOURCE_TYPE), None)
    assert llm_span is not None, f"No openai LLM span found among: {[s.name for s in spans]}"
    return llm_span


def _assert_block_tagged_on_llm_span(llm_span):
    """Pin every signal that the LLMObs payload derived from this APM span
    depends on for surfacing an AI Guard block.

    Pre-fix failure modes that each assertion catches:
    - ``llm_span.error == 1`` — drives the LLMObs ``status: error`` field on
      the emitted span event. The after-hook block originally submitted
      ``error == 0`` because ``span.finish()`` ran before the after-dispatch
      raised.
    - ``error.type`` — distinguishes an AI Guard abort from a generic SDK
      error in the LLMObs UI. Accepts both ``AIGuardAbortError`` (openai
      SDK not importable) and the compound ``OpenAIAIGuardAbortError``
      actually raised by the integration.
    - ``error.message`` — populates the LLMObs event ``error.message``;
      empty if the abort was never surfaced through ``set_exc_info`` (the
      regression-prone path: if ``g.send((resp, err))`` skips the error
      branch, message/type/stack all stay unset).

    Note: ``_dd.llmobs.submitted`` is NOT asserted here because the metric
    is only set when LLMObs is actually enabled in the test process. The
    ``ai_guard_openai`` suite runs with LLMObs disabled, so the integration's
    ``llmobs_set_tags`` short-circuits before stamping the metric. The
    contract being tested is the *APM-side* error data the LLMObs trace
    processor reads from on span finish.
    """
    assert llm_span.error == 1
    assert "AIGuardAbortError" in (llm_span.get_tag("error.type") or "")
    assert "AIGuardAbortError" in (llm_span.get_tag("error.message") or "")


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_sync_before_block_tags_llm_span_with_error(mock_execute_request, openai_client, tracer, test_spans):
    """Before-hook DENY: the openai LLM span MUST exist and carry the abort
    so the LLMObs payload reaches the backend as an errored llm span.

    Pre-fix the dispatch raises before ``_traced_endpoint`` is entered, so
    no openai span is ever created — ``_find_openai_llm_span`` then fails
    with ``No openai LLM span found among: ['ai_guard']``.
    """
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    with pytest.raises(AIGuardAbortError):
        openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    _assert_block_tagged_on_llm_span(_find_openai_llm_span(test_spans))


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_sync_after_block_tags_llm_span_with_error(mock_execute_request, openai_client, tracer, test_spans):
    """After-hook DENY: the openai LLM span MUST carry the abort error and
    preserve the real OpenAI response metadata.

    Pre-fix the after-dispatch fires AFTER ``span.finish()`` has already
    submitted the LLMObs span as success — so ``llm_span.error == 0`` and
    the abort is invisible to LLMObs.
    """
    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),
        mock_evaluate_response("DENY"),
    ]

    with pytest.raises(AIGuardAbortError):
        openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    llm_span = _find_openai_llm_span(test_spans)
    _assert_block_tagged_on_llm_span(llm_span)
    # The after-block path runs ``_record_response`` with the *real* OpenAI
    # response object (plus the error), so ``openai.response.model`` MUST
    # be present. A regression that skipped ``_record_response`` on the
    # error path would drop this tag and ship an LLMObs error span that's
    # missing the model identity it had on the wire.
    assert llm_span.get_tag("openai.response.model"), (
        "openai.response.model missing — _record_response did not run on the "
        "real response before the after-hook abort was raised"
    )


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_before_block_tags_llm_span_with_error(
    mock_execute_request, async_openai_client, tracer, test_spans
):
    """Async variant of ``test_chat_sync_before_block_tags_llm_span_with_error``."""
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    with pytest.raises(AIGuardAbortError):
        await async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    _assert_block_tagged_on_llm_span(_find_openai_llm_span(test_spans))


@pytest.mark.asyncio
@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
async def test_chat_async_after_block_tags_llm_span_with_error(
    mock_execute_request, async_openai_client, tracer, test_spans
):
    """Async variant of ``test_chat_sync_after_block_tags_llm_span_with_error``."""
    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),
        mock_evaluate_response("DENY"),
    ]

    with pytest.raises(AIGuardAbortError):
        await async_openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    llm_span = _find_openai_llm_span(test_spans)
    _assert_block_tagged_on_llm_span(llm_span)
    assert llm_span.get_tag("openai.response.model"), (
        "openai.response.model missing — _record_response did not run on the "
        "real response before the after-hook abort was raised"
    )


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_chat_sync_before_block_records_request_model_on_llm_span(
    mock_execute_request, openai_client, tracer, test_spans
):
    """Before-hook DENY: the openai LLM span MUST still carry the request
    metadata captured by ``_record_request`` (model, endpoint, method).

    The before-hook dispatches AFTER ``_traced_endpoint``'s first ``yield``,
    which already ran ``_record_request`` on the span. Pre-fix the span
    never existed; post-fix it exists *with* the request data even though
    the OpenAI call never happened. A regression that dispatched the
    before-hook before ``_record_request`` would drop these tags and ship
    an LLMObs error span missing the request identity.
    """
    mock_execute_request.return_value = mock_evaluate_response("DENY")

    with pytest.raises(AIGuardAbortError):
        openai_client.chat.completions.create(messages=_user_messages(), **CHAT_PARAMS)

    llm_span = _find_openai_llm_span(test_spans)
    _assert_block_tagged_on_llm_span(llm_span)
    assert llm_span.get_tag("openai.request.model") == CHAT_MODEL
    assert llm_span.get_tag("openai.request.endpoint") == "/v1/chat/completions"
    assert llm_span.get_tag("openai.request.method") == "POST"
