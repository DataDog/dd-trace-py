"""Tests for the trace context used by the ddtrace pytest plugin.

The phantom root span approach creates a lightweight root Span directly (bypassing
tracer.trace()'s full pipeline) to reduce per-test overhead. These tests verify that
it still provides the properties the plugin and downstream integrations rely on:
trace_id/span_id for the test run, correct child-span parenting, the type=test beacon
for Selenium, and proper cleanup.
"""

import pytest

from ddtrace.testing.internal.tracer_api.context import _ddtrace_context
from ddtrace.testing.internal.tracer_api.context import trace_context
from ddtrace.testing.internal.tracer_api.span_processor import TestOptSpanProcessor
from ddtrace.testing.internal.utils import DDTESTOPT_ROOT_SPAN_RESOURCE
from ddtrace.testing.internal.utils import DDTraceTestContext


@pytest.fixture
def span_processor(monkeypatch):
    """Install a TestOptSpanProcessor with a mock writer and restore config after."""
    from unittest.mock import Mock

    from ddtrace.trace import tracer

    writer = Mock()
    writer._events = []
    writer.put_event = lambda e: writer._events.append(e)

    processor = TestOptSpanProcessor(writer)
    tracer.configure(trace_processors=[processor])
    yield writer
    # Restore: remove our processor
    tracer.configure(trace_processors=[])
    tracer.context_provider.activate(None)


def test_trace_context_provides_ids_and_type_tag():
    with _ddtrace_context() as ctx:
        assert isinstance(ctx, DDTraceTestContext)
        assert ctx.trace_id is not None
        assert ctx.span_id is not None
        # The root span must carry type=test for the Selenium integration.
        tags = ctx.get_tags()
        assert tags.get("type") == "test"
        assert tags.get("span.kind") == "test"


def test_trace_context_current_root_span_has_type_test():
    from ddtrace.trace import tracer

    with _ddtrace_context():
        root = tracer.current_root_span()
        assert root is not None
        assert root.resource == DDTESTOPT_ROOT_SPAN_RESOURCE
        assert root.get_tag("type") == "test"


def test_trace_context_child_spans_are_parented():
    from ddtrace.trace import tracer

    with _ddtrace_context() as ctx:
        child = tracer.trace("http.request")
        assert child.parent_id == ctx.span_id
        assert child.trace_id == ctx.trace_id or child.trace_id % (1 << 64) == ctx.trace_id
        child.finish()


def test_trace_context_child_spans_become_events(span_processor):
    from ddtrace.trace import tracer

    with _ddtrace_context():
        child = tracer.trace("http.request")
        child.finish()

    assert len(span_processor._events) == 1
    event = span_processor._events[0]
    assert event["type"] == "span"
    assert event["content"]["resource"] == "http.request"
    assert event["content"]["parent_id"] is not None  # parented to the phantom root


def test_trace_context_multiple_child_spans(span_processor):
    from ddtrace.trace import tracer

    with _ddtrace_context():
        child1 = tracer.trace("http.request")
        child1.finish()
        child2 = tracer.trace("db.query")
        child2.finish()

    assert len(span_processor._events) == 2
    resources = {e["content"]["resource"] for e in span_processor._events}
    assert resources == {"http.request", "db.query"}


def test_trace_context_cleans_up_after_exit():
    from ddtrace.trace import tracer

    assert tracer.current_root_span() is None
    with _ddtrace_context():
        assert tracer.current_root_span() is not None
    # After the context exits, the root span should be deactivated.
    assert tracer.current_root_span() is None


def test_trace_context_clears_leftover_spans():
    """A buggy integration leaving an unfinished span must not affect the next test."""
    from ddtrace.trace import tracer

    # Simulate a leftover span from a previous test.
    leftover = tracer.trace("leftover")
    # Don't finish it — it's still active.

    with _ddtrace_context() as ctx:
        root = tracer.current_root_span()
        assert root is not None
        assert root.resource == DDTESTOPT_ROOT_SPAN_RESOURCE
        # The new test's children should be parented to the new root, not the leftover.
        child = tracer.trace("child")
        assert child.parent_id == ctx.span_id
        child.finish()

    # Clean up the leftover.
    leftover.finish()
    tracer.context_provider.activate(None)


def test_trace_context_with_ddtrace_disabled():
    """When ddtrace is not enabled, a plain context with fresh IDs is used."""
    with trace_context(False) as ctx:
        from ddtrace.trace import tracer

        # No root span should be active.
        assert tracer.current_root_span() is None
        assert ctx.trace_id is not None
        assert ctx.span_id is not None
