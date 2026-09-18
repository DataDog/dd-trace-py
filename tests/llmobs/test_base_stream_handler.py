"""Lock the ``start_stream`` lifecycle hook contract on ``BaseStreamHandler``
and ``TracedStream`` / ``TracedAsyncStream``. The hook is consumed by every
LLM contrib, so a regression here surfaces as silent breakage downstream.
"""

import asyncio
import gc
from unittest.mock import Mock
from unittest.mock import patch

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
        self.finalize_exceptions = []
        self.handle_exception_calls = []

    def start_stream(self):
        self.start_stream_calls += 1

    def handle_exception(self, exception):
        self.handle_exception_calls.append(exception)

    def finalize_stream(self, exception=None):
        self.finalize_stream_calls += 1
        self.finalize_exceptions.append(exception)


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


class _CtxStream:
    """Iterator that also supports the context-manager protocol."""

    def __init__(self, n):
        self._it = iter(range(n))

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._it)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _AsyncCtxStream:
    """Async iterator that also supports the async context-manager protocol."""

    def __init__(self, n):
        self._it = iter(range(n))

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _StreamManager:
    """Context manager whose __enter__ returns a distinct stream object."""

    def __init__(self, n):
        self._n = n
        self.exits = 0

    def __enter__(self):
        return _CtxStream(self._n)

    def __exit__(self, *exc):
        self.exits += 1
        return False


class _AsyncStreamManager:
    """Async context manager whose __aenter__ returns a distinct stream object."""

    def __init__(self, n):
        self._n = n
        self.exits = 0

    async def __aenter__(self):
        return _AsyncCtxStream(self._n)

    async def __aexit__(self, *exc):
        self.exits += 1
        return False


class _ExitRaises(_CtxStream):
    def __exit__(self, *exc):
        raise RuntimeError("close failed")


class _AsyncExitRaises(_AsyncCtxStream):
    async def __aexit__(self, *exc):
        raise RuntimeError("close failed")


class _AsyncExitCancelled(_AsyncCtxStream):
    async def __aexit__(self, *exc):
        raise asyncio.CancelledError()


def test_traced_stream_finalizes_on_context_manager_exit_when_not_exhausted():
    """A caller that opens the stream with `with` and only pulls some chunks
    must still finalize. Otherwise the OpenAI/Anthropic span stays open and
    later requests on the same worker nest under it.
    """
    handler = _SyncRecordingHandler()
    traced = make_traced_stream(_CtxStream(5), handler)
    with traced as stream:
        assert next(stream) == 0
        assert next(stream) == 1
    assert handler.start_stream_calls == 1
    assert handler.finalize_stream_calls == 1


def test_traced_stream_finalizes_once_when_exhausted_inside_context_manager():
    """`with stream: for chunk in stream` hits both __iter__ and __exit__.
    finalize_stream must run exactly once.
    """
    handler = _SyncRecordingHandler()
    traced = make_traced_stream(_CtxStream(3), handler)
    with traced as stream:
        assert list(stream) == [0, 1, 2]
    assert handler.finalize_stream_calls == 1


def test_traced_stream_finalizes_on_context_manager_exit_without_iteration():
    handler = _SyncRecordingHandler()
    traced = make_traced_stream(_CtxStream(3), handler)
    with traced:
        pass
    assert handler.start_stream_calls == 0
    assert handler.finalize_stream_calls == 1


def test_traced_stream_finalizes_when_dropped_after_partial_next():
    """A caller that pulls chunks with next() and then drops the stream never
    hits StopIteration, so finalize must run from GC. Otherwise the LLM span
    stays open and later requests on the same worker nest under it.
    """
    handler = _SyncRecordingHandler()
    traced = make_traced_stream(_sync_chunks(5), handler)
    assert next(traced) == 0
    assert next(traced) == 1
    assert handler.finalize_stream_calls == 0
    del traced
    gc.collect()
    assert handler.finalize_stream_calls == 1


