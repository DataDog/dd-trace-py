from nose.tools import eq_, ok_

from ddtrace.context import Context
from ddtrace.contrib.tornado import TracerStackContext

from .utils import TornadoTestCase
from .web.compat import sleep


class TestStackContext(TornadoTestCase):
    def test_without_stack_context(self):
        # without a TracerStackContext, propagation is not available
        ctx = self.tracer.context_provider.active()
        ok_(ctx is None)

    def test_stack_context(self):
        # a TracerStackContext should automatically propagate a tracing context
        with TracerStackContext():
            ctx = self.tracer.context_provider.active()

        ok_(ctx is not None)

    def test_propagation_with_new_context(self):
        # inside a TracerStackContext it should be possible to set
        # a new Context for distributed tracing
        with TracerStackContext():
            ctx = Context(trace_id=100, span_id=101)
            self.tracer.context_provider.activate(ctx)
            with self.tracer.trace('tornado'):
                sleep(0.01)

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        eq_(traces[0][0].trace_id, 100)
        eq_(traces[0][0].parent_id, 101)

    def test_propagation_without_stack_context(self):
        # a Context is discarded if not set inside a TracerStackContext
        ctx = Context(trace_id=100, span_id=101)
        self.tracer.context_provider.activate(ctx)
        with self.tracer.trace('tornado'):
            sleep(0.01)

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        ok_(traces[0][0].trace_id != 100)
        ok_(traces[0][0].parent_id != 101)
