import asyncio
from nose.tools import eq_, ok_

from opentracing.scope_managers import AsyncioScopeManager
from tests.opentracer.test_tracer import get_dummy_ot_tracer
from tests.contrib.asyncio.utils import AsyncioTestCase, mark_asyncio


def get_dummy_asyncio_tracer():
    return get_dummy_ot_tracer('asyncio_svc', {}, AsyncioScopeManager())


class TestTracerAsyncio(AsyncioTestCase):

    def setUp(self):
        super(TestTracerAsyncio, self).setUp()
        # use the dummy asyncio ot tracer
        self.tracer = get_dummy_asyncio_tracer()

    @mark_asyncio
    def test_trace_coroutine(self):
        # it should use the task context when invoked in a coroutine
        with self.tracer.start_span('coroutine'):
            pass

        traces = self.tracer._dd_tracer.writer.pop_traces()

        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        eq_('coroutine', traces[0][0].name)

    @mark_asyncio
    def test_trace_multiple_coroutines(self):
        # if multiple coroutines have nested tracing, they must belong
        # to the same trace
        @asyncio.coroutine
        def coro():
            # another traced coroutine
            with self.tracer.start_active_span('coroutine_2'):
                return 42

        with self.tracer.start_active_span('coroutine_1'):
            value = yield from coro()

        # the coroutine has been called correctly
        eq_(42, value)
        # a single trace has been properly reported
        traces = self.tracer._dd_tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(2, len(traces[0]))
        eq_('coroutine_1', traces[0][0].name)
        eq_('coroutine_2', traces[0][1].name)
        # the parenting is correct
        eq_(traces[0][0], traces[0][1]._parent)
        eq_(traces[0][0].trace_id, traces[0][1].trace_id)

    @mark_asyncio
    def test_exception(self):
        @asyncio.coroutine
        def f1():
            with self.tracer.start_span('f1'):
                raise Exception('f1 error')

        with self.assertRaises(Exception):
            yield from f1()

        traces = self.tracer._dd_tracer.writer.pop_traces()
        eq_(1, len(traces))
        spans = traces[0]
        eq_(1, len(spans))
        span = spans[0]
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
            with self.tracer.start_span('coroutine'):
                yield from asyncio.sleep(0.01)

        futures = [asyncio.ensure_future(coro()) for x in range(10)]
        for future in futures:
            yield from future

        traces = self.tracer._dd_tracer.writer.pop_traces()
        eq_(10, len(traces))
        eq_(1, len(traces[0]))
        eq_('coroutine', traces[0][0].name)

