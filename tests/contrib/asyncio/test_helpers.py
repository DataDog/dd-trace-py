import asyncio

from nose.tools import eq_, ok_

from ddtrace.context import Context
from ddtrace.contrib.asyncio import helpers
from .utils import AsyncioTestCase, mark_asyncio


class TestAsyncioHelpers(AsyncioTestCase):
    """
    Ensure that helpers set the ``Context`` properly when creating
    new ``Task`` or threads.
    """
    @mark_asyncio
    def test_set_call_context(self):
        # a different Context is set for the current logical execution
        task = asyncio.Task.current_task()
        ctx = Context()
        helpers.set_call_context(task, ctx)
        eq_(ctx, self.tracer.get_call_context())

    @mark_asyncio
    def test_ensure_future(self):
        # the wrapper should create a new Future that has the Context attached
        @asyncio.coroutine
        def future_work():
            # the ctx is available in this task
            ctx = self.tracer.get_call_context()
            eq_(1, len(ctx._trace))
            eq_('coroutine', ctx._trace[0].name)
            return ctx._trace[0].name

        span = self.tracer.trace('coroutine')
        # schedule future work and wait for a result
        delayed_task = helpers.ensure_future(future_work(), tracer=self.tracer)
        result = yield from asyncio.wait_for(delayed_task, timeout=1)
        eq_('coroutine', result)

    @mark_asyncio
    def test_run_in_executor_proxy(self):
        # the wrapper should pass arguments and results properly
        def future_work(number, name):
            eq_(42, number)
            eq_('john', name)
            return True

        future = helpers.run_in_executor(self.loop, None, future_work, 42, 'john', tracer=self.tracer)
        result = yield from future
        ok_(result)

    @mark_asyncio
    def test_run_in_executor_traces(self):
        # the wrapper should create a different Context when the Thread
        # is started; the new Context creates a new trace
        def future_work():
            # the Context is empty but the reference to the latest
            # span is here to keep the parenting
            ctx = self.tracer.get_call_context()
            eq_(0, len(ctx._trace))
            eq_('coroutine', ctx._current_span.name)
            return True

        span = self.tracer.trace('coroutine')
        future = helpers.run_in_executor(self.loop, None, future_work, tracer=self.tracer)
        # we close the Context
        span.finish()
        result = yield from future
        ok_(result)
