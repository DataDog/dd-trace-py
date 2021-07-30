from ddtrace import appsec
from tests.utils import TracerTestCase
from tests.utils import override_env


class TestSqreenLibrary(TracerTestCase):
    def test_report(self):
        appsec.enable()

        with self.trace("test") as span:
            appsec.process_request(span, query="foo=bar")
        assert "_dd.sq.process_ms" in span.metrics

        with self.trace("test") as span:
            appsec.process_request(span, query="q=<script>alert(1);")
        assert "_dd.sq.process_ms" in span.metrics
        assert "_dd.sq.reports" in span.metrics

    def test_overtime(self):
        with override_env({"DD_APPSEC_BUDGET_MS": "0"}):
            appsec.enable()

            with self.trace("test") as span:
                appsec.process_request(span, query="foo=bar")
            assert "_dd.sq.process_ms" in span.metrics
            assert span.metrics.get("_dd.sq.overtime_ms") == span.metrics.get("_dd.sq.process_ms")
