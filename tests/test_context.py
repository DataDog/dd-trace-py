import mock
import threading

from unittest import TestCase
from nose.tools import eq_, ok_
from tests.test_tracer import get_dummy_tracer

from ddtrace.span import Span
from ddtrace.context import Context, ThreadLocalContext


class TestTracingContext(TestCase):
    """
    Tests related to the ``Context`` class that hosts the trace for the
    current execution flow.
    """
    def test_add_span(self):
        # it should add multiple spans
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        eq_(1, len(ctx._trace))
        eq_('fake_span', ctx._trace[0].name)
        eq_(ctx, span.context)

    def test_context_sampled(self):
        # a context is sampled if the spans are sampled
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        ok_(ctx._sampled is True)

    def test_current_span(self):
        # it should return the current active span
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        eq_(span, ctx.get_current_span())

    def test_close_span(self):
        # it should keep track of closed spans, moving
        # the current active to it's parent
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        ctx.close_span(span)
        eq_(1, ctx._finished_spans)
        ok_(ctx.get_current_span() is None)

    def test_get_trace(self):
        # it should return the internal trace structure
        # if the context is finished
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        ctx.close_span(span)
        trace, sampled = ctx.get()
        eq_(1, len(trace))
        eq_(span, trace[0])
        ok_(sampled is True)
        # the context should be empty
        eq_(0, len(ctx._trace))
        eq_(0, ctx._finished_spans)
        ok_(ctx._current_span is None)
        ok_(ctx._sampled is True)

    def test_get_trace_empty(self):
        # it should return None if the Context is not finished
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        trace, sampled = ctx.get()
        ok_(trace is None)
        ok_(sampled is None)

    def test_finished(self):
        # a Context is finished if all spans inside are finished
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        ctx.close_span(span)
        ok_(ctx.is_finished())

    def test_finished_empty(self):
        # a Context is not finished if it's empty
        ctx = Context()
        ok_(ctx.is_finished() is False)

    @mock.patch('logging.Logger.debug')
    def test_log_unfinished_spans(self, log):
        # when the root parent is finished, notify if there are spans still pending
        tracer = get_dummy_tracer()
        tracer.debug_logging = True
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
        ok_(ctx.is_finished() is False)
        unfinished_spans_log = log.call_args_list[-3][0][2]
        child_1_log = log.call_args_list[-2][0][1]
        child_2_log = log.call_args_list[-1][0][1]
        eq_(2, unfinished_spans_log)
        ok_('name child_1' in child_1_log)
        ok_('name child_2' in child_2_log)
        ok_('duration 0.000000s' in child_1_log)
        ok_('duration 0.000000s' in child_2_log)

    @mock.patch('logging.Logger.debug')
    def test_log_unfinished_spans_disabled(self, log):
        # the trace finished status logging is disabled
        tracer = get_dummy_tracer()
        tracer.debug_logging = False
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
        ok_(ctx.is_finished() is False)
        # the logger has never been invoked to print unfinished spans
        for call, _ in log.call_args_list:
            msg = call[0]
            ok_('the trace has %d unfinished spans' not in msg)

    @mock.patch('logging.Logger.debug')
    def test_log_unfinished_spans_when_ok(self, log):
        # if the unfinished spans logging is enabled but the trace is finished, don't log anything
        tracer = get_dummy_tracer()
        tracer.debug_logging = True
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
            ok_('the trace has %d unfinished spans' not in msg)

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

        eq_(100, len(ctx._trace))


class TestThreadContext(TestCase):
    """
    Ensures that a ``ThreadLocalContext`` makes the Context
    local to each thread.
    """
    def test_get_or_create(self):
        # asking the Context multiple times should return
        # always the same instance
        l_ctx = ThreadLocalContext()
        eq_(l_ctx.get(), l_ctx.get())

    def test_set_context(self):
        # the Context can be set in the current Thread
        ctx = Context()
        local = ThreadLocalContext()
        ok_(local.get() is not ctx)

        local.set(ctx)
        ok_(local.get() is ctx)

    def test_multiple_threads_multiple_context(self):
        # each thread should have it's own Context
        l_ctx = ThreadLocalContext()

        def _fill_ctx():
            ctx = l_ctx.get()
            span = Span(tracer=None, name='fake_span')
            ctx.add_span(span)
            eq_(1, len(ctx._trace))

        threads = [threading.Thread(target=_fill_ctx) for _ in range(100)]

        for t in threads:
            t.daemon = True
            t.start()

        for t in threads:
            t.join()

        # the main instance should have an empty Context
        # because it has not been used in this thread
        ctx = l_ctx.get()
        eq_(0, len(ctx._trace))