def test_traced_stream_gc_does_not_double_finalize_after_exhaust():
    handler = _SyncRecordingHandler()
    traced = make_traced_stream(_sync_chunks(3), handler)
    assert list(traced) == [0, 1, 2]
    assert handler.finalize_stream_calls == 1
    del traced
    gc.collect()
    assert handler.finalize_stream_calls == 1


@pytest.mark.asyncio
async def test_traced_async_stream_finalizes_on_context_manager_exit_when_not_exhausted():
    handler = _AsyncRecordingHandler()
    traced = make_traced_stream(_AsyncCtxStream(5), handler)
    async with traced as stream:
        assert await stream.__anext__() == 0
        assert await stream.__anext__() == 1
    assert handler.start_stream_calls == 1
    assert handler.finalize_stream_calls == 1


@pytest.mark.asyncio
async def test_traced_async_stream_finalizes_once_when_exhausted_inside_context_manager():
    handler = _AsyncRecordingHandler()
    traced = make_traced_stream(_AsyncCtxStream(3), handler)
    async with traced as stream:
        chunks = [chunk async for chunk in stream]
    assert chunks == [0, 1, 2]
    assert handler.finalize_stream_calls == 1


def test_traced_stream_records_exception_from_context_manager_body():
    """A raise inside `with stream:` after a partial consume must mark the span
    as an error. Iteration never saw the exception, so __exit__ has to.
    """
    handler = _SyncRecordingHandler()
    traced = make_traced_stream(_CtxStream(5), handler)
    with pytest.raises(ValueError, match="boom"):
        with traced as stream:
            assert next(stream) == 0
            raise ValueError("boom")
    assert handler.finalize_stream_calls == 1
    assert len(handler.handle_exception_calls) == 1
    assert isinstance(handler.handle_exception_calls[0], ValueError)
    assert isinstance(handler.finalize_exceptions[0], ValueError)


def test_traced_stream_does_not_attribute_exception_after_stream_completes():
    """Once the iterator is exhausted the LLM span is done. An error in later
    caller code must not be recorded as a stream failure.
    """
    handler = _SyncRecordingHandler()
    traced = make_traced_stream(_CtxStream(3), handler)
    with pytest.raises(ValueError, match="after"):
        with traced as stream:
            assert list(stream) == [0, 1, 2]
            raise ValueError("after")
    assert handler.finalize_stream_calls == 1
    assert handler.handle_exception_calls == []
    assert handler.finalize_exceptions == [None]


@pytest.mark.asyncio
async def test_traced_async_stream_records_exception_from_context_manager_body():
    handler = _AsyncRecordingHandler()
    traced = make_traced_stream(_AsyncCtxStream(5), handler)
    with pytest.raises(ValueError, match="boom"):
        async with traced as stream:
            assert await stream.__anext__() == 0
            raise ValueError("boom")
    assert handler.finalize_stream_calls == 1
    assert len(handler.handle_exception_calls) == 1
    assert isinstance(handler.handle_exception_calls[0], ValueError)


@pytest.mark.asyncio
async def test_traced_async_stream_finalizes_when_dropped_after_partial_anext():
    handler = _AsyncRecordingHandler()
    traced = make_traced_stream(_async_chunks(5), handler)
    assert await traced.__anext__() == 0
    assert handler.finalize_stream_calls == 0
    del traced
    gc.collect()
    assert handler.finalize_stream_calls == 1


def test_traced_stream_manager_without_as_does_not_finalize_on_enter():
    """`with traced:` keeps the parent, not the child wrapper returned by a
    stream manager. Hold that child or __del__ finalizes before the body.
    """
    handler = _SyncRecordingHandler()
    manager = _StreamManager(3)
    traced = make_traced_stream(manager, handler)
    with traced:
        gc.collect()
        assert handler.finalize_stream_calls == 0
    assert handler.finalize_stream_calls == 1
    assert manager.exits == 1


def test_traced_stream_manager_as_target_still_iterates():
    handler = _SyncRecordingHandler()
    traced = make_traced_stream(_StreamManager(3), handler)
    with traced as stream:
        assert list(stream) == [0, 1, 2]
    assert handler.finalize_stream_calls == 1


