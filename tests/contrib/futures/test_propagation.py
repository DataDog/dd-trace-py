import time
import concurrent

from unittest import TestCase
from nose.tools import eq_, ok_

from ddtrace.contrib.futures import patch, unpatch

from tests.opentracer.utils import init_tracer
from ...util import override_global_tracer
from ...test_tracer import get_dummy_tracer


class PropagationTestCase(TestCase):
    """Ensures the Context Propagation works between threads
    when the ``futures`` library is used, or when the
    ``concurrent`` module is available (Python 3 only)
    """
    def setUp(self):
        # instrument ``concurrent``
        patch()
        self.tracer = get_dummy_tracer()

    def tearDown(self):
        # remove instrumentation
        unpatch()

    def test_propagation(self):
        # it must propagate the tracing context if available

        def fn():
            # an active context must be available
            ok_(self.tracer.context_provider.active() is not None)
            with self.tracer.trace('executor.thread'):
                return 42

        with override_global_tracer(self.tracer):
            with self.tracer.trace('main.thread'):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    future = executor.submit(fn)
                    result = future.result()
                    # assert the right result
                    eq_(result, 42)

        # the trace must be completed
        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 2)
        main = traces[0][0]
        executor = traces[0][1]

        eq_(main.name, 'main.thread')
        eq_(executor.name, 'executor.thread')
        ok_(executor._parent is main)

    def test_propagation_with_params(self):
        # instrumentation must proxy arguments if available

        def fn(value, key=None):
            # an active context must be available
            ok_(self.tracer.context_provider.active() is not None)
            with self.tracer.trace('executor.thread'):
                return value, key

        with override_global_tracer(self.tracer):
            with self.tracer.trace('main.thread'):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    future = executor.submit(fn, 42, 'CheeseShop')
                    value, key = future.result()
                    # assert the right result
                    eq_(value, 42)
                    eq_(key, 'CheeseShop')

        # the trace must be completed
        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 2)
        main = traces[0][0]
        executor = traces[0][1]

        eq_(main.name, 'main.thread')
        eq_(executor.name, 'executor.thread')
        ok_(executor._parent is main)

    def test_disabled_instrumentation(self):
        # it must not propagate if the module is disabled
        unpatch()

        def fn():
            # an active context must be available
            ok_(self.tracer.context_provider.active() is not None)
            with self.tracer.trace('executor.thread'):
                return 42

        with override_global_tracer(self.tracer):
            with self.tracer.trace('main.thread'):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    future = executor.submit(fn)
                    result = future.result()
                    # assert the right result
                    eq_(result, 42)

        # we provide two different traces
        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 2)
        eq_(len(traces[0]), 1)
        eq_(len(traces[1]), 1)
        executor = traces[0][0]
        main = traces[1][0]

        eq_(main.name, 'main.thread')
        eq_(executor.name, 'executor.thread')
        ok_(main.parent_id is None)
        ok_(executor.parent_id is None)

    def test_double_instrumentation(self):
        # double instrumentation must not happen
        patch()

        def fn():
            with self.tracer.trace('executor.thread'):
                return 42

        with override_global_tracer(self.tracer):
            with self.tracer.trace('main.thread'):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    future = executor.submit(fn)
                    result = future.result()
                    # assert the right result
                    eq_(result, 42)

        # the trace must be completed
        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 2)

    def test_send_trace_when_finished(self):
        # it must send the trace only when all threads are finished

        def fn():
            with self.tracer.trace('executor.thread'):
                # wait before returning
                time.sleep(0.05)
                return 42

        with override_global_tracer(self.tracer):
            with self.tracer.trace('main.thread'):
                # don't wait for the execution
                executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
                future = executor.submit(fn)
                time.sleep(0.01)

        # assert the trace is not sent because the secondary thread
        # didn't finish the processing
        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 0)

        # then wait for the second thread and send the trace
        result = future.result()
        eq_(result, 42)
        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 2)

    def test_propagation_ot(self):
        """OpenTracing version of test_propagation."""
        # it must propagate the tracing context if available
        ot_tracer = init_tracer('my_svc', self.tracer)

        def fn():
            # an active context must be available
            ok_(self.tracer.context_provider.active() is not None)
            with self.tracer.trace('executor.thread'):
                return 42

        with override_global_tracer(self.tracer):
            with ot_tracer.start_active_span('main.thread'):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    future = executor.submit(fn)
                    result = future.result()
                    # assert the right result
                    eq_(result, 42)

        # the trace must be completed
        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 2)
        main = traces[0][0]
        executor = traces[0][1]

        eq_(main.name, 'main.thread')
        eq_(executor.name, 'executor.thread')
        ok_(executor._parent is main)
