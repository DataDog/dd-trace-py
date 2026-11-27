"""Streaming response-evaluation AI Guard tests for the OpenAI SDK.

All "buffered" cases run with ``DD_AI_GUARD_ANALYZE_STREAM_RESPONSES_ENABLED=true``
(via the *_buffered fixtures). Flag-off cases assert request inputs are evaluated and
the stream is not buffered.
"""

import asyncio
import json
import threading
import time
from unittest.mock import patch

from openai import version
import pytest

from ddtrace.aiguard import AIGuardAbortError
from ddtrace.aiguard import AIGuardClientError
from ddtrace.aiguard._listener import _openai_stream_created
from ddtrace.aiguard._streaming import AsyncBufferedEvents
from ddtrace.aiguard._streaming import BufferedEvents
from ddtrace.aiguard._streaming import InterruptedStreamError
from ddtrace.contrib.internal._sdk_streams import StreamEvents
from ddtrace.internal.utils.version import parse_version
from tests.aiguard.utils import mock_evaluate_response
from tests.aiguard.utils import override_ai_guard_config


CHAT_MODEL = "gpt-3.5-turbo"
OPENAI_VERSION = parse_version(version.VERSION)
RESPONSES_MODEL = "gpt-4o-mini"


def _user_messages(content="Hello"):
    return [{"role": "user", "content": content}]


def _assert_buffered(stream, buffered):
    """Check whether AI Guard buffers the events of an SDK stream.

    OpenAI SDKs older than 1.6 return the contrib's generator, which hides the SDK stream: the block tests cover them.
    """
    if not hasattr(stream, "_iterator") and OPENAI_VERSION < (1, 6):
        return
    assert isinstance(stream._iterator, (BufferedEvents, AsyncBufferedEvents)) is buffered


