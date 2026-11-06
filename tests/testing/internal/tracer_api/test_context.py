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


@pytest.fixture(autouse=True)
def _reset_tracer_context():
    """Clear the global tracer's active context before and after each test.

    These tests touch the real tracer (not a mock), so leftover spans from prior tests
    in the same xdist worker would pollute current_root_span() assertions.
    """
    from ddtrace.trace import tracer

    tracer.context_provider.activate(None)
    yield
    tracer.context_provider.activate(None)


@pytest.fixture
def span_processor():
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


def test_trace_context_finishes_root_retained_by_copied_context():
    import contextvars

    from ddtrace.trace import tracer

    with _ddtrace_context():
        root = tracer.current_root_span()
        copied_context = contextvars.copy_context()

    def create_span_after_test():
        assert tracer.current_root_span() is None
        child = tracer.trace("after.test")
        try:
            return child.parent_id
        finally:
            child.finish()

    parent_id = copied_context.run(create_span_after_test)

    assert root is not None
    assert root.finished
    # A copied execution context must not retain the finished phantom Span as a parent.
    assert parent_id != root.span_id


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


class TestStartSpanParity:
    """The phantom root must tag itself the way Tracer.start_span() would.

    Child spans inherit service from their parent and only receive the version tag when
    the root already carries it, so a root missing those loses service and version on
    every span created inside a test. These tests pin parity against the real
    tracer.trace() path rather than against hardcoded expectations, so they keep failing
    if Tracer.start_span() grows tagging the phantom does not replicate.
    """

    @pytest.fixture
    def dd_identity(self):
        from ddtrace import config

        original = (config.service, config.env, config.version)
        config.service, config.env, config.version = "svc-parity", "env-parity", "9.9.9"
        yield
        config.service, config.env, config.version = original

    def _identity_of(self, span):
        from ddtrace.constants import ENV_KEY
        from ddtrace.constants import VERSION_KEY

        return (span.service, span.get_tag(ENV_KEY), span.get_tag(VERSION_KEY))

    def test_root_identity_matches_tracer_trace(self, dd_identity):
        from ddtrace.trace import tracer

        with _ddtrace_context():
            phantom = self._identity_of(tracer.current_root_span())

        tracer.context_provider.activate(None)
        with tracer.trace(DDTESTOPT_ROOT_SPAN_RESOURCE) as reference:
            expected = self._identity_of(reference)

        assert phantom == expected

    def test_child_inherits_service_and_version(self, dd_identity):
        from ddtrace.trace import tracer

        with _ddtrace_context():
            with tracer.trace("child") as child:
                phantom_child = self._identity_of(child)

        tracer.context_provider.activate(None)
        with tracer.trace(DDTESTOPT_ROOT_SPAN_RESOURCE):
            with tracer.trace("child") as reference_child:
                expected = self._identity_of(reference_child)

        assert phantom_child == expected
        # Guard against the assertion above passing because both sides are empty.
        assert phantom_child == ("svc-parity", "env-parity", "9.9.9")

    def test_service_mapping_is_applied(self, dd_identity):
        from ddtrace import config
        from ddtrace.trace import tracer

        original_mapping = config.service_mapping
        config.service_mapping = {"svc-parity": "svc-mapped"}
        try:
            with _ddtrace_context():
                assert tracer.current_root_span().service == "svc-mapped"
                with tracer.trace("child") as child:
                    assert child.service == "svc-mapped"
        finally:
            config.service_mapping = original_mapping
