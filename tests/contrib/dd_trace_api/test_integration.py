from typing import Any

import dd_trace_api

from ddtrace import Span as dd_span_class
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

    def _assert_span_stub(self, stub: Any):
        assert not isinstance(stub, dd_span_class), "Returned span object should be a stub"
        assert not hasattr(stub, "span_id"), "Returned span stub should not support read operations"

    def _assert_real_span(self):
        spans = self.pop_spans()
        assert len(spans) == 1
        generated_span = spans[0]
        assert isinstance(generated_span, dd_span_class), "Generated span is a real span"
        assert hasattr(generated_span, "span_id"), "Generated span should support read operations"

    def test_start_span(self):
        with dd_trace_api.tracer.Tracer().start_span("web.request") as span:
            self._assert_span_stub(span)
        self._assert_real_span()

    def test_trace(self):
        with dd_trace_api.tracer.Tracer().trace("web.request") as span:
            self._assert_span_stub(span)
        self._assert_real_span()
