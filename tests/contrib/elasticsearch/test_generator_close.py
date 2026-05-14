"""Unit test for elasticsearch generator close on TransportError.

Verifies that when a TransportError is raised during perform_request,
the generator coroutine is properly closed, ensuring the tracing span
is finalized immediately rather than becoming a zombie span.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from ddtrace import tracer
from ddtrace.contrib.internal.elasticsearch.patch import (
    _get_perform_request,
    _get_perform_request_async,
    patch,
    unpatch,
)
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME


class _FakeTransportError(Exception):
    """Simulates elasticsearch TransportError."""

    status_code = 500


def _make_transport():
    """Create a mock transport with enough attributes for the generator."""
    transport = MagicMock()
    transport._datadog_patch = False
    return transport


def _make_instance():
    """Create a mock ES instance with connection pool."""
    instance = MagicMock()
    conn = MagicMock()
    conn.host = "localhost:9200"
    instance.connection_pool.connections = [conn]
    instance.serializer.dumps.return_value = "{}"
    return instance


def test_sync_generator_closed_on_transport_error():
    """When TransportError is raised, the sync generator is closed and span finishes immediately."""
    transport = _make_transport()
    instance = _make_instance()

    error = _FakeTransportError("connection error")

    def failing_func(*args, **kwargs):
        raise error

    perform_request = _get_perform_request(transport)

    with tracer.trace("test_parent") as parent:
        with pytest.raises(_FakeTransportError):
            perform_request(failing_func, instance, ("GET", "/_search"), {})

    # The span should have been finished (not left as zombie)
    # Check that parent has exactly one child span that is finished
    spans = tracer.pop()
    # The elasticsearch query span should have been created and finished
    # Since we use tracer.trace inside the generator, the span is created in the tracer
    # and should be closed when coro.close() is called
    assert len(spans) >= 1, f"Expected at least 1 span, got {len(spans)}"
    es_span = spans[0]
    assert es_span.name == "elasticsearch.query"
    assert es_span.finished, "Span should be finished (not a zombie)"
    assert es_span.error == 1, "Span should be marked as error"
    # Duration should be near-instant, not delayed by GC
    assert es_span.duration_ns < 1_000_000_000, f"Span duration too high ({es_span.duration_ns}ns), likely a zombie"


@pytest.mark.asyncio
async def test_async_generator_closed_on_transport_error():
    """When TransportError is raised, the async generator is closed and span finishes immediately."""
    transport = _make_transport()
    instance = _make_instance()

    error = _FakeTransportError("connection error")

    def failing_func(*args, **kwargs):
        raise error

    perform_request = _get_perform_request_async(transport)

    with tracer.trace("test_parent_async") as parent:
        with pytest.raises(_FakeTransportError):
            await perform_request(failing_func, instance, ("GET", "/_search"), {})

    spans = tracer.pop()
    assert len(spans) >= 1, f"Expected at least 1 span, got {len(spans)}"
    es_span = spans[0]
    assert es_span.name == "elasticsearch.query"
    assert es_span.finished, "Span should be finished (not a zombie)"
    assert es_span.error == 1, "Span should be marked as error"
    assert es_span.duration_ns < 1_000_000_000, f"Span duration too high ({es_span.duration_ns}ns), likely a zombie"
