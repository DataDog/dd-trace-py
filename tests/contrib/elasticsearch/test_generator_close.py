"""Unit tests for elasticsearch generator close on TransportError.

Verifies that when a TransportError is raised during perform_request,
the generator coroutine is properly closed, ensuring the tracing span
is finalized immediately rather than becoming a zombie span (gh #17100).
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from ddtrace.contrib.internal.elasticsearch.patch import (
    _get_perform_request,
    _get_perform_request_async,
)
from tests.utils import TracerTestCase


class _FakeTransportError(Exception):
    """Simulates elasticsearch.TransportError (HTTP 400/500 response errors)."""

    status_code = 400


def _make_transport() -> MagicMock:
    """Create a mock transport module with necessary attributes."""
    transport = MagicMock()
    transport._datadog_patch = False
    transport.TransportError = _FakeTransportError
    return transport


def _make_instance() -> MagicMock:
    """Create a mock ES transport instance with connection pool."""
    instance = MagicMock()
    conn = MagicMock()
    conn.host = "http://localhost:9200"
    instance.connection_pool.connections = [conn]
    instance.serializer.dumps.return_value = "{}"
    return instance


class TestGeneratorCloseOnTransportError(TracerTestCase):
    """Tests that the tracing generator coroutine is closed when TransportError is raised.

    The bug (gh #17100): when TransportError escapes next(coro) / await next(coro),
    the generator holding the span's with-block is left open until GC — producing
    zombie spans with inflated durations.  The fix calls coro.close() in the except
    clause so span.finish() runs immediately.
    """

    def test_sync_generator_closed_on_transport_error(self) -> None:
        """Sync path: TransportError must finish the span immediately, not at GC time."""
        transport = _make_transport()
        instance = _make_instance()

        def failing_func(*args, **kwargs):
            raise _FakeTransportError("index_not_found_exception")

        perform_request = _get_perform_request(transport)

        with self.tracer.trace("test_parent"):
            with pytest.raises(_FakeTransportError):
                perform_request(failing_func, instance, ("GET", "/_search"), {})

        spans = self.pop_spans()
        assert len(spans) >= 2, f"Expected at least 2 spans (parent + es query), got {len(spans)}"

        es_spans = [s for s in spans if s.name == "elasticsearch.query"]
        assert len(es_spans) == 1, f"Expected exactly 1 elasticsearch.query span, got {len(es_spans)}"

        es_span = es_spans[0]
        assert es_span.finished, "Span must be finished immediately — not a zombie awaiting GC"
        assert es_span.error == 1, "Span must be marked as error=1 for TransportError"
        assert es_span.get_tag("http.status_code") == "400", (
            f"Expected http.status_code=400, got {es_span.get_tag('http.status_code')}"
        )
        assert es_span.get_tag("component") == "elasticsearch", (
            f"Expected component=elasticsearch, got {es_span.get_tag('component')}"
        )
        assert es_span.get_tag("span.kind") == "client", (
            f"Expected span.kind=client, got {es_span.get_tag('span.kind')}"
        )
        # Duration must be sub-second — a zombie span (finalized by GC) would have a
        # duration of many seconds or minutes.
        assert es_span.duration_ns < 1_000_000_000, (
            f"Span duration {es_span.duration_ns}ns exceeds 1s — likely a zombie span "
            "not finalized until GC"
        )

    def test_async_generator_closed_on_transport_error(self) -> None:
        """Async path: TransportError must finish the span immediately, not at GC time."""
        transport = _make_transport()
        instance = _make_instance()

        def failing_func(*args, **kwargs):
            raise _FakeTransportError("index_not_found_exception")

        perform_request = _get_perform_request_async(transport)

        async def run() -> None:
            with self.tracer.trace("test_parent_async"):
                with pytest.raises(_FakeTransportError):
                    await perform_request(failing_func, instance, ("GET", "/_search"), {})

        asyncio.get_event_loop().run_until_complete(run())

        spans = self.pop_spans()
        assert len(spans) >= 2, f"Expected at least 2 spans (parent + es query), got {len(spans)}"

        es_spans = [s for s in spans if s.name == "elasticsearch.query"]
        assert len(es_spans) == 1, f"Expected exactly 1 elasticsearch.query span, got {len(es_spans)}"

        es_span = es_spans[0]
        assert es_span.finished, "Span must be finished immediately — not a zombie awaiting GC"
        assert es_span.error == 1, "Span must be marked as error=1 for TransportError"
        assert es_span.get_tag("http.status_code") == "400", (
            f"Expected http.status_code=400, got {es_span.get_tag('http.status_code')}"
        )
        assert es_span.get_tag("component") == "elasticsearch", (
            f"Expected component=elasticsearch, got {es_span.get_tag('component')}"
        )
        assert es_span.get_tag("span.kind") == "client", (
            f"Expected span.kind=client, got {es_span.get_tag('span.kind')}"
        )
        assert es_span.duration_ns < 1_000_000_000, (
            f"Span duration {es_span.duration_ns}ns exceeds 1s — likely a zombie span "
            "not finalized until GC"
        )