# ---------------------------------------------------------------------------
# Chat Completions — sync
# ---------------------------------------------------------------------------


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_chat_stream_sync_buffered_allow(mock_execute_request, openai_client_stream_buffered):
    """ALLOW: create(stream=True) buffers its events; chunks replay after 2 evals."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = openai_client_stream_buffered.chat.completions.create(
        model=CHAT_MODEL, messages=_user_messages(), stream=True
    )
    _assert_buffered(stream, True)
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
def test_chat_stream_sync_read_after_caught_block(mock_execute_request, openai_client_stream_buffered):
    """Iterating a blocked stream again never yields the blocked chunks, and does not evaluate them again."""
    mock_execute_request.side_effect = [
        mock_evaluate_response("ALLOW"),
        mock_evaluate_response("DENY"),
        mock_evaluate_response("ALLOW"),
    ]

    stream = openai_client_stream_buffered.chat.completions.create(
        model=CHAT_MODEL, messages=_user_messages(), stream=True
    )
    with pytest.raises(AIGuardAbortError):
        list(stream)
    if OPENAI_VERSION < (1, 6):
        # The contrib's generator has finished: it yields nothing.
        assert list(stream) == []
    else:
        with pytest.raises(AIGuardAbortError):
            list(stream)

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


@patch("ddtrace.contrib.internal.openai._streaming.openai_construct_message_from_streamed_chunks")
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
    """Flag off: create(stream=True) is not buffered (today's behavior)."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = openai_client_stream.chat.completions.create(model=CHAT_MODEL, messages=_user_messages(), stream=True)
    _assert_buffered(stream, False)
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
    """Async ALLOW: AsyncCompletions.create buffers its events."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = await async_openai_client_stream_buffered.chat.completions.create(
        model=CHAT_MODEL, messages=_user_messages(), stream=True
    )
    _assert_buffered(stream, True)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["ALLOW", "DENY"], ids=["allow", "deny"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_chat_stream_async_buffered_evaluates_off_event_loop(
    mock_execute_request, async_openai_client_stream_buffered, decision
):
    """A slow buffered-response evaluation must not stall other asyncio tasks; a block still propagates."""
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    ticks_during_response_eval = []

    def execute_request(*args, **kwargs):
        if mock_execute_request.call_count == 1:  # before-hook request evaluation
            return mock_evaluate_response("ALLOW")
        start = ticks
        time.sleep(0.3)
        ticks_during_response_eval.append(ticks - start)
        return mock_evaluate_response(decision)

    mock_execute_request.side_effect = execute_request
    ticker_task = asyncio.create_task(ticker())
    try:
        stream = await async_openai_client_stream_buffered.chat.completions.create(
            model=CHAT_MODEL, messages=_user_messages(), stream=True
        )
        seen = []
        if decision == "ALLOW":
            seen = [chunk async for chunk in stream]
            assert seen
        else:
            with pytest.raises(AIGuardAbortError):
                async for chunk in stream:
                    seen.append(chunk)
            assert seen == []
    finally:
        ticker_task.cancel()
        await asyncio.gather(ticker_task, return_exceptions=True)

    assert mock_execute_request.call_count == 2
    # The ticker kept running while the blocking evaluation slept in its worker thread.
    assert ticks_during_response_eval and ticks_during_response_eval[0] > 1


def _run_without_event_loop(coro):
    """Drive a coroutine that never suspends, with no asyncio loop running (as under trio)."""
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    raise AssertionError("coroutine suspended")


async def _events(*chunks):
    for chunk in chunks:
        yield chunk


async def _async_iterate(stream):
    return [chunk async for chunk in stream]


@pytest.mark.parametrize("decision", ["ALLOW", "DENY"], ids=["allow", "deny"])
def test_async_buffered_events_without_asyncio_loop_evaluate_inline(decision):
    """Without a running asyncio loop the evaluation runs inline: allow replays, block raises."""

    def evaluate(response):
        assert response == ["a", "b"]
        if decision == "DENY":
            raise AIGuardAbortError(action="DENY", reason="blocked")

    stream = AsyncBufferedEvents(_events("a", "b"), reconstruct=list, evaluate=evaluate, enabled=lambda: True)
    if decision == "ALLOW":
        assert _run_without_event_loop(_async_iterate(stream)) == ["a", "b"]
    else:
        with pytest.raises(AIGuardAbortError):
            _run_without_event_loop(_async_iterate(stream))


@pytest.mark.asyncio
async def test_async_buffered_events_concurrent_and_later_readers_share_the_verdict():
    """A reader arriving during or after a blocking evaluation must not read the exhausted events unevaluated."""
    evaluated = []

    def evaluate(response):
        evaluated.append(response)
        time.sleep(0.2)
        if response:
            raise AIGuardAbortError(action="DENY", reason="blocked")

    async def read_during_evaluation(stream):
        await asyncio.sleep(0.05)
        return await stream.__anext__()

    stream = AsyncBufferedEvents(_events("a", "b"), reconstruct=list, evaluate=evaluate, enabled=lambda: True)
    results = await asyncio.gather(_async_iterate(stream), read_during_evaluation(stream), return_exceptions=True)
    assert [type(result) for result in results] == [AIGuardAbortError, AIGuardAbortError]
    with pytest.raises(AIGuardAbortError):
        await stream.__anext__()
    assert evaluated == [["a", "b"]]


_SYNC_READS = {
    "iterate": lambda stream: list(stream),
    "next": lambda stream: next(stream, None),
}

_ASYNC_READS = {
    "iterate": _async_iterate,
    "next": lambda stream: stream.__anext__(),
}

# After the blocking verdict: a later verdict allows, AI Guard fails, or stream analysis is turned off.
_LATER = ["allow", "client_error", "analysis_off"]


def _block_once(later):
    """Return the recorded evaluations and a stub that blocks on its first call."""
    evaluated = []

    def evaluate(response):
        evaluated.append(response)
        if len(evaluated) == 1:
            raise AIGuardAbortError(action="DENY", reason="blocked")
        if later == "client_error":
            raise AIGuardClientError("AI Guard is unavailable")

    return evaluated, evaluate


@pytest.mark.parametrize("later", _LATER)
@pytest.mark.parametrize("read", list(_SYNC_READS))
def test_buffered_events_read_after_block(read, later):
    """Every read after a block raises it again, and nothing evaluates the events again to release them."""
    evaluated, evaluate = _block_once(later)
    analysis = {"enabled": True}
    stream = BufferedEvents(iter(["a", "b"]), reconstruct=list, evaluate=evaluate, enabled=lambda: analysis["enabled"])

    with pytest.raises(AIGuardAbortError) as blocked:
        list(stream)
    analysis["enabled"] = later != "analysis_off"
    with pytest.raises(AIGuardAbortError) as again:
        _SYNC_READS[read](stream)

    assert again.value is blocked.value
    assert evaluated == [["a", "b"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("later", _LATER)
@pytest.mark.parametrize("read", list(_ASYNC_READS))
async def test_async_buffered_events_read_after_block(read, later):
    """Async twin: every read after a block raises it again, without a new evaluation."""
    evaluated, evaluate = _block_once(later)
    analysis = {"enabled": True}
    stream = AsyncBufferedEvents(
        _events("a", "b"), reconstruct=list, evaluate=evaluate, enabled=lambda: analysis["enabled"]
    )

    with pytest.raises(AIGuardAbortError) as blocked:
        await _async_iterate(stream)
    analysis["enabled"] = later != "analysis_off"
    with pytest.raises(AIGuardAbortError) as again:
        await _ASYNC_READS[read](stream)

    assert again.value is blocked.value
    assert evaluated == [["a", "b"]]


def test_buffered_events_keep_a_block_without_the_frames_it_chains():
    """The remembered block does not keep the frames of the exceptions it chains."""

    def evaluate(response):
        try:
            raise AIGuardAbortError(action="DENY", reason="inner")
        except AIGuardAbortError as inner:
            raise AIGuardAbortError(action="DENY", reason="outer") from inner

    stream = BufferedEvents(iter(["a"]), reconstruct=list, evaluate=evaluate, enabled=lambda: True)
    with pytest.raises(AIGuardAbortError) as blocked:
        list(stream)

    assert blocked.value.__cause__.__traceback__ is None
    # Raised again outside the evaluation, so the remembered block keeps no frame of it.
    assert blocked.value.__context__ is None or blocked.value.__context__ is blocked.value.__cause__


class _NoVerdict(BaseException):
    """Ends an evaluation without a verdict, like a timeout raised inside the AI Guard call."""


@pytest.mark.parametrize("later", ["unchanged", "analysis_off", "claim"])
def test_buffered_events_evaluate_the_buffer_again_without_verdict(later, request):
    """An evaluation that ends without a verdict keeps the drained buffer, and the next read evaluates it."""
    evaluated = []

    def evaluate(response):
        evaluated.append(response)
        if len(evaluated) == 1:
            raise _NoVerdict()

    analysis = {"enabled": True}
    stream = BufferedEvents(iter(["a", "b"]), reconstruct=list, evaluate=evaluate, enabled=lambda: analysis["enabled"])

    with pytest.raises(_NoVerdict):
        list(stream)
    # Pass-through is decided on the first read, so turning analysis off or claiming the call skips nothing now.
    analysis["enabled"] = later != "analysis_off"
    if later == "claim":
        request.getfixturevalue("aiguard_active_context")
    assert list(stream) == ["a", "b"]
    assert evaluated == [["a", "b"], ["a", "b"]]


@pytest.mark.asyncio
async def test_async_buffered_events_evaluate_the_buffer_again_without_verdict():
    """Async twin: an off-loop evaluation that ends without a verdict is not kept, and the next read evaluates again."""
    evaluated = []

    def evaluate(response):
        evaluated.append(response)
        if len(evaluated) == 1:
            raise _NoVerdict()

    stream = AsyncBufferedEvents(_events("a", "b"), reconstruct=list, evaluate=evaluate, enabled=lambda: True)

    with pytest.raises(_NoVerdict):
        await _async_iterate(stream)
    assert await _async_iterate(stream) == ["a", "b"]
    assert evaluated == [["a", "b"], ["a", "b"]]


class _InterruptedEvents:
    """Events whose read is interrupted once before "b", like a signal arriving mid-stream."""

    def __init__(self):
        self._chunks = ["a", "b"]
        self._interrupted = False

    def __iter__(self):
        return self

    def __next__(self):
        if not self._chunks:
            raise StopIteration
        if self._chunks[0] == "b" and not self._interrupted:
            self._interrupted = True
            raise _NoVerdict()
        return self._chunks.pop(0)


def test_buffered_events_interrupted_drain_is_terminal():
    """An interrupted drain never evaluates or replays the events drained before the interruption."""
    evaluated = []
    stream = BufferedEvents(_InterruptedEvents(), reconstruct=list, evaluate=evaluated.append, enabled=lambda: True)

    with pytest.raises(_NoVerdict):
        list(stream)
    with pytest.raises(InterruptedStreamError):
        list(stream)
    assert evaluated == []


@pytest.mark.asyncio
async def test_async_buffered_events_cancelled_drain_is_terminal():
    """A drain cancelled half way never makes its prefix look complete to a later read."""
    evaluated = []
    first_event, release = asyncio.Event(), asyncio.Event()

    async def events():
        yield "a"
        first_event.set()
        await release.wait()
        yield "b"

    stream = AsyncBufferedEvents(events(), reconstruct=list, evaluate=evaluated.append, enabled=lambda: True)
    reading = asyncio.ensure_future(stream.__anext__())
    await first_event.wait()
    reading.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reading

    with pytest.raises(InterruptedStreamError):
        await stream.__anext__()
    assert evaluated == []


def _failing_events():
    yield "a"
    raise RuntimeError("provider error event")


async def _async_failing_events():
    yield "a"
    raise RuntimeError("provider error event")


def test_buffered_events_provider_error_never_replays_the_partial_events():
    """A provider error during the drain replays nothing and is raised again by every later read."""
    evaluated = []
    stream = BufferedEvents(_failing_events(), reconstruct=list, evaluate=evaluated.append, enabled=lambda: True)

    with pytest.raises(RuntimeError) as failed:
        list(stream)
    with pytest.raises(RuntimeError) as again:
        next(stream)

    assert again.value is failed.value
    assert evaluated == []


@pytest.mark.asyncio
async def test_async_buffered_events_provider_error_never_replays_the_partial_events():
    """Async twin: a provider error during the drain replays nothing and is raised again."""
    evaluated = []
    stream = AsyncBufferedEvents(
        _async_failing_events(), reconstruct=list, evaluate=evaluated.append, enabled=lambda: True
    )

    with pytest.raises(RuntimeError) as failed:
        await _async_iterate(stream)
    with pytest.raises(RuntimeError) as again:
        await stream.__anext__()

    assert again.value is failed.value
    assert evaluated == []


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["allow", "deny"])
async def test_async_buffered_events_cancelled_reader_shares_the_evaluation(verdict):
    """A reader cancelled during the evaluation leaves it running: the next reader gets its verdict, not a new one."""
    evaluated = []
    started, release = threading.Event(), threading.Event()

    def evaluate(response):
        evaluated.append(response)
        first = len(evaluated) == 1
        if first:
            started.set()
            release.wait(5)
        # The first evaluation gives the verdict under test; a second one would give the other verdict.
        if first == (verdict == "deny"):
            raise AIGuardAbortError(action="DENY", reason="blocked")

    stream = AsyncBufferedEvents(_events("a", "b"), reconstruct=list, evaluate=evaluate, enabled=lambda: True)
    reading = asyncio.ensure_future(stream.__anext__())
    try:
        assert await asyncio.to_thread(started.wait, 5)
        evaluation = stream._evaluation
        reading.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reading
        later = asyncio.ensure_future(_async_iterate(stream))
        await asyncio.sleep(0.05)
        assert not later.done()
    finally:
        release.set()

    if verdict == "deny":
        with pytest.raises(AIGuardAbortError):
            await later
    else:
        assert await later == ["a", "b"]
    # The finished evaluation is released once it completes, whether or not a reader awaits it.
    await asyncio.wait([evaluation])
    await asyncio.sleep(0)
    assert stream._evaluation is None
    assert evaluated == [["a", "b"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["analysis_off", "claim"])
async def test_buffered_events_pass_through_is_decided_on_the_first_read(mode, request):
    """A stream that is pass-through on its first read stays unbuffered and is never evaluated."""
    evaluated = []
    analysis = {"enabled": mode != "analysis_off"}
    if mode == "claim":
        request.getfixturevalue("aiguard_active_context")
    sync_stream = BufferedEvents(
        iter(["a", "b"]), reconstruct=list, evaluate=evaluated.append, enabled=lambda: analysis["enabled"]
    )
    async_stream = AsyncBufferedEvents(
        _events("a", "b"), reconstruct=list, evaluate=evaluated.append, enabled=lambda: analysis["enabled"]
    )

    assert next(sync_stream) == "a"
    assert await async_stream.__anext__() == "a"
    analysis["enabled"] = True
    assert list(sync_stream) == ["b"]
    assert await _async_iterate(async_stream) == ["b"]
    assert evaluated == []


# ---------------------------------------------------------------------------
# Responses API
# ---------------------------------------------------------------------------


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_responses_stream_sync_buffered_allow(mock_execute_request, openai_responses_stream_client_buffered):
    """ALLOW: responses.create(stream=True) buffers; request eval + response eval."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = openai_responses_stream_client_buffered.responses.create(model=RESPONSES_MODEL, input="Hello", stream=True)
    _assert_buffered(stream, True)
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
    _assert_buffered(stream, True)
    events = [event async for event in stream]

    assert events
    assert mock_execute_request.call_count == 2


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_responses_stream_flag_off_not_buffered(mock_execute_request, openai_responses_stream_client):
    """Flag off: streaming Responses are not buffered; only the request inputs are evaluated."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = openai_responses_stream_client.responses.create(model=RESPONSES_MODEL, input="Hello", stream=True)
    _assert_buffered(stream, False)
    list(stream)

    mock_execute_request.assert_called_once()


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_responses_stream_without_mcp_tools_unchanged_by_default_mcp(
    mock_execute_request, openai_responses_stream_client
):
    """DD_AI_GUARD_MCP_ENABLED is on by default: a stream with only function tools is still unbuffered."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = openai_responses_stream_client.responses.create(
        model=RESPONSES_MODEL, input="Hello", tools=[{"type": "function", "name": "lookup"}], stream=True
    )
    _assert_buffered(stream, False)
    assert list(stream)

    mock_execute_request.assert_called_once()
    assert set(mock_execute_request.call_args.args[1]["data"]["attributes"]) == {"messages", "meta"}


