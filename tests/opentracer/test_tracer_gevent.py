import gevent
import pytest
import unittest
from nose.tools import eq_
from opentracing.scope_managers.gevent import GeventScopeManager

import ddtrace
from ddtrace.contrib.gevent import patch, unpatch
from ddtrace.opentracer.utils import get_context_provider_for_scope_manager

from tests.opentracer.test_tracer import get_dummy_ot_tracer


def get_dummy_gevent_tracer():
    from ddtrace.contrib.gevent import context_provider

    return get_dummy_ot_tracer("gevent", {}, GeventScopeManager(), context_provider)


@pytest.fixture()
def nop_tracer():
    return get_dummy_gevent_tracer()


class TestTracerGevent(unittest.TestCase):
    """Converted Gevent tests for the regular tracer.

    Ensures that greenlets are properly traced when using
    the opentracer.
    """
    def setUp(self):
        # use a dummy tracer
        self.tracer = get_dummy_gevent_tracer()

        # patch gevent
        patch()

    def tearDown(self):
        # unpatch gevent
        unpatch()

    def test_no_threading(self):
        with self.tracer.start_span('span') as span:
            span.set_tag('tag', 'value')

        assert span._finished

    def test_greenlets(self):
        def f():
            with self.tracer.start_span('f') as span:
                gevent.sleep(0.04)
                span.set_tag('f', 'yes')

        def g():
            with self.tracer.start_span('g') as span:
                gevent.sleep(0.03)
                span.set_tag('g', 'yes')

        with self.tracer.start_span('root'):
            gevent.joinall([
                gevent.spawn(f),
                gevent.spawn(g),
            ])

        traces = self.tracer._dd_tracer.writer.pop_traces()
        assert len(traces) == 3

    def test_trace_greenlet(self):
        # a greenlet can be traced using the trace API
        def greenlet():
            with self.tracer.start_span('greenlet'):
                pass

        gevent.spawn(greenlet).join()
        traces = self.tracer._dd_tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        eq_('greenlet', traces[0][0].name)

    def test_trace_later_greenlet(self):
        # a greenlet can be traced using the trace API
        def greenlet():
            with self.tracer.start_span('greenlet'):
                pass

        gevent.spawn_later(0.01, greenlet).join()
        traces = self.tracer._dd_tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        eq_('greenlet', traces[0][0].name)

    def test_trace_concurrent_calls(self):
        # create multiple futures so that we expect multiple
        # traces instead of a single one
        def greenlet():
            with self.tracer.start_span('greenlet'):
                gevent.sleep(0.01)

        jobs = [gevent.spawn(greenlet) for x in range(100)]
        gevent.joinall(jobs)

        traces = self.tracer._dd_tracer.writer.pop_traces()
        eq_(100, len(traces))
        eq_(1, len(traces[0]))
        eq_('greenlet', traces[0][0].name)

    def test_trace_concurrent_spawn_later_calls(self):
        # create multiple futures so that we expect multiple
        # traces instead of a single one, even if greenlets
        # are delayed
        def greenlet():
            with self.tracer.start_span('greenlet'):
                gevent.sleep(0.01)

        jobs = [gevent.spawn_later(0.01, greenlet) for x in range(100)]
        gevent.joinall(jobs)

        traces = self.tracer._dd_tracer.writer.pop_traces()
        eq_(100, len(traces))
        eq_(1, len(traces[0]))
        eq_('greenlet', traces[0][0].name)


