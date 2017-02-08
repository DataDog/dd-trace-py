import asyncio

from nose.tools import eq_, ok_

from ddtrace.context import Context
from .utils import AsyncioTestCase, mark_asyncio


class TestAsyncioTracer(AsyncioTestCase):
    """
    Ensure that the ``AsyncioTracer`` works for asynchronous execution
    within the same ``IOLoop``.
    """
    @mark_asyncio
    def test_get_call_context(self):
        # it should return the context attached to the current Task
        # or create a new one
        task = asyncio.Task.current_task()
        ctx = getattr(task, '__datadog_context', None)
        ok_(ctx is None)
        # get the context from the loop creates a new one that
        # is attached to the Task object
        ctx = self.tracer.get_call_context()
        eq_(ctx, getattr(task, '__datadog_context', None))

    @mark_asyncio
    def test_get_call_context_twice(self):
        # it should return the same Context if called twice
        task = asyncio.Task.current_task()
        eq_(self.tracer.get_call_context(), self.tracer.get_call_context())

    @mark_asyncio
    def test_set_call_context(self):
        # a different Context is set for the current logical execution
        task = asyncio.Task.current_task()
        ctx = Context()
        self.tracer.set_call_context(task, ctx)
        eq_(ctx, self.tracer.get_call_context())

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
    def test_trace_multiple_coroutines(self):
        async def coro():
            # another traced coroutine
            with self.tracer.trace('coroutine_2'):
                return 42

        with self.tracer.trace('coroutine_1'):
            value = yield from coro()

        # the coroutine has been called correctly
        eq_(42, value)
        # a single trace has been properly reported
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(2, len(traces[0]))
        eq_('coroutine_1', traces[0][0].name)
        eq_('coroutine_2', traces[0][1].name)

    @mark_asyncio
    def test_event_loop_exception(self):
        # it should handle a loop exception
        asyncio.set_event_loop(None)
        ctx = self.tracer.get_call_context()
        ok_(ctx is not None)

    def test_context_task_none(self):
        # it should handle the case where a Task is not available
        # Note: the @mark_asyncio is missing to simulate an execution
        # without a Task
        task = asyncio.Task.current_task()
        # the task is not available
        ok_(task is None)
        # but a new Context is still created making the operation safe
        ctx = self.tracer.get_call_context()
        ok_(ctx is not None)
