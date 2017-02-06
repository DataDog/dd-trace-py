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

    def test_current_span(self):
        # it should return the current active span
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        eq_(span, ctx.get_current_span())

    def test_set_current_span(self):
        # it should set to none the current active span
        # despide the trace length
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        ctx.set_current_span(None)
        ok_(ctx.get_current_span() is None)

    def test_finish_span(self):
        # it should keep track of closed spans, moving
        # the current active to it's parent
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        ctx.finish_span(span)
        eq_(1, ctx._finished_spans)
        ok_(ctx.get_current_span() is None)

    def test_current_trace(self):
        # it should return the internal trace structure
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        trace = ctx.get_current_trace()
        eq_(1, len(trace))
        eq_(span, trace[0])

    def test_finished(self):
        # a Context is finished if all spans inside are finished
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        ctx.finish_span(span)
        ok_(ctx.is_finished)

    def test_reset(self):
        # the Context should be reusable if reset is called
        ctx = Context()
        span = Span(tracer=None, name='fake_span')
        ctx.add_span(span)
        ctx.finish_span(span)
        ctx.reset()
        eq_(0, len(ctx._trace))
        eq_(0, ctx._finished_spans)
        ok_(ctx._current_span is None)

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