def test_traced_stream_records_exception_from_wrapped_exit():
    handler = _SyncRecordingHandler()
    traced = make_traced_stream(_ExitRaises(3), handler)
    with pytest.raises(RuntimeError, match="close failed"):
        with traced as stream:
            assert next(stream) == 0
    assert handler.finalize_stream_calls == 1
    assert len(handler.handle_exception_calls) == 1
    assert isinstance(handler.handle_exception_calls[0], RuntimeError)
    assert isinstance(handler.finalize_exceptions[0], RuntimeError)


@pytest.mark.asyncio
async def test_traced_async_stream_manager_without_as_does_not_finalize_on_enter():
    handler = _AsyncRecordingHandler()
    manager = _AsyncStreamManager(3)
    traced = make_traced_stream(manager, handler)
    async with traced:
        gc.collect()
        assert handler.finalize_stream_calls == 0
    assert handler.finalize_stream_calls == 1
    assert manager.exits == 1


@pytest.mark.asyncio
async def test_traced_async_stream_records_exception_from_wrapped_exit():
    handler = _AsyncRecordingHandler()
    traced = make_traced_stream(_AsyncExitRaises(3), handler)
    with pytest.raises(RuntimeError, match="close failed"):
        async with traced as stream:
            assert await stream.__anext__() == 0
    assert handler.finalize_stream_calls == 1
    assert len(handler.handle_exception_calls) == 1
    assert isinstance(handler.handle_exception_calls[0], RuntimeError)


@pytest.mark.asyncio
async def test_traced_async_stream_finalizes_when_wrapped_aexit_cancelled():
    handler = _AsyncRecordingHandler()
    traced = make_traced_stream(_AsyncExitCancelled(3), handler)
    with pytest.raises(asyncio.CancelledError):
        async with traced as stream:
            assert await stream.__anext__() == 0
    assert handler.finalize_stream_calls == 1
    assert len(handler.handle_exception_calls) == 1
    assert isinstance(handler.handle_exception_calls[0], asyncio.CancelledError)
    assert isinstance(handler.finalize_exceptions[0], asyncio.CancelledError)


def test_traced_stream_finalizes_when_on_stream_created_raises():
    handler = _SyncRecordingHandler()

    def boom(_stream):
        raise RuntimeError("callback failed")

    traced = make_traced_stream(_StreamManager(3), handler, on_stream_created=boom)
    with pytest.raises(RuntimeError, match="callback failed"):
        with traced:
            pass
    assert handler.finalize_stream_calls == 1
    assert isinstance(handler.finalize_exceptions[0], RuntimeError)
    assert traced._self_entered_stream is None


@pytest.mark.asyncio
async def test_traced_async_stream_finalizes_when_on_stream_created_raises():
    handler = _AsyncRecordingHandler()

    def boom(_stream):
        raise RuntimeError("callback failed")

    traced = make_traced_stream(_AsyncStreamManager(3), handler, on_stream_created=boom)
    with pytest.raises(RuntimeError, match="callback failed"):
        async with traced:
            pass
    assert handler.finalize_stream_calls == 1
    assert isinstance(handler.finalize_exceptions[0], RuntimeError)
    assert traced._self_entered_stream is None


def test_langchain_finalize_skips_aiguard_finally_when_stream_never_started():
    from ddtrace.contrib.internal.langchain.utils import LangchainStreamHandler

    span = Mock()
    handler = LangchainStreamHandler(None, span, (), {}, aiguard_finally_event="langchain.llm.stream.finally")
    with patch("ddtrace.contrib.internal.langchain.utils.core.dispatch") as dispatch:
        handler.finalize_stream()
    dispatch.assert_not_called()
    span.finish.assert_called_once()

    started = LangchainStreamHandler(None, Mock(), (), {}, aiguard_finally_event="langchain.llm.stream.finally")
    started._stream_started = True
    with patch("ddtrace.contrib.internal.langchain.utils.core.dispatch") as dispatch:
        started.finalize_stream()
    dispatch.assert_called_once_with("langchain.llm.stream.finally", ())
