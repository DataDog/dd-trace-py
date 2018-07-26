import asyncio
from nose.tools import eq_, ok_
from opentracing.scope_managers.asyncio import AsyncioScopeManager

import ddtrace
from ddtrace.opentracer.utils import get_context_provider_for_scope_manager

from tests.contrib.asyncio.utils import AsyncioTestCase, mark_asyncio
from tests.opentracer.test_tracer import get_dummy_ot_tracer
from tests.opentracer.utils import opentracer_init
from tests.test_tracer import get_dummy_tracer


def get_dummy_asyncio_tracer():
    from ddtrace.contrib.asyncio import context_provider
    return get_dummy_ot_tracer('asyncio_svc', {}, AsyncioScopeManager(),
                               context_provider)


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



class TestTracerAsyncioCompatibility(AsyncioTestCase):
    """Ensure the opentracer works in tandem with the ddtracer and asyncio."""

    def setUp(self):
        super(TestTracerAsyncioCompatibility, self).setUp()
        self.ot_tracer, self.dd_tracer = opentracer_init(set_global=False)
        self.writer = self.dd_tracer.writer

    @mark_asyncio
    def test_trace_multiple_coroutines_ot_dd(self):
        """
        Ensure we can trace from opentracer to ddtracer across asyncio
        context switches.
        """
        # if multiple coroutines have nested tracing, they must belong
        # to the same trace
        @asyncio.coroutine
        def coro():
            # another traced coroutine
            with self.dd_tracer.trace('coroutine_2'):
                return 42

        with self.ot_tracer.start_active_span('coroutine_1'):
            value = yield from coro()

        # the coroutine has been called correctly
        eq_(42, value)
        # a single trace has been properly reported
        traces = self.writer.pop_traces()
        eq_(1, len(traces))
        eq_(2, len(traces[0]))
        eq_('coroutine_1', traces[0][0].name)
        eq_('coroutine_2', traces[0][1].name)
        # the parenting is correct
        eq_(traces[0][0], traces[0][1]._parent)
        eq_(traces[0][0].trace_id, traces[0][1].trace_id)

    @mark_asyncio
    def test_trace_multiple_coroutines_dd_ot(self):
        """
        Ensure we can trace from ddtracer to opentracer across asyncio
        context switches.
        """
        # if multiple coroutines have nested tracing, they must belong
        # to the same trace
        @asyncio.coroutine
        def coro():
            # another traced coroutine
            with self.ot_tracer.start_span('coroutine_2'):
                return 42

        with self.dd_tracer.trace('coroutine_1'):
            value = yield from coro()

        # the coroutine has been called correctly
        eq_(42, value)
        # a single trace has been properly reported
        traces = self.writer.pop_traces()
        eq_(1, len(traces))
        eq_(2, len(traces[0]))
        eq_('coroutine_1', traces[0][0].name)
        eq_('coroutine_2', traces[0][1].name)
        # the parenting is correct
        eq_(traces[0][0], traces[0][1]._parent)
        eq_(traces[0][0].trace_id, traces[0][1].trace_id)


class TestUtilsAsyncio(object):
    """Test the util routines of the opentracer with asyncio specific
    configuration.
    """
    def test_get_context_provider_for_scope_manager_asyncio(self):
        scope_manager = AsyncioScopeManager()
        ctx_prov = get_context_provider_for_scope_manager(scope_manager)
        assert isinstance(ctx_prov, ddtrace.contrib.asyncio.provider.AsyncioContextProvider)

    def test_tracer_context_provider_config(self):
        tracer = ddtrace.opentracer.Tracer("mysvc", scope_manager=AsyncioScopeManager())
        assert isinstance(tracer._dd_tracer.context_provider, ddtrace.contrib.asyncio.provider.AsyncioContextProvider)
