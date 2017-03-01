import threading

from unittest import TestCase
from nose.tools import eq_, ok_

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
        ok_(ctx._sampled is False)

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
