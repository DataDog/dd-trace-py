"""Lock the ``start_stream`` lifecycle hook contract on ``BaseStreamHandler``
and ``TracedStream`` / ``TracedAsyncStream``. The hook is consumed by every
LLM contrib, so a regression here surfaces as silent breakage downstream.
"""

import pytest

from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import BaseStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import StreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import make_traced_stream


class _RecordingMixin:
    """Shared lifecycle-call counters for the test handlers."""

    def __init__(self):
        self.options = {}
        self.start_stream_calls = 0
        self.finalize_stream_calls = 0

    def start_stream(self):
        self.start_stream_calls += 1

    def finalize_stream(self, exception=None):
        self.finalize_stream_calls += 1


class _SyncRecordingHandler(_RecordingMixin, StreamHandler):
    def process_chunk(self, chunk, iterator=None):
        pass


class _AsyncRecordingHandler(_RecordingMixin, AsyncStreamHandler):
    async def process_chunk(self, chunk, iterator=None):
        pass


def _sync_chunks(n):
    for i in range(n):
        yield i


async def _async_chunks(n):
    for i in range(n):
        yield i


def test_base_handler_start_stream_default_is_noop():
    """``BaseStreamHandler.start_stream`` default implementation must be a
    side-effect-free no-op so contribs that do not override it are unaffected
    by the hook addition.
    """

    class _MinimalHandler(BaseStreamHandler):
        def __init__(self):
            self.options = {}

        def finalize_stream(self, exception=None):
            pass

    handler = _MinimalHandler()
    handler.start_stream()
    handler.start_stream()
    assert handler.options == {}


def test_traced_stream_does_not_fire_start_stream_on_construction():
    """Constructing the wrapper must NOT trigger ``start_stream`` — the hook
    only runs when the caller actually starts iterating. This is the codex
    P2 contract: unconsumed wrappers must not leak any setup the hook
    performs (e.g. AI Guard depth counter).
    """
    handler = _SyncRecordingHandler()
    make_traced_stream(_sync_chunks(3), handler)
    assert handler.start_stream_calls == 0


def test_traced_async_stream_does_not_fire_start_stream_on_construction():
    """Async variant of the lazy-construction contract."""
    handler = _AsyncRecordingHandler()
    make_traced_stream(_async_chunks(3), handler)
    assert handler.start_stream_calls == 0


def test_traced_stream_fires_start_stream_once_on_for_loop():
    handler = _SyncRecordingHandler()
    traced = make_traced_stream(_sync_chunks(3), handler)
    chunks = list(traced)
    assert chunks == [0, 1, 2]
    assert handler.start_stream_calls == 1
    assert handler.finalize_stream_calls == 1


def test_traced_stream_fires_start_stream_once_on_next_calls():
    handler = _SyncRecordingHandler()
    traced = make_traced_stream(_sync_chunks(3), handler)
    assert next(traced) == 0
    assert next(traced) == 1
    assert handler.start_stream_calls == 1


def test_traced_stream_fires_start_stream_once_when_mixing_next_then_for():
    """Idempotency contract: even if a caller pulls one chunk via ``next()``
    and then iterates with ``for``, ``start_stream`` runs exactly once.
    """
    handler = _SyncRecordingHandler()
    traced = make_traced_stream(_sync_chunks(3), handler)
    assert next(traced) == 0
    remaining = list(traced)
    assert remaining == [1, 2]
    assert handler.start_stream_calls == 1


@pytest.mark.asyncio
async def test_traced_async_stream_fires_start_stream_once_on_async_for_loop():
    handler = _AsyncRecordingHandler()
    traced = make_traced_stream(_async_chunks(3), handler)
    chunks = [chunk async for chunk in traced]
    assert chunks == [0, 1, 2]
    assert handler.start_stream_calls == 1
    assert handler.finalize_stream_calls == 1


@pytest.mark.asyncio
async def test_traced_async_stream_fires_start_stream_once_on_anext_calls():
    handler = _AsyncRecordingHandler()
    traced = make_traced_stream(_async_chunks(3), handler)
    assert await traced.__anext__() == 0
    assert await traced.__anext__() == 1
    assert handler.start_stream_calls == 1


def test_traced_stream_start_stream_fires_before_first_chunk():
    """The hook must run before any chunk-processing observable side effect,
    so the downstream contrib (e.g. langchain ``BaseLangchainStreamHandler``)
    can set up state that the underlying SDK call depends on.
    """

    class _OrderRecordingHandler(_SyncRecordingHandler):
        def __init__(self):
            super().__init__()
            self.events = []

        def start_stream(self):
            self.events.append("start")
            super().start_stream()

        def process_chunk(self, chunk, iterator=None):
            self.events.append(("chunk", chunk))

    handler = _OrderRecordingHandler()
    traced = make_traced_stream(_sync_chunks(2), handler)
    list(traced)
    assert handler.events == ["start", ("chunk", 0), ("chunk", 1)]