@pytest.mark.asyncio
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_responses_stream_async_flag_off_evaluates_input(
    mock_execute_request, async_openai_responses_stream_client
):
    """Flag off: async streaming Responses are not buffered; only the request inputs are evaluated."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    stream = await async_openai_responses_stream_client.responses.create(
        model=RESPONSES_MODEL, input="Hello", stream=True
    )
    _assert_buffered(stream, False)
    assert [event async for event in stream]

    mock_execute_request.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_responses_stream_async_flag_off_blocks_on_deny(
    mock_execute_request, async_openai_responses_stream_client, decision
):
    """Flag off: a blocking verdict on async streaming request inputs raises before the stream is returned."""
    import openai

    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(openai.UnprocessableEntityError) as exc_info:
        await async_openai_responses_stream_client.responses.create(model=RESPONSES_MODEL, input="Hello", stream=True)

    assert isinstance(exc_info.value, AIGuardAbortError)
    mock_execute_request.assert_called_once()


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_responses_raw_response_stream_flag_off_evaluates_input(mock_execute_request, openai_responses_stream_client):
    """Flag off: with_raw_response.create(stream=True) evaluates the request inputs once, unbuffered."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    raw = openai_responses_stream_client.responses.with_raw_response.create(
        model=RESPONSES_MODEL, input="Hello", stream=True
    )
    stream = raw.parse()
    _assert_buffered(stream, False)
    assert list(stream)

    mock_execute_request.assert_called_once()


