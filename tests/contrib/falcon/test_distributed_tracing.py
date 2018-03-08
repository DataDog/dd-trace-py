from ddtrace.propagation.http import HTTPPropagator
from ddtrace.ext import errors as errx, http as httpx, AppTypes
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
        self.api = get_app(tracer=self.tracer, distributed_tracing=True)

    def test_has_parent_span(self):
        headers = {}
        root_tracer = get_dummy_tracer()
        root_tracer.set_service_info('root', 'root', AppTypes.web)
        with root_tracer.trace('root') as root:
            propagator = HTTPPropagator()
            propagator.inject(root.context, headers)
        out = self.simulate_get('/200', headers=headers)
        eq_(out.status_code, 200)
        eq_(out.content.decode('utf-8'), 'Success')

        traces = self.tracer.writer.pop_traces()
        
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)

        eq_(traces[0][0].parent_id, root.span_id)
        eq_(traces[0][0].trace_id, root.trace_id)

