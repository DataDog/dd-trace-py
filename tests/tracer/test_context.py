import logging
import mock
import threading

import pytest

from ddtrace.span import Span
from ddtrace.context import Context
from ddtrace.constants import HOSTNAME_KEY
from ddtrace.ext.priority import USER_REJECT, AUTO_REJECT, AUTO_KEEP, USER_KEEP

from .test_tracer import get_dummy_tracer
from tests import BaseTestCase


@pytest.fixture
def tracer_with_debug_logging():
    # All the tracers, dummy or not, shares the same logging object.
    tracer = get_dummy_tracer()
    level = tracer.log.level
    tracer.log.setLevel(logging.DEBUG)
    try:
        yield tracer
    finally:
        tracer.log.setLevel(level)


@mock.patch('logging.Logger.debug')
def test_log_unfinished_spans(log, tracer_with_debug_logging):
    # when the root parent is finished, notify if there are spans still pending
    tracer = tracer_with_debug_logging
    ctx = Context()
    # manually create a root-child trace
    root = Span(tracer=tracer, name='root')
    child_1 = Span(tracer=tracer, name='child_1', trace_id=root.trace_id, parent_id=root.span_id)
    child_2 = Span(tracer=tracer, name='child_2', trace_id=root.trace_id, parent_id=root.span_id)
    child_1._parent = root
    child_2._parent = root
    ctx.add_span(root)
    ctx.add_span(child_1)
    ctx.add_span(child_2)
    # close only the parent
    root.finish()
    unfinished_spans_log = log.call_args_list[-3][0][2]
    child_1_log = log.call_args_list[-2][0][1]
    child_2_log = log.call_args_list[-1][0][1]
    assert 2 == unfinished_spans_log
    assert 'name child_1' in child_1_log
    assert 'name child_2' in child_2_log
    assert 'duration 0.000000s' in child_1_log
    assert 'duration 0.000000s' in child_2_log


