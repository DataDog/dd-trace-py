import gevent
import ddtrace

from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.context import Context
from ddtrace.contrib.gevent import patch, unpatch
from ddtrace.ext.priority import USER_KEEP

from unittest import TestCase
from nose.tools import eq_, ok_
from opentracing.scope_managers.gevent import GeventScopeManager
from tests.opentracer.utils import init_tracer
from tests.test_tracer import get_dummy_tracer

from .utils import silence_errors


class TestGeventTracer(TestCase):
    """
    Ensures that greenlets are properly traced when using
    the default Tracer.
    """
    def setUp(self):
        # use a dummy tracer
        self.tracer = get_dummy_tracer()
        self._original_tracer = ddtrace.tracer
        ddtrace.tracer = self.tracer
        # trace gevent
        patch()

    def tearDown(self):
        # clean the active Context
        self.tracer.context_provider.activate(None)
        # restore the original tracer
        ddtrace.tracer = self._original_tracer
        # untrace gevent
        unpatch()

    def test_main_greenlet(self):
        # the main greenlet must not be affected by the tracer
        main_greenlet = gevent.getcurrent()
        ctx = getattr(main_greenlet, '__datadog_context', None)
        ok_(ctx is None)

    def test_main_greenlet_context(self):
        # the main greenlet must have a ``Context`` if called
        ctx_tracer = self.tracer.get_call_context()
        main_greenlet = gevent.getcurrent()
        ctx_greenlet = getattr(main_greenlet, '__datadog_context', None)
        ok_(ctx_tracer is ctx_greenlet)
        eq_(len(ctx_tracer._trace), 0)

    def test_get_call_context(self):
        # it should return the context attached to the provider
        def greenlet():
            return self.tracer.get_call_context()

        g = gevent.spawn(greenlet)
        g.join()
        ctx = g.value
        stored_ctx = getattr(g, '__datadog_context', None)
        ok_(stored_ctx is not None)
        eq_(ctx, stored_ctx)

    def test_get_call_context_twice(self):
        # it should return the same Context if called twice
        def greenlet():
            eq_(self.tracer.get_call_context(), self.tracer.get_call_context())
            return True

        g = gevent.spawn(greenlet)
        g.join()
        ok_(g.value)

    def test_spawn_greenlet_no_context(self):
        # a greenlet will not have a context if the tracer is not used
        def greenlet():
            gevent.sleep(0.01)

        g = gevent.spawn(greenlet)
        g.join()
        ctx = getattr(g, '__datadog_context', None)
        ok_(ctx is None)

    def test_spawn_greenlet(self):
        # a greenlet will have a context if the tracer is used
        def greenlet():
            self.tracer.get_call_context()

        g = gevent.spawn(greenlet)
        g.join()
        ctx = getattr(g, '__datadog_context', None)
        ok_(ctx is not None)
        eq_(0, len(ctx._trace))

    def test_spawn_later_greenlet(self):
        # a greenlet will have a context if the tracer is used even
        # if it's spawned later
        def greenlet():
            self.tracer.get_call_context()

        g = gevent.spawn_later(0.01, greenlet)
        g.join()
        ctx = getattr(g, '__datadog_context', None)
        ok_(ctx is not None)
        eq_(0, len(ctx._trace))

    def test_trace_greenlet(self):
        # a greenlet can be traced using the trace API
        def greenlet():
            with self.tracer.trace('greenlet') as span:
                span.resource = 'base'

        gevent.spawn(greenlet).join()
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        eq_('greenlet', traces[0][0].name)
        eq_('base', traces[0][0].resource)

    def test_trace_later_greenlet(self):
        # a greenlet can be traced using the trace API
        def greenlet():
            with self.tracer.trace('greenlet') as span:
                span.resource = 'base'

        gevent.spawn_later(0.01, greenlet).join()
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        eq_('greenlet', traces[0][0].name)
        eq_('base', traces[0][0].resource)

    def test_trace_sampling_priority_spawn_multiple_greenlets_multiple_traces(self):
        # multiple greenlets must be part of the same trace
        def entrypoint():
            with self.tracer.trace('greenlet.main') as span:
                span.context.sampling_priority = USER_KEEP
                span.resource = 'base'
                jobs = [gevent.spawn(green_1), gevent.spawn(green_2)]
                gevent.joinall(jobs)

        def green_1():
            with self.tracer.trace('greenlet.worker') as span:
                span.set_tag('worker_id', '1')
                gevent.sleep(0.01)

        def green_2():
            with self.tracer.trace('greenlet.worker') as span:
                span.set_tag('worker_id', '2')
                gevent.sleep(0.01)

        gevent.spawn(entrypoint).join()
        traces = self.tracer.writer.pop_traces()
        eq_(3, len(traces))
        eq_(1, len(traces[0]))
        parent_span = traces[2][0]
        worker_1 = traces[0][0]
        worker_2 = traces[1][0]
        # check sampling priority
        eq_(parent_span.get_metric(SAMPLING_PRIORITY_KEY), USER_KEEP)
        eq_(worker_1.get_metric(SAMPLING_PRIORITY_KEY), USER_KEEP)
        eq_(worker_2.get_metric(SAMPLING_PRIORITY_KEY), USER_KEEP)

    def test_trace_spawn_multiple_greenlets_multiple_traces(self):
        # multiple greenlets must be part of the same trace
        def entrypoint():
            with self.tracer.trace('greenlet.main') as span:
                span.resource = 'base'
                jobs = [gevent.spawn(green_1), gevent.spawn(green_2)]
                gevent.joinall(jobs)

        def green_1():
            with self.tracer.trace('greenlet.worker') as span:
                span.set_tag('worker_id', '1')
                gevent.sleep(0.01)

        def green_2():
            with self.tracer.trace('greenlet.worker') as span:
                span.set_tag('worker_id', '2')
                gevent.sleep(0.01)

        gevent.spawn(entrypoint).join()
        traces = self.tracer.writer.pop_traces()
        eq_(3, len(traces))
        eq_(1, len(traces[0]))
        parent_span = traces[2][0]
        worker_1 = traces[0][0]
        worker_2 = traces[1][0]
        # check spans data and hierarchy
        eq_(parent_span.name, 'greenlet.main')
        eq_(parent_span.resource, 'base')
        eq_(worker_1.get_tag('worker_id'), '1')
        eq_(worker_1.name, 'greenlet.worker')
        eq_(worker_1.resource, 'greenlet.worker')
        eq_(worker_1.parent_id, parent_span.span_id)
        eq_(worker_2.get_tag('worker_id'), '2')
        eq_(worker_2.name, 'greenlet.worker')
        eq_(worker_2.resource, 'greenlet.worker')
        eq_(worker_2.parent_id, parent_span.span_id)

    def test_trace_spawn_later_multiple_greenlets_multiple_traces(self):
        # multiple greenlets must be part of the same trace
        def entrypoint():
            with self.tracer.trace('greenlet.main') as span:
                span.resource = 'base'
                jobs = [gevent.spawn_later(0.01, green_1), gevent.spawn_later(0.01, green_2)]
                gevent.joinall(jobs)

        def green_1():
            with self.tracer.trace('greenlet.worker') as span:
                span.set_tag('worker_id', '1')
                gevent.sleep(0.01)

        def green_2():
            with self.tracer.trace('greenlet.worker') as span:
                span.set_tag('worker_id', '2')
                gevent.sleep(0.01)

        gevent.spawn(entrypoint).join()
        traces = self.tracer.writer.pop_traces()
        eq_(3, len(traces))
        eq_(1, len(traces[0]))
        parent_span = traces[2][0]
        worker_1 = traces[0][0]
        worker_2 = traces[1][0]
        # check spans data and hierarchy
        eq_(parent_span.name, 'greenlet.main')
        eq_(parent_span.resource, 'base')
        eq_(worker_1.get_tag('worker_id'), '1')
        eq_(worker_1.name, 'greenlet.worker')
        eq_(worker_1.resource, 'greenlet.worker')
        eq_(worker_1.parent_id, parent_span.span_id)
        eq_(worker_2.get_tag('worker_id'), '2')
        eq_(worker_2.name, 'greenlet.worker')
        eq_(worker_2.resource, 'greenlet.worker')
        eq_(worker_2.parent_id, parent_span.span_id)

    def test_trace_concurrent_calls(self):
        # create multiple futures so that we expect multiple
        # traces instead of a single one
        def greenlet():
            with self.tracer.trace('greenlet'):
                gevent.sleep(0.01)

        jobs = [gevent.spawn(greenlet) for x in range(100)]
        gevent.joinall(jobs)

        traces = self.tracer.writer.pop_traces()
        eq_(100, len(traces))
        eq_(1, len(traces[0]))
        eq_('greenlet', traces[0][0].name)

    def test_propagation_with_new_context(self):
        # create multiple futures so that we expect multiple
        # traces instead of a single one
        ctx = Context(trace_id=100, span_id=101)
        self.tracer.context_provider.activate(ctx)

        def greenlet():
            with self.tracer.trace('greenlet') as span:
                gevent.sleep(0.01)

        jobs = [gevent.spawn(greenlet) for x in range(1)]
        gevent.joinall(jobs)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        eq_(traces[0][0].trace_id, 100)
        eq_(traces[0][0].parent_id, 101)

    def test_trace_concurrent_spawn_later_calls(self):
        # create multiple futures so that we expect multiple
        # traces instead of a single one, even if greenlets
        # are delayed
        def greenlet():
            with self.tracer.trace('greenlet'):
                gevent.sleep(0.01)

        jobs = [gevent.spawn_later(0.01, greenlet) for x in range(100)]
        gevent.joinall(jobs)

        traces = self.tracer.writer.pop_traces()
        eq_(100, len(traces))
        eq_(1, len(traces[0]))
        eq_('greenlet', traces[0][0].name)

    @silence_errors
    def test_exception(self):
        # it should catch the exception like usual
        def greenlet():
            with self.tracer.trace('greenlet'):
                raise Exception('Custom exception')

        g = gevent.spawn(greenlet)
        g.join()
        ok_(isinstance(g.exception, Exception))

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        eq_(1, span.error)
        eq_('Custom exception', span.get_tag('error.msg'))
        ok_('Traceback (most recent call last)' in span.get_tag('error.stack'))

    def _assert_spawn_multiple_greenlets(self, spans):
        """A helper to assert the parenting of a trace when greenlets are
        spawned within another greenlet.

        This is meant to help maintain compatibility between the Datadog and
        OpenTracing tracer implementations.

        Note that for gevent there is differing behaviour between the context
        management so the traces are not identical in form. However, the
        parenting of the spans must remain the same.
        """
        eq_(len(spans), 3)

        parent = None
        worker_1 = None
        worker_2 = None
        # get the spans since they can be in any order
        for span in spans:
            if span.name == 'greenlet.main':
                parent = span
            if span.name == 'greenlet.worker1':
                worker_1 = span
            if span.name == 'greenlet.worker2':
                worker_2 = span
        ok_(parent)
        ok_(worker_1)
        ok_(worker_2)

        # confirm the parenting
        eq_(worker_1.parent_id, parent.span_id)
        eq_(worker_2.parent_id, parent.span_id)

        # check spans data and hierarchy
        eq_(parent.name, 'greenlet.main')
        eq_(worker_1.get_tag('worker_id'), '1')
        eq_(worker_1.name, 'greenlet.worker1')
        eq_(worker_1.resource, 'greenlet.worker1')
        eq_(worker_2.get_tag('worker_id'), '2')
        eq_(worker_2.name, 'greenlet.worker2')
        eq_(worker_2.resource, 'greenlet.worker2')

    def test_trace_spawn_multiple_greenlets_multiple_traces_dd(self):
        """Datadog version of the same test."""
        def entrypoint():
            with self.tracer.trace('greenlet.main') as span:
                span.resource = 'base'
                jobs = [gevent.spawn(green_1), gevent.spawn(green_2)]
                gevent.joinall(jobs)

        def green_1():
            with self.tracer.trace('greenlet.worker1') as span:
                span.set_tag('worker_id', '1')
                gevent.sleep(0.01)

        # note that replacing the `tracer.trace` call here with the
        # OpenTracing equivalent will cause the checks to fail
        def green_2():
            with self.tracer.trace('greenlet.worker2') as span:
                span.set_tag('worker_id', '2')
                gevent.sleep(0.01)

        gevent.spawn(entrypoint).join()
        spans = self.tracer.writer.pop()
        self._assert_spawn_multiple_greenlets(spans)

    def test_trace_spawn_multiple_greenlets_multiple_traces_ot(self):
        """OpenTracing version of the same test."""

        ot_tracer = init_tracer('my_svc', self.tracer, scope_manager=GeventScopeManager())

        def entrypoint():
            with ot_tracer.start_active_span('greenlet.main') as span:
                span.resource = 'base'
                jobs = [gevent.spawn(green_1), gevent.spawn(green_2)]
                gevent.joinall(jobs)

        def green_1():
            with self.tracer.trace('greenlet.worker1') as span:
                span.set_tag('worker_id', '1')
                gevent.sleep(0.01)

        # note that replacing the `tracer.trace` call here with the
        # OpenTracing equivalent will cause the checks to fail
        def green_2():
            with ot_tracer.start_active_span('greenlet.worker2') as scope:
                scope.span.set_tag('worker_id', '2')
                gevent.sleep(0.01)

        gevent.spawn(entrypoint).join()

        spans = self.tracer.writer.pop()
        self._assert_spawn_multiple_greenlets(spans)
