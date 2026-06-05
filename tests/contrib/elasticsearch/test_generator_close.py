"""Unit tests for elasticsearch generator close on TransportError.

Verifies that when a TransportError is raised during perform_request,
the generator coroutine is explicitly closed via coro.close(), ensuring
the tracing span is finalized immediately rather than becoming a zombie
span (gh #17100).

NOTE on CPython refcounting: asserting ``span.finished`` alone does NOT
distinguish between "closed explicitly by the fix" and "closed by GC
before the assertion ran".  Under CPython the generator may be
immediately collected when the local variable goes out of scope, making
the ``span.finished`` assertion pass even WITHOUT the fix.

The primary tests use a proxy wrapper around the generator to spy on
``.close()`` calls — a deterministic signal that works regardless of GC
timing or interpreter.  Generator objects don't allow replacing instance
methods, so the spy uses a proxy class that delegates all generator
protocol methods and intercepts ``.close()``.
"""

import asyncio
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import patch as mock_patch

import pytest

import ddtrace.contrib.internal.elasticsearch.patch as es_patch_module
from ddtrace.contrib.internal.elasticsearch.patch import _get_perform_request
from ddtrace.contrib.internal.elasticsearch.patch import _get_perform_request_async
from ddtrace.contrib.internal.elasticsearch.patch import _get_perform_request_coro
from tests.utils import TracerTestCase


class _FakeTransportError(Exception):
    """Simulates elasticsearch.TransportError (e.g. HTTP 400 index_not_found)."""

    status_code = 400


def _make_transport() -> MagicMock:
    """Mock transport module with the minimum attributes needed by the generator."""
    transport = MagicMock()
    transport._datadog_patch = False
    transport.TransportError = _FakeTransportError
    return transport


def _make_instance() -> MagicMock:
    """Mock ES transport instance with connection pool."""
    instance = MagicMock()
    conn = MagicMock()
    conn.host = "http://localhost:9200"
    instance.connection_pool.connections = [conn]
    instance.serializer.dumps.return_value = "{}"
    return instance


class _SpyGenerator:
    """Proxy that wraps a generator and records explicit .close() calls.

    Generator objects don't allow replacing instance attributes (the C slot
    for ``close`` is read-only), so we must use a proxy object that implements
    the generator protocol and intercepts ``.close()``.

    ``close_log`` is a list that is appended to each time ``.close()`` is
    called explicitly (i.e., by the fix code).  The test asserts it is
    non-empty after a failed request.
    """

    def __init__(self, gen: Any, close_log: list) -> None:
        self._gen = gen
        self._close_log = close_log

    def __iter__(self) -> "_SpyGenerator":
        return self

    def __next__(self) -> Any:
        return next(self._gen)

    def send(self, value: Any) -> Any:
        return self._gen.send(value)

    def throw(self, typ: Any, val: Any = None, tb: Any = None) -> Any:
        if val is None and tb is None:
            return self._gen.throw(typ)
        if tb is None:
            return self._gen.throw(typ, val)
        return self._gen.throw(typ, val, tb)

    def close(self) -> None:
        self._close_log.append(True)
        self._gen.close()


def _make_tracked_get_coro(close_called: list) -> Any:
    """Return a patched _get_perform_request_coro that wraps each generator in _SpyGenerator.

    The original ``_get_perform_request_coro(transport)`` returns a generator function.
    Each call to that generator function produces a generator instance.  This wrapper
    intercepts the generator instance and wraps it in ``_SpyGenerator`` so that any
    explicit ``.close()`` call is recorded in ``close_called``.
    """
    original = _get_perform_request_coro

    def tracked_get_coro(transport: Any) -> Any:
        original_coro_func = original(transport)

        def tracked_coro_func(*args: Any, **kwargs: Any) -> _SpyGenerator:
            gen = original_coro_func(*args, **kwargs)
            return _SpyGenerator(gen, close_called)

        return tracked_coro_func

    return tracked_get_coro