class TestTracingContext(BaseTestCase):
    """
    Tests related to the ``Context`` class that hosts the trace for the
    current execution flow.
    """

    def test_add_span(self):
        # it should add multiple spans
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        assert 1 == len(ctx._trace)
        assert 'fake_span' == ctx._trace[0].name
        assert ctx == span.context

    def test_context_sampled(self):
        # a context is sampled if the spans are sampled
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        span.finished = True
        trace, sampled = ctx.close_span(span)

        assert sampled is True
        assert ctx.sampling_priority is None

    def test_context_priority(self):
        # a context is sampled if the spans are sampled
        ctx = Context()
        for priority in [USER_REJECT, AUTO_REJECT, AUTO_KEEP, USER_KEEP, None, 999]:
            ctx.sampling_priority = priority
            span = Span(tracer=None, name=('fake_span_%s' % repr(priority)))
            ctx.add_span(span)
            span.finished = True
            # It's "normal" to have sampled be true even when priority sampling is
            # set to 0 or -1. It would stay false even even with priority set to 2.
            # The only criteria to send (or not) the spans to the agent should be
            # this "sampled" attribute, as it's tightly related to the trace weight.
            assert priority == ctx.sampling_priority
            trace, sampled = ctx.close_span(span)
            assert sampled is True, 'priority has no impact on sampled status'

    def test_current_span(self):
        # it should return the current active span
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        assert span == ctx.get_current_span()

    def test_current_root_span_none(self):
        # it should return none when there is no root span
        ctx = Context()
        assert ctx.get_current_root_span() is None

    def test_current_root_span(self):
        # it should return the current active root span
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        assert span == ctx.get_current_root_span()

    def test_close_span(self):
        # it should keep track of closed spans, moving
        # the current active to its parent
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        ctx.close_span(span)
        assert ctx.get_current_span() is None

    def test_get_trace(self):
        # it should return the internal trace structure
        # if the context is finished
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        span.finished = True
        trace, sampled = ctx.close_span(span)
        assert [span] == trace
        assert sampled is True
        # the context should be empty
        assert 0 == len(ctx._trace)
        assert ctx._current_span is None

    @mock.patch('ddtrace.internal.hostname.get_hostname')
    def test_get_report_hostname_enabled(self, get_hostname):
        get_hostname.return_value = 'test-hostname'

        with self.override_global_config(dict(report_hostname=True)):
            # Create a context and add a span and finish it
            ctx = Context()
            span = Span(tracer=None, name='fake_span')
            ctx.add_span(span)
            span.finish()
            assert span.get_tag(HOSTNAME_KEY) == 'test-hostname'

    @mock.patch('ddtrace.internal.hostname.get_hostname')
    def test_get_report_hostname_disabled(self, get_hostname):
        get_hostname.return_value = 'test-hostname'

        with self.override_global_config(dict(report_hostname=False)):
            # Create a context and add a span and finish it
            ctx = Context()
            span = Span(tracer=None, name='fake_span')
            ctx.add_span(span)
            span.finished = True

            # Assert that we have not added the tag to the span yet
            assert span.get_tag(HOSTNAME_KEY) is None

            # Assert that retrieving the trace does not set the tag
            trace, _ = ctx.close_span(span)
            assert trace[0].get_tag(HOSTNAME_KEY) is None
            assert span.get_tag(HOSTNAME_KEY) is None

    @mock.patch('ddtrace.internal.hostname.get_hostname')
    def test_get_report_hostname_default(self, get_hostname):
        get_hostname.return_value = 'test-hostname'

        # Create a context and add a span and finish it
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        span.finished = True

        # Assert that we have not added the tag to the span yet
        assert span.get_tag(HOSTNAME_KEY) is None

        # Assert that retrieving the trace does not set the tag
        trace, _ = ctx.close_span(span)
        assert trace[0].get_tag(HOSTNAME_KEY) is None
        assert span.get_tag(HOSTNAME_KEY) is None

    def test_finished(self):
        # a Context is finished if all spans inside are finished
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        ctx.close_span(span)

    @mock.patch('logging.Logger.debug')
    def test_log_unfinished_spans_disabled(self, log):
        # the trace finished status logging is disabled
        tracer = get_dummy_tracer()
        ctx = Context()
        # manually create a root-child trace
        root = Span(tracer=tracer, name='root')
        child_1 = Span(tracer=tracer, name='child_1', trace_id=root.trace_id, parent_id=root.span_id)
        child_2 = Span(tracer=tracer, name='child_2', trace_id=root.trace_id, parent_id=root.span_id)
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
            assert 'the trace has %d unfinished spans' not in msg

    @mock.patch('logging.Logger.debug')
    def test_log_unfinished_spans_when_ok(self, log):
        # if the unfinished spans logging is enabled but the trace is finished, don't log anything
        tracer = get_dummy_tracer()
        ctx = Context()
        # manually create a root-child trace
        root = Span(tracer=tracer, name='root')
        child = Span(tracer=tracer, name='child_1', trace_id=root.trace_id, parent_id=root.span_id)
        child._parent = root
        ctx.add_span(root)
        ctx.add_span(child)
        # close the trace
        child.finish()
        root.finish()
        # the logger has never been invoked to print unfinished spans
        for call, _ in log.call_args_list:
            msg = call[0]
            assert 'the trace has %d unfinished spans' not in msg

    def test_thread_safe(self):
        # the Context must be thread-safe
        ctx = Context()

        def _fill_ctx():
            span = Span(tracer=None, name='fake_span')
            ctx.add_span(span)

        threads = [threading.Thread(target=_fill_ctx) for _ in range(100)]

        for t in threads:
            t.daemon = True
            t.start()

        for t in threads:
            t.join()

        assert 100 == len(ctx._trace)

    def test_clone(self):
        ctx = Context()
        ctx.sampling_priority = 2
        # manually create a root-child trace
        root = Span(tracer=None, name='root')
        child = Span(tracer=None, name='child_1', trace_id=root.trace_id, parent_id=root.span_id)
        child._parent = root
        ctx.add_span(root)
        ctx.add_span(child)
        cloned_ctx = ctx.clone()
        assert cloned_ctx._parent_trace_id == ctx._parent_trace_id
        assert cloned_ctx._parent_span_id == ctx._parent_span_id
        assert cloned_ctx._sampling_priority == ctx._sampling_priority
        assert cloned_ctx._dd_origin == ctx._dd_origin
        assert cloned_ctx._current_span == ctx._current_span
        assert cloned_ctx._trace == []