@pytest.mark.parametrize("decision", ["DENY", "ABORT"], ids=["deny", "abort"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_responses_raw_response_stream_flag_off_blocks_on_deny(
    mock_execute_request, openai_responses_stream_client, decision
):
    """Flag off: a blocking verdict on with_raw_response.create(stream=True) inputs raises AIGuardAbortError."""
    import openai

    mock_execute_request.return_value = mock_evaluate_response(decision)

    with pytest.raises(openai.UnprocessableEntityError) as exc_info:
        openai_responses_stream_client.responses.with_raw_response.create(
            model=RESPONSES_MODEL, input="Hello", stream=True
        )

    assert isinstance(exc_info.value, AIGuardAbortError)
    mock_execute_request.assert_called_once()


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
    _assert_buffered(stream, True)
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
    _assert_buffered(stream, True)
    chunks = [chunk async for chunk in stream]

    assert chunks
    assert mock_execute_request.call_count == 2


# ---------------------------------------------------------------------------
# Streaming response helper (chat.completions.with_streaming_response)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["ALLOW", "DENY"], ids=["allow", "deny"])
@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
def test_streaming_response_stream_sync_buffered(mock_execute_request, openai_client_stream_buffered, decision):
    """with_streaming_response builds its stream outside the SDK call; it is buffered from the request sent."""
    if not hasattr(openai_client_stream_buffered.chat.completions, "with_streaming_response"):
        pytest.skip("with_streaming_response needs a newer OpenAI SDK")
    mock_execute_request.side_effect = [mock_evaluate_response("ALLOW"), mock_evaluate_response(decision)]

    with openai_client_stream_buffered.chat.completions.with_streaming_response.create(
        model=CHAT_MODEL, messages=_user_messages(), stream=True
    ) as response:
        stream = response.parse()
        _assert_buffered(stream, True)
        if decision == "ALLOW":
            assert list(stream)
        else:
            with pytest.raises(AIGuardAbortError):
                list(stream)

    assert mock_execute_request.call_count == 2


# ---------------------------------------------------------------------------
# openai.stream.created listener
# ---------------------------------------------------------------------------


class _Request:
    def __init__(self, path, body):
        self.url = type("_URL", (), {"path": path})()
        self.content = json.dumps(body).encode()


class _Response:
    def __init__(self, path, body):
        self.request = _Request(path, body)


def _stream_events(path="/v1/chat/completions"):
    response = _Response(path, {"messages": _user_messages()})
    return StreamEvents(response, iter(["chunk"]), is_async=False)


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/openai/deployments/gpt/chat/completions"])
def test_stream_listener_buffers_chat_from_the_sent_request(path):
    events = _stream_events(path)
    with override_ai_guard_config(dict(_ai_guard_analyze_stream_responses_enabled=True)):
        _openai_stream_created(object(), events)

    assert isinstance(events.iterator, BufferedEvents)


@pytest.mark.parametrize(
    "path,analysis",
    [("/v1/chat/completions", False), ("/v1/completions", True), ("/v1/responses/resp_123", True)],
    ids=["analysis_off", "other_endpoint", "retrieve"],
)
def test_stream_listener_leaves_other_streams_unbuffered(path, analysis):
    events = _stream_events(path)
    original = events.iterator
    with override_ai_guard_config(
        dict(_ai_guard_analyze_stream_responses_enabled=analysis, _ai_guard_mcp_enabled=False)
    ):
        _openai_stream_created(object(), events)

    assert events.iterator is original
