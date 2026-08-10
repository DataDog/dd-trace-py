"""Streaming AI Guard tests for the Anthropic SDK."""

from unittest.mock import patch

import pytest

from ddtrace.aiguard import AIGuardAbortError
from ddtrace.aiguard._streaming import BufferedAIGuardStream
from tests.aiguard.utils import mock_evaluate_response


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
def test_wrappers_not_installed_when_flag_off(mock_execute_request, anthropic_client_stream):
    """Default (flag=False): stream is NOT wrapped in a BufferedAIGuardStream."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    result = anthropic_client_stream.messages.create(messages=_user_messages(), stream=True, **CHAT_PARAMS)
    assert not isinstance(result, BufferedAIGuardStream)
    list(result)


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_wrappers_installed_when_flag_on(mock_execute_request, anthropic_client_stream_buffered):
    """With flag=True: Messages.create(stream=True) returns a BufferedAIGuardStream."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    result = anthropic_client_stream_buffered.messages.create(messages=_user_messages(), stream=True, **CHAT_PARAMS)
    assert isinstance(result, BufferedAIGuardStream)
    list(result)


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
# Install / uninstall safety
# ---------------------------------------------------------------------------


def test_partial_install_failure_does_not_strip_contrib_wrappers(anthropic_sdk_buffered, monkeypatch):
    """A wrap() failure mid-install must not leave us unwrapping targets we never
    wrapped — otherwise uninstall would peel the contrib's tracing wrapper.

    We reset our wrappers to the contrib-only baseline, force ``wrap`` to fail on
    one target, re-install, and assert the failed target is never recorded and
    its contrib wrapper survives a subsequent uninstall.
    """
    import inspect

    import anthropic

    from ddtrace.aiguard import _listener
    import ddtrace.contrib.internal.trace_utils as trace_utils

    target_cls = anthropic.resources.messages.AsyncMessages
    other_cls = anthropic.resources.messages.Messages

    # ``getattr_static`` returns the stored FunctionWrapper without invoking the
    # descriptor, so its identity is stable (``target_cls.create`` would mint a
    # fresh BoundFunctionWrapper on every access).
    def stored(cls, attr):
        return inspect.getattr_static(cls, attr)

    # Peel our buffer layer installed by the fixture's patch(), back to contrib-only.
    _listener._uninstall_anthropic_wrappers()
    contrib_create = stored(target_cls, "create")
    assert hasattr(contrib_create, "__wrapped__"), "contrib wrapper expected after uninstall"

    real_wrap = trace_utils.wrap

    def flaky_wrap(owner, attr, wrapper):
        if owner is target_cls and attr == "create":
            raise RuntimeError("boom")
        return real_wrap(owner, attr, wrapper)

    monkeypatch.setattr(trace_utils, "wrap", flaky_wrap)
    _listener._install_anthropic_wrappers(object())

    # The failed target was never recorded, and our buffer layer was not applied
    # to it (still the exact contrib wrapper object).
    assert (target_cls, "create") not in _listener._anthropic_wrapped_targets
    assert stored(target_cls, "create") is contrib_create
    # A sibling target that did NOT fail was wrapped by us and recorded.
    assert (other_cls, "create") in _listener._anthropic_wrapped_targets
    assert stored(other_cls, "create") is not contrib_create

    # Uninstall must leave the contrib wrapper on the never-wrapped target intact.
    _listener._uninstall_anthropic_wrappers()
    assert stored(target_cls, "create") is contrib_create
    assert not _listener._anthropic_wrapped_targets
