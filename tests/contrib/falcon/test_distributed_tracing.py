from falcon import testing
from nose.tools import eq_, ok_
from tests.test_tracer import get_dummy_tracer

from .app import get_app


class DistributedTracingTestCase(testing.TestCase):
    """Executes tests using the manual instrumentation so a middleware
    is explicitly added.
    """

    def setUp(self):
        super(DistributedTracingTestCase, self).setUp()
        self._service = 'falcon'
        self.tracer = get_dummy_tracer()
        self.api = get_app(tracer=self.tracer)

    def test_distributred_tracing(self):
        headers = {
            'x-datadog-trace-id': '100',
            'x-datadog-parent-id': '42',
        }
        out = self.simulate_get('/200', headers=headers)
        eq_(out.status_code, 200)
        eq_(out.content.decode('utf-8'), 'Success')

        traces = self.tracer.writer.pop_traces()

        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)

        eq_(traces[0][0].parent_id, 42)
        eq_(traces[0][0].trace_id, 100)

    def test_distributred_tracing_disabled(self):
        self.tracer = get_dummy_tracer()
        self.api = get_app(tracer=self.tracer, distributed_tracing=False)
        headers = {
            'x-datadog-trace-id': '100',
            'x-datadog-parent-id': '42',
        }
        out = self.simulate_get('/200', headers=headers)
        eq_(out.status_code, 200)
        eq_(out.content.decode('utf-8'), 'Success')

        traces = self.tracer.writer.pop_traces()

        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)

        ok_(traces[0][0].parent_id is not 42)
        ok_(traces[0][0].trace_id is not 100)
