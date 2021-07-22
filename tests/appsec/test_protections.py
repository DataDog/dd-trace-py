from ddtrace import appsec
from tests.utils import TracerTestCase


class TestSqreenLibrary(TracerTestCase):
    def test_report(self):
        appsec.enable()

        with self.trace("test") as span:
            appsec.process_request(span, query="foo=bar")
        assert "sq.process_ms" in span.metrics

        with self.trace("test") as span:
            appsec.process_request(span, query="q=<script>alert(1);")
        assert "sq.process_ms" in span.metrics
        assert "sq.reports" in span.metrics
