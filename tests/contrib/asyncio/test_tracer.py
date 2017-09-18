import asyncio

from asyncio import BaseEventLoop

from ddtrace.context import Context
from ddtrace.provider import DefaultContextProvider
from ddtrace.contrib.asyncio.patch import patch, unpatch
from ddtrace.contrib.asyncio.helpers import set_call_context

from nose.tools import eq_, ok_
from .utils import AsyncioTestCase, mark_asyncio


_orig_create_task = BaseEventLoop.create_task


class TestAsyncioTracer(AsyncioTestCase):
    """Ensure that the tracer works with asynchronous executions within
    the same ``IOLoop``.
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
        eq_(self.tracer.get_call_context(), self.tracer.get_call_context())

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
        # if multiple coroutines have nested tracing, they must belong
        # to the same trace
        @asyncio.coroutine
        def coro():
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
        # the parenting is correct
        eq_(traces[0][0], traces[0][1]._parent)
        eq_(traces[0][0].trace_id, traces[0][1].trace_id)

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

    @mark_asyncio
    def test_exception(self):
        @asyncio.coroutine
        def f1():
            with self.tracer.trace('f1'):
                raise Exception('f1 error')

        with self.assertRaises(Exception):
            yield from f1()
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        spans = traces[0]
        eq_(1, len(spans))
        span = spans[0]
        eq_(1, span.error)
        eq_('f1 error', span.get_tag('error.msg'))
        ok_('Exception: f1 error' in span.get_tag('error.stack'))

    @mark_asyncio
    def test_nested_exceptions(self):
        @asyncio.coroutine
        def f1():
            with self.tracer.trace('f1'):
                raise Exception('f1 error')

        @asyncio.coroutine
        def f2():
            with self.tracer.trace('f2'):
                yield from f1()

        with self.assertRaises(Exception):
            yield from f2()

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        spans = traces[0]
        eq_(2, len(spans))
        span = spans[0]
        eq_('f2', span.name)
        eq_(1, span.error) # f2 did not catch the exception
        eq_('f1 error', span.get_tag('error.msg'))
        ok_('Exception: f1 error' in span.get_tag('error.stack'))
        span = spans[1]
        eq_('f1', span.name)
        eq_(1, span.error)
        eq_('f1 error', span.get_tag('error.msg'))
        ok_('Exception: f1 error' in span.get_tag('error.stack'))

    @mark_asyncio
    def test_handled_nested_exceptions(self):
        @asyncio.coroutine
        def f1():
            with self.tracer.trace('f1'):
                raise Exception('f1 error')

        @asyncio.coroutine
        def f2():
            with self.tracer.trace('f2'):
                try:
                    yield from f1()
                except Exception:
                    pass

        yield from f2()

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        spans = traces[0]
        eq_(2, len(spans))
        span = spans[0]
        eq_('f2', span.name)
        eq_(0, span.error) # f2 caught the exception
        span = spans[1]
        eq_('f1', span.name)
        eq_(1, span.error)
        eq_('f1 error', span.get_tag('error.msg'))
        ok_('Exception: f1 error' in span.get_tag('error.stack'))

    @mark_asyncio
    def test_trace_multiple_calls(self):
        # create multiple futures so that we expect multiple
        # traces instead of a single one (helper not used)
        @asyncio.coroutine
        def coro():
            # another traced coroutine
            with self.tracer.trace('coroutine'):
                yield from asyncio.sleep(0.01)

        futures = [asyncio.ensure_future(coro()) for x in range(10)]
        for future in futures:
            yield from future

        traces = self.tracer.writer.pop_traces()
        eq_(10, len(traces))
        eq_(1, len(traces[0]))
        eq_('coroutine', traces[0][0].name)

    @mark_asyncio
    def test_wrapped_coroutine(self):
        @self.tracer.wrap('f1')
        @asyncio.coroutine
        def f1():
            yield from asyncio.sleep(0.25)

        yield from f1()

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        spans = traces[0]
        eq_(1, len(spans))
        span = spans[0]
        ok_(span.duration > 0.25, msg='span.duration={}'.format(span.duration))


class TestAsyncioPropagation(AsyncioTestCase):
    """Ensure that asyncio context propagation works between different tasks"""
    def setUp(self):
        # patch asyncio event loop
        super(TestAsyncioPropagation, self).setUp()
        patch()

    def tearDown(self):
        # unpatch asyncio event loop
        super(TestAsyncioPropagation, self).tearDown()
        unpatch()

    @mark_asyncio
    def test_tasks_chaining(self):
        # ensures that the context is propagated between different tasks
        @self.tracer.wrap('spawn_task')
        @asyncio.coroutine
        def coro_2():
            yield from asyncio.sleep(0.01)

        @self.tracer.wrap('main_task')
        @asyncio.coroutine
        def coro_1():
            yield from asyncio.ensure_future(coro_2())

        yield from coro_1()

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 2)
        eq_(len(traces[0]), 1)
        eq_(len(traces[1]), 1)
        spawn_task = traces[0][0]
        main_task = traces[1][0]
        # check if the context has been correctly propagated
        eq_(spawn_task.trace_id, main_task.trace_id)
        eq_(spawn_task.parent_id, main_task.span_id)

    @mark_asyncio
    def test_concurrent_chaining(self):
        # ensures that the context is correctly propagated when
        # concurrent tasks are created from a common tracing block
        @self.tracer.wrap('f1')
        @asyncio.coroutine
        def f1():
            yield from asyncio.sleep(0.01)

        @self.tracer.wrap('f2')
        @asyncio.coroutine
        def f2():
            yield from asyncio.sleep(0.01)

        with self.tracer.trace('main_task'):
            yield from asyncio.gather(f1(), f2())

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 3)
        eq_(len(traces[0]), 1)
        eq_(len(traces[1]), 1)
        eq_(len(traces[2]), 1)
        child_1 = traces[0][0]
        child_2 = traces[1][0]
        main_task = traces[2][0]
        # check if the context has been correctly propagated
        eq_(child_1.trace_id, main_task.trace_id)
        eq_(child_1.parent_id, main_task.span_id)
        eq_(child_2.trace_id, main_task.trace_id)
        eq_(child_2.parent_id, main_task.span_id)

    @mark_asyncio
    def test_propagation_with_set_call_context(self):
        # ensures that if a new Context is attached to the current
        # running Task via helpers, a previous trace is resumed
        task = asyncio.Task.current_task()
        ctx = Context(trace_id=100, span_id=101)
        set_call_context(task, ctx)

        with self.tracer.trace('async_task'):
            yield from asyncio.sleep(0.01)

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        eq_(span.trace_id, 100)
        eq_(span.parent_id, 101)

    @mark_asyncio
    def test_propagation_with_new_context(self):
        # ensures that if a new Context is activated, a trace
        # with the Context arguments is created
        task = asyncio.Task.current_task()
        ctx = Context(trace_id=100, span_id=101)
        self.tracer.context_provider.activate(ctx)

        with self.tracer.trace('async_task'):
            yield from asyncio.sleep(0.01)

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        eq_(span.trace_id, 100)
        eq_(span.parent_id, 101)

    @mark_asyncio
    def test_event_loop_unpatch(self):
        # ensures that the event loop can be unpatched
        unpatch()
        ok_(isinstance(self.tracer._context_provider, DefaultContextProvider))
        ok_(BaseEventLoop.create_task == _orig_create_task)

    def test_event_loop_double_patch(self):
        # ensures that double patching will not double instrument
        # the event loop
        patch()
        self.test_tasks_chaining()
