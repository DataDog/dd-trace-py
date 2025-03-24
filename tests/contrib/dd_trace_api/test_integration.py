import sys
from typing import Any

import dd_trace_api
import pytest

from ddtrace._trace.span import Span as dd_span_class
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
        return spans

    def test_tracer_singleton(self):
        assert isinstance(dd_trace_api.tracer, dd_trace_api.Tracer), "Tracer stub should be exposed as a singleton"

    @pytest.mark.snapshot()
    def test_start_span(self):
        with dd_trace_api.tracer.start_span("web.request") as span:
            self._assert_span_stub(span)
        self._assert_real_spans()

    @pytest.mark.snapshot()
    def test_span_finish(self):
        span = dd_trace_api.tracer.start_span("web.request")
        self._assert_span_stub(span)
        span.finish()
        self._assert_real_spans()

    @pytest.mark.snapshot()
    def test_span_finish_with_ancestors(self):
        span = dd_trace_api.tracer.start_span("web.request")
        child_span = dd_trace_api.tracer.start_span("web.request", child_of=span)
        child_span.finish_with_ancestors()
        self._assert_real_spans(2)

    @pytest.mark.snapshot()
    def test_trace(self):
        with dd_trace_api.tracer.trace("web.request") as span:
            self._assert_span_stub(span)
        self._assert_real_spans()

    @pytest.mark.snapshot()
    def test_current_span(self):
        with dd_trace_api.tracer.trace("web.request"):
            span = dd_trace_api.tracer.current_span()
            self._assert_span_stub(span)
        self._assert_real_spans()

    @pytest.mark.snapshot()
    def test_current_root_span(self):
        with dd_trace_api.tracer.trace("web.request"):
            span = dd_trace_api.tracer.current_root_span()
            self._assert_span_stub(span)
            with dd_trace_api.tracer.trace("web.other.request"):
                root_from_nested = dd_trace_api.tracer.current_root_span()
                self._assert_span_stub(root_from_nested)
        self._assert_real_spans(2)

    @pytest.mark.snapshot()
    def test_wrap(self):
        @dd_trace_api.tracer.wrap()
        def foo():
            return 1 + 1

        result = foo()
        assert result == 2
        self._assert_real_spans()

    @pytest.mark.snapshot(ignores=["meta.error.stack"])
    def test_set_traceback(self):
        with dd_trace_api.tracer.trace("web.request") as span:
            try:
                raise Exception
            except Exception:  # noqa
                span.set_traceback()
        spans = self._assert_real_spans()
        assert "error.stack" in spans[0]._meta

    @pytest.mark.snapshot(ignores=["meta.error.stack"])
    def test_set_exc_info(self):
        with dd_trace_api.tracer.trace("web.request") as span:
            try:
                raise Exception
            except Exception:  # noqa
                span.set_exc_info(*sys.exc_info())
        spans = self._assert_real_spans()
        assert "error.message" in spans[0]._meta
        assert "error.stack" in spans[0]._meta
        assert "error.type" in spans[0]._meta

    @pytest.mark.snapshot(ignores=["meta.error.stack"])
    def test_exc_info_caught(self):
        try:
            with dd_trace_api.tracer.trace("web.request"):
                raise Exception
        except Exception:  # noqa
            pass
        spans = self._assert_real_spans()
        assert "error.message" in spans[0]._meta
        assert "error.stack" in spans[0]._meta
        assert "error.type" in spans[0]._meta

    @pytest.mark.snapshot()
    def test_set_tags(self):
        with dd_trace_api.tracer.trace("web.request") as span:
            span.set_tags({"tag1": "value1", "tag2": "value2"})
        spans = self._assert_real_spans()
        assert spans[0]._meta["tag1"] == "value1", "Tag set via API should be applied to the real spans"
        assert spans[0]._meta["tag2"] == "value2", "Tag set via API should be applied to the real spans"
