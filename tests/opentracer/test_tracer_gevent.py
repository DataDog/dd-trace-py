import pytest
import gevent
import opentracing

from opentracing.ext.scope_manager.gevent import GeventScopeManager
from tests.opentracer.test_tracer import get_dummy_ot_tracer


def get_dummy_gevent_tracer():
    return get_dummy_ot_tracer('gevent', {}, GeventScopeManager())


@pytest.fixture()
def nop_tracer():
    return get_dummy_gevent_tracer()


class TestTracerGevent(object):
    def test_no_threading(self, nop_tracer):
        with nop_tracer.start_span('span') as span:
            span.set_tag('tag', 'value')

        assert span.finished

    def test_greenlets(self, nop_tracer):
        def f():
            with nop_tracer.start_span('f') as span:
                gevent.sleep(0.04)
                span.set_tag('f', 'yes')

        def g():
            with nop_tracer.start_span('g') as span:
                gevent.sleep(0.03)
                span.set_tag('g', 'yes')

        with nop_tracer.start_span('root'):
            gevent.joinall([
                gevent.spawn(f),
                gevent.spawn(g),
            ])

        traces = nop_tracer._dd_tracer.writer.pop_traces()
        assert len(traces) == 3


from unittest import TestCase
from nose.tools import eq_, ok_

class TestTracerGeventCompat(TestCase):
    """Converted Gevent tests for the regular tracer.

    Ensures that greenlets are properly traced when using
    the default Tracer.
    """
    def setUp(self):
        # use a dummy tracer
        self.tracer = get_dummy_gevent_tracer()

    def tearDown(self):
        pass

    def test_trace_greenlet(self):
        # a greenlet can be traced using the trace API
        def greenlet():
            with self.tracer.start_span('greenlet') as span:
                pass

        gevent.spawn(greenlet).join()
        traces = self.tracer._dd_tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        eq_('greenlet', traces[0][0].name)

    def test_trace_later_greenlet(self):
        # a greenlet can be traced using the trace API
        def greenlet():
            with self.tracer.start_span('greenlet') as span:
                pass

        gevent.spawn_later(0.01, greenlet).join()
        traces = self.tracer._dd_tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        eq_('greenlet', traces[0][0].name)

    def test_trace_spawn_multiple_greenlets_multiple_traces(self):
        """TODO: this test's behaviour might be different for opentracing
        than for regular tracing. It is undefined so far as to how/if opentracing
        will patch threading libraries to handle scope management.
        """
        # multiple greenlets must be part of the same trace
        def entrypoint():
            with self.tracer.start_span('greenlet.main') as span:
                jobs = [gevent.spawn(green_1), gevent.spawn(green_2)]
                gevent.joinall(jobs)

        def green_1():
            with self.tracer.start_span('greenlet.worker') as span:
                span.set_tag('worker_id', '1')
                gevent.sleep(0.01)

        def green_2():
            with self.tracer.start_span('greenlet.worker') as span:
                span.set_tag('worker_id', '2')
                gevent.sleep(0.01)

        gevent.spawn(entrypoint).join()
        traces = self.tracer._dd_tracer.writer.pop_traces()
        eq_(3, len(traces))
        eq_(1, len(traces[0]))
        parent_span = traces[2][0]
        worker_1 = traces[0][0]
        worker_2 = traces[1][0]
        # check spans data and hierarchy
        eq_(parent_span.name, 'greenlet.main')
        eq_(worker_1.get_tag('worker_id'), '1')
        eq_(worker_1.name, 'greenlet.worker')
        # TODO:
        # eq_(worker_1.parent_id, parent_span.span_id)
        eq_(worker_2.get_tag('worker_id'), '2')
        eq_(worker_2.name, 'greenlet.worker')
        # TODO:
        # eq_(worker_2.parent_id, parent_span.span_id)

    def test_trace_spawn_later_multiple_greenlets_multiple_traces(self):
        """TODO: see previous test's TODO."""
        # multiple greenlets must be part of the same trace
        def entrypoint():
            with self.tracer.start_span('greenlet.main') as span:
                jobs = [gevent.spawn_later(0.01, green_1), gevent.spawn_later(0.01, green_2)]
                gevent.joinall(jobs)

        def green_1():
            with self.tracer.start_span('greenlet.worker') as span:
                span.set_tag('worker_id', '1')
                gevent.sleep(0.01)

        def green_2():
            with self.tracer.start_span('greenlet.worker') as span:
                span.set_tag('worker_id', '2')
                gevent.sleep(0.01)

        gevent.spawn(entrypoint).join()
        traces = self.tracer._dd_tracer.writer.pop_traces()
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
        # TODO:
        # eq_(worker_1.parent_id, parent_span.span_id)
        eq_(worker_2.get_tag('worker_id'), '2')
        eq_(worker_2.name, 'greenlet.worker')
        eq_(worker_2.resource, 'greenlet.worker')
        # TODO:
        # eq_(worker_2.parent_id, parent_span.span_id)

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
