import asyncio
from asyncio import BaseEventLoop

from ddtrace.context import Context
from ddtrace.contrib.asyncio.helpers import set_call_context
from ddtrace.contrib.asyncio.patch import patch, unpatch
from ddtrace.contrib.asyncio import context_provider
from ddtrace.provider import DefaultContextProvider

from nose.tools import eq_, ok_

from .utils import AsyncioTestCase, mark_asyncio

_orig_create_task = BaseEventLoop.create_task


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

    @mark_asyncio
    def test_patch_chain(self):
        patch(self.tracer)

        assert self.tracer._context_provider is context_provider

        with self.tracer.trace('foo'):
            @self.tracer.wrap('f1')
            @asyncio.coroutine
            def f1():
                yield from asyncio.sleep(0.1)

            @self.tracer.wrap('f2')
            @asyncio.coroutine
            def f2():
                yield from asyncio.ensure_future(f1())

            yield from asyncio.ensure_future(f2())

        traces = list(reversed(self.tracer.writer.pop_traces()))
        assert len(traces) == 3
        root_span = traces[0][0]
        last_span_id = None
        for trace in traces:
            assert len(trace) == 1
            span = trace[0]
            assert span.trace_id == root_span.trace_id
            assert span.parent_id == last_span_id
            last_span_id = span.span_id

    @mark_asyncio
    def test_patch_parallel(self):
        patch(self.tracer)

        assert self.tracer._context_provider is context_provider

        with self.tracer.trace('foo'):
            @self.tracer.wrap('f1')
            @asyncio.coroutine
            def f1():
                yield from asyncio.sleep(0.1)

            @self.tracer.wrap('f2')
            @asyncio.coroutine
            def f2():
                yield from asyncio.sleep(0.1)

            yield from asyncio.gather(f1(), f2())

        traces = self.tracer.writer.pop_traces()
        assert len(traces) == 3
        root_span = traces[2][0]
        for trace in traces[:2]:
            assert len(trace) == 1
            span = trace[0]
            assert span.trace_id == root_span.trace_id
            assert span.parent_id == root_span.span_id

    @mark_asyncio
    def test_distributed(self):
        patch(self.tracer)

        task = asyncio.Task.current_task()
        ctx = Context(trace_id=100, span_id=101)
        set_call_context(task, ctx)

        with self.tracer.trace('foo'):
            pass

        traces = self.tracer.writer.pop_traces()
        assert len(traces) == 1
        trace = traces[0]
        assert len(trace) == 1
        span = trace[0]

        assert span.trace_id == ctx._parent_trace_id
        assert span.parent_id == ctx._parent_span_id

    @mark_asyncio
    def test_unpatch(self):
        patch(self.tracer)
        unpatch(self.tracer)

        assert isinstance(self.tracer._context_provider, DefaultContextProvider)
        assert BaseEventLoop.create_task == _orig_create_task

    def test_double_patch(self):
        patch(self.tracer)
        self.test_patch_chain()
