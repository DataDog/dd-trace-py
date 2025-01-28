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
        self.pop_spans()
        super(DDTraceAPITestCase, self).tearDown()
        unpatch()

    def _assert_span_stub(self, stub: Any):
        assert not isinstance(stub, dd_span_class), "Returned span object should be a stub"

    def _assert_real_spans(self, count=1):
        spans = self.pop_spans()
        assert len(spans) == count
        generated_span = spans[0]
        assert isinstance(generated_span, dd_span_class), "Generated span is a real span"
        assert hasattr(generated_span, "span_id"), "Generated span should support read operations"

    def test_tracer_singleton(self):
        assert isinstance(dd_trace_api.tracer, dd_trace_api.Tracer), "Tracer stub should be exposed as a singleton"

    def test_start_span(self):
        with dd_trace_api.tracer.start_span("web.request") as span:
            self._assert_span_stub(span)
        self._assert_real_spans()

    def test_span_finish(self):
        span = dd_trace_api.tracer.start_span("web.request")
        self._assert_span_stub(span)
        span.finish()
        self._assert_real_spans()

    def test_span_finish_with_ancestors(self):
        span = dd_trace_api.tracer.start_span("web.request")
        child_span = dd_trace_api.tracer.start_span("web.request", child_of=span)
        child_span.finish_with_ancestors()
        self._assert_real_spans(2)

    def test_trace(self):
        with dd_trace_api.tracer.trace("web.request") as span:
            self._assert_span_stub(span)
        self._assert_real_spans()

    def test_current_span(self):
        with dd_trace_api.tracer.trace("web.request"):
            span = dd_trace_api.tracer.current_span()
            self._assert_span_stub(span)
        self._assert_real_spans()

    def test_current_root_span(self):
        with dd_trace_api.tracer.trace("web.request"):
            span = dd_trace_api.tracer.current_root_span()
            self._assert_span_stub(span)
            with dd_trace_api.tracer.trace("web.other.request"):
                root_from_nested = dd_trace_api.tracer.current_root_span()
                self._assert_span_stub(root_from_nested)
        self._assert_real_spans(2)