class TestGeneratorCloseOnTransportError(TracerTestCase):
    """Regression tests for the zombie-span bug (gh #17100).

    The spy-based tests assert that coro.close() is called explicitly when a
    TransportError is raised.  This is a strict, GC-independent signal that the
    fix is active — it fails even on Python 3.12 where CPython auto-closes
    generators at frame exit, because the spy proxy's .close() is only invoked
    by the explicit call in the fix, not by CPython's automatic frame cleanup.

    Supplementary tests verify span tags and duration correctness.
    """

    # ------------------------------------------------------------------
    # Spy tests: FAIL before fix, PASS after
    # ------------------------------------------------------------------

    def test_sync_coro_close_called_on_transport_error(self) -> None:
        """Sync: coro.close() must be called explicitly when TransportError is raised.

        FAILS before the fix (no try/except + coro.close() in _get_perform_request).
        PASSES after the fix.
        """
        transport = _make_transport()
        instance = _make_instance()
        close_called: list = []

        def failing_func(*args: Any, **kwargs: Any) -> None:
            raise _FakeTransportError("index_not_found_exception")

        tracked_get_coro = _make_tracked_get_coro(close_called)

        with mock_patch.object(es_patch_module, "_get_perform_request_coro", tracked_get_coro):
            perform_request = _get_perform_request(transport)
            with pytest.raises(_FakeTransportError):
                perform_request(failing_func, instance, ("GET", "/_search"), {})

        assert close_called, (
            "coro.close() was NOT called after TransportError on the sync path. "
            "The fix (try/except + coro.close() in _get_perform_request) is missing — "
            "generator left open, span becomes a zombie (gh #17100)."
        )

    def test_async_coro_close_called_on_transport_error(self) -> None:
        """Async: coro.close() must be called explicitly when TransportError is raised.

        FAILS before the fix (no try/except + coro.close() in _get_perform_request_async).
        PASSES after the fix.
        """
        transport = _make_transport()
        instance = _make_instance()
        close_called: list = []

        def failing_func(*args: Any, **kwargs: Any) -> None:
            raise _FakeTransportError("index_not_found_exception")

        tracked_get_coro = _make_tracked_get_coro(close_called)

        async def run() -> None:
            with mock_patch.object(es_patch_module, "_get_perform_request_coro", tracked_get_coro):
                perform_request = _get_perform_request_async(transport)
                with pytest.raises(_FakeTransportError):
                    await perform_request(failing_func, instance, ("GET", "/_search"), {})

        asyncio.get_event_loop().run_until_complete(run())

        assert close_called, (
            "coro.close() was NOT called after TransportError on the async path. "
            "The fix (try/except + coro.close() in _get_perform_request_async) is missing — "
            "generator left open, span becomes a zombie (gh #17100)."
        )

    # ------------------------------------------------------------------
    # Supplementary span-tag tests: verify correct observability data
    # ------------------------------------------------------------------

    def test_sync_span_finished_and_tagged_on_transport_error(self) -> None:
        """Sync: span must carry full observability data after TransportError."""
        transport = _make_transport()
        instance = _make_instance()

        def failing_func(*args: Any, **kwargs: Any) -> None:
            raise _FakeTransportError("index_not_found_exception")

        perform_request = _get_perform_request(transport)

        with self.tracer.trace("test_parent"):
            with pytest.raises(_FakeTransportError):
                perform_request(failing_func, instance, ("GET", "/_search"), {})

        spans = self.pop_spans()
        assert len(spans) >= 2, f"Expected at least 2 spans (parent + elasticsearch.query), got {len(spans)}"
        es_spans = [s for s in spans if s.name == "elasticsearch.query"]
        assert len(es_spans) == 1, f"Expected exactly 1 elasticsearch.query span, got {len(es_spans)}"
        es_span = es_spans[0]

        assert es_span.finished, "Span must be finished — not a zombie awaiting GC"
        assert es_span.error == 1, "Span must be error=1 for TransportError"
        assert es_span.get_tag("http.status_code") == "400", (
            f"Expected http.status_code=400, got {es_span.get_tag('http.status_code')}"
        )
        assert es_span.get_tag("component") == "elasticsearch", (
            f"Expected component=elasticsearch, got {es_span.get_tag('component')}"
        )
        assert es_span.get_tag("span.kind") == "client", (
            f"Expected span.kind=client, got {es_span.get_tag('span.kind')}"
        )
        assert es_span.get_tag("elasticsearch.method") == "GET", (
            f"Expected elasticsearch.method=GET, got {es_span.get_tag('elasticsearch.method')}"
        )
        assert es_span.get_tag("elasticsearch.url") == "/_search", (
            f"Expected elasticsearch.url=/_search, got {es_span.get_tag('elasticsearch.url')}"
        )
        assert es_span.duration_ns is not None, "Span duration must be set"
        assert es_span.duration_ns < 1_000_000_000, (
            f"Span duration {es_span.duration_ns}ns exceeds 1s — possible zombie span"
        )

    def test_async_span_finished_and_tagged_on_transport_error(self) -> None:
        """Async: span must carry full observability data after TransportError."""
        transport = _make_transport()
        instance = _make_instance()

        def failing_func(*args: Any, **kwargs: Any) -> None:
            raise _FakeTransportError("index_not_found_exception")

        perform_request = _get_perform_request_async(transport)

        async def run() -> None:
            with self.tracer.trace("test_parent_async"):
                with pytest.raises(_FakeTransportError):
                    await perform_request(failing_func, instance, ("GET", "/_search"), {})

        asyncio.get_event_loop().run_until_complete(run())

        spans = self.pop_spans()
        assert len(spans) >= 2, f"Expected at least 2 spans (parent + elasticsearch.query), got {len(spans)}"
        es_spans = [s for s in spans if s.name == "elasticsearch.query"]
        assert len(es_spans) == 1, f"Expected exactly 1 elasticsearch.query span, got {len(es_spans)}"
        es_span = es_spans[0]

        assert es_span.finished, "Span must be finished — not a zombie awaiting GC"
        assert es_span.error == 1, "Span must be error=1 for TransportError"
        assert es_span.get_tag("http.status_code") == "400", (
            f"Expected http.status_code=400, got {es_span.get_tag('http.status_code')}"
        )
        assert es_span.get_tag("component") == "elasticsearch", (
            f"Expected component=elasticsearch, got {es_span.get_tag('component')}"
        )
        assert es_span.get_tag("span.kind") == "client", (
            f"Expected span.kind=client, got {es_span.get_tag('span.kind')}"
        )
        assert es_span.get_tag("elasticsearch.method") == "GET", (
            f"Expected elasticsearch.method=GET, got {es_span.get_tag('elasticsearch.method')}"
        )
        assert es_span.get_tag("elasticsearch.url") == "/_search", (
            f"Expected elasticsearch.url=/_search, got {es_span.get_tag('elasticsearch.url')}"
        )
        assert es_span.duration_ns is not None, "Span duration must be set"
        assert es_span.duration_ns < 1_000_000_000, (
            f"Span duration {es_span.duration_ns}ns exceeds 1s — possible zombie span"
        )
