import dd_trace_api

from ddtrace.contrib.internal.dd_trace_api.patch import patch
from ddtrace.contrib.internal.dd_trace_api.patch import unpatch
from tests.utils import TracerTestCase


class DDTraceAPITestCase(TracerTestCase):
    def setUp(self):
        super(DDTraceAPITestCase, self).setUp()
        patch(tracer=self.tracer)

    def tearDown(self):
        super(DDTraceAPITestCase, self).tearDown()
        unpatch()

    def test_start_span(self):
        with dd_trace_api.tracer.Tracer().start_span("web.request") as span:
            span.finish()
        spans = self.pop_spans()
        assert len(spans) == 1
