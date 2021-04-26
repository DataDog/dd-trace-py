import logging

import mock
import pytest

from ddtrace.context import Context
from ddtrace.span import Span
from tests.utils import BaseTestCase
from tests.utils import DummyTracer


@pytest.fixture
def tracer_with_debug_logging():
    # All the tracers, dummy or not, shares the same logging object.
    tracer = DummyTracer()
    level = tracer.log.level
    tracer.log.setLevel(logging.DEBUG)
    try:
        yield tracer
    finally:
        tracer.log.setLevel(level)


class TestTracingContext(BaseTestCase):
    """
    Tests related to the ``Context`` class that hosts the trace for the
    current execution flow.
    """

    def test_add_span(self):
        ctx = Context()
        span = Span(tracer=None, name="fake_span")
        ctx.add_span(span)
        assert ctx == span.context
        assert ctx.span_id == span.span_id
        assert ctx.get_current_span() == span
        assert ctx.get_current_root_span() == span

        span2 = Span(tracer=None, name="fake_span2")
        span2.parent_id = span.span_id
        span2._parent = span
        ctx.add_span(span2)
        assert ctx == span2.context
        assert ctx.span_id == span2.span_id
        assert ctx.get_current_span() == span2
        assert ctx.get_current_root_span() == span

    def test_current_span(self):
        # it should return the current active span
        ctx = Context()
        span = Span(tracer=None, name="fake_span")
        ctx.add_span(span)
        assert span == ctx.get_current_span()

    def test_current_root_span_none(self):
        # it should return none when there is no root span
        ctx = Context()
        assert ctx.get_current_root_span() is None

    def test_current_root_span(self):
        # it should return the current active root span
        ctx = Context()
        span = Span(tracer=None, name="fake_span")
        ctx.add_span(span)
        assert span == ctx.get_current_root_span()

    def test_close_span(self):
        # it should keep track of closed spans, moving
        # the current active to its parent
        ctx = Context()
        span = Span(tracer=None, name="fake_span")
        ctx.add_span(span)
        ctx.close_span(span)
        assert ctx.get_current_span() is None

    def test_finished(self):
        # a Context is finished if all spans inside are finished
        ctx = Context()
        span = Span(tracer=None, name="fake_span")
        ctx.add_span(span)
        ctx.close_span(span)

    @mock.patch("logging.Logger.debug")
    def test_log_unfinished_spans_disabled(self, log):
        # the trace finished status logging is disabled
        tracer = DummyTracer()
        ctx = Context()
        # manually create a root-child trace
        root = Span(tracer=tracer, name="root")
        child_1 = Span(tracer=tracer, name="child_1", trace_id=root.trace_id, parent_id=root.span_id)
        child_2 = Span(tracer=tracer, name="child_2", trace_id=root.trace_id, parent_id=root.span_id)
        child_1._parent = root
        child_2._parent = root
        ctx.add_span(root)
        ctx.add_span(child_1)
        ctx.add_span(child_2)
        # close only the parent
        root.finish()
        # the logger has never been invoked to print unfinished spans
        for call, _ in log.call_args_list:
            msg = call[0]
            assert "the trace has %d unfinished spans" not in msg

    def test_clone(self):
        ctx = Context()
        ctx.sampling_priority = 2
        # manually create a root-child trace
        root = Span(tracer=None, name="root")
        child = Span(tracer=None, name="child_1", trace_id=root.trace_id, parent_id=root.span_id)
        child._parent = root
        ctx.add_span(root)
        ctx.add_span(child)
        cloned_ctx = ctx.clone()
        assert cloned_ctx._parent_trace_id == ctx._parent_trace_id
        assert cloned_ctx._parent_span_id == ctx._parent_span_id
        assert cloned_ctx._sampling_priority == ctx._sampling_priority
        assert cloned_ctx.dd_origin == ctx.dd_origin