class TestTracerGeventCompatibility(unittest.TestCase):
    """Ensure the opentracer works in tandem with the ddtracer and gevent."""

    def setUp(self):
        self.ot_tracer = get_dummy_gevent_tracer()
        self.dd_tracer = self.ot_tracer._dd_tracer
        self.writer = self.dd_tracer.writer

        # patch gevent
        patch()

    def tearDown(self):
        # unpatch gevent
        unpatch()

    def test_trace_spawn_multiple_greenlets_multiple_traces_ot_parent(self):
        """
        Copy of gevent test with the same name but testing with mixed usage of
        the opentracer and datadog tracers.

        Uses an opentracer span as the parent span.
        """
        # multiple greenlets must be part of the same trace
        def entrypoint():
            with self.ot_tracer.start_active_span('greenlet.main'):
                jobs = [gevent.spawn(green_1), gevent.spawn(green_2)]
                gevent.joinall(jobs)

        def green_1():
            with self.dd_tracer.trace('greenlet.worker') as span:
                span.set_tag('worker_id', '1')
                gevent.sleep(0.01)

        def green_2():
            with self.ot_tracer.start_span('greenlet.worker') as span:
                span.set_tag('worker_id', '2')
                gevent.sleep(0.01)

        gevent.spawn(entrypoint).join()
        traces = self.writer.pop_traces()
        eq_(3, len(traces))
        eq_(1, len(traces[0]))
        parent_span = traces[2][0]
        worker_1 = traces[0][0]
        worker_2 = traces[1][0]
        # check spans data and hierarchy
        eq_(parent_span.name, 'greenlet.main')
        eq_(worker_1.get_tag('worker_id'), '1')
        eq_(worker_1.name, 'greenlet.worker')
        eq_(worker_1.resource, 'greenlet.worker')
        eq_(worker_1.parent_id, parent_span.span_id)
        eq_(worker_2.get_tag('worker_id'), '2')
        eq_(worker_2.name, 'greenlet.worker')
        eq_(worker_2.resource, 'greenlet.worker')
        eq_(worker_2.parent_id, parent_span.span_id)

    def test_trace_spawn_multiple_greenlets_multiple_traces_dd_parent(self):
        """
        Copy of gevent test with the same name but testing with mixed usage of
        the opentracer and datadog tracers.

        Uses an opentracer span as the parent span.
        """
        # multiple greenlets must be part of the same trace
        def entrypoint():
            with self.dd_tracer.trace('greenlet.main'):
                jobs = [gevent.spawn(green_1), gevent.spawn(green_2)]
                gevent.joinall(jobs)

        def green_1():
            with self.ot_tracer.start_span('greenlet.worker') as span:
                span.set_tag('worker_id', '1')
                gevent.sleep(0.01)

        def green_2():
            with self.dd_tracer.trace('greenlet.worker') as span:
                span.set_tag('worker_id', '2')
                gevent.sleep(0.01)

        gevent.spawn(entrypoint).join()
        traces = self.writer.pop_traces()
        eq_(3, len(traces))
        eq_(1, len(traces[0]))
        parent_span = traces[2][0]
        worker_1 = traces[0][0]
        worker_2 = traces[1][0]
        # check spans data and hierarchy
        eq_(parent_span.name, 'greenlet.main')
        eq_(worker_1.get_tag('worker_id'), '1')
        eq_(worker_1.name, 'greenlet.worker')
        eq_(worker_1.resource, 'greenlet.worker')
        eq_(worker_1.parent_id, parent_span.span_id)
        eq_(worker_2.get_tag('worker_id'), '2')
        eq_(worker_2.name, 'greenlet.worker')
        eq_(worker_2.resource, 'greenlet.worker')
        eq_(worker_2.parent_id, parent_span.span_id)


class TestUtilsGevent(object):
    """Test the util routines of the opentracer with gevent specific
    configuration.
    """
    def test_get_context_provider_for_scope_manager_asyncio(self):
        scope_manager = GeventScopeManager()
        ctx_prov = get_context_provider_for_scope_manager(scope_manager)
        assert isinstance(ctx_prov, ddtrace.contrib.gevent.provider.GeventContextProvider)

    def test_tracer_context_provider_config(self):
        tracer = ddtrace.opentracer.Tracer("mysvc", scope_manager=GeventScopeManager())
        assert isinstance(tracer._dd_tracer.context_provider,
                          ddtrace.contrib.gevent.provider.GeventContextProvider)
