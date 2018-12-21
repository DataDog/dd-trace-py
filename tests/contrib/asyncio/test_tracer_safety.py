# flake8: noqa
# DEV: Skip linting, we lint with Python 2, we'll get SyntaxErrors from `yield from`
import asyncio

from nose.tools import eq_, ok_

from ddtrace.provider import DefaultContextProvider
from .utils import AsyncioTestCase, mark_asyncio


class TestAsyncioSafety(AsyncioTestCase):
    """
    Ensure that if the ``AsyncioTracer`` is not properly configured,
    bad traces are produced but the ``Context`` object will not
    leak memory.
    """
    def setUp(self):
        # Asyncio TestCase with the wrong context provider
        super(TestAsyncioSafety, self).setUp()
        self.tracer.configure(context_provider=DefaultContextProvider())

    @mark_asyncio
    def test_get_call_context(self):
        # it should return a context even if not attached to the Task
        ctx = self.tracer.get_call_context()
        ok_(ctx is not None)
        # test that it behaves the wrong way
        task = asyncio.Task.current_task()
        task_ctx = getattr(task, '__datadog_context', None)
        ok_(task_ctx is None)

    @mark_asyncio
    def test_trace_coroutine(self):
        # it should use the task context when invoked in a coroutine
        with self.tracer.trace('coroutine') as span:
            span.resource = 'base'

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        eq_('coroutine', traces[0][0].name)
        eq_('base', traces[0][0].resource)

    @mark_asyncio
    def test_trace_multiple_calls(self):
        @asyncio.coroutine
        def coro():
            # another traced coroutine
            with self.tracer.trace('coroutine'):
                yield from asyncio.sleep(0.01)

        ctx = self.tracer.get_call_context()
        futures = [asyncio.ensure_future(coro()) for x in range(1000)]
        for future in futures:
            yield from future

        # the trace is wrong but the Context is finished
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1000, len(traces[0]))
        eq_(0, len(ctx._trace))
