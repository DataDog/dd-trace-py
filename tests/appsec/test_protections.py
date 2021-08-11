from tests.utils import DummyTracer
from tests.utils import override_env


def test_sqreen_library_report(appsec):
    tracer = DummyTracer()
    appsec.enable()

    with tracer.trace("test") as span:
        appsec.process_request(span, query="foo=bar")
    assert "_dd.sq.process_ms" in span.metrics

    with tracer.trace("test") as span:
        appsec.process_request(span, query="q=<script>alert(1);")
    assert "_dd.sq.process_ms" in span.metrics
    assert "_dd.sq.reports" in span.metrics


def test_sqreen_library_overtime(appsec):
    tracer = DummyTracer()
    with override_env({"DD_APPSEC_WAF_BUDGET_MS": "0"}):
        appsec.enable()
        with tracer.trace("test") as span:
            appsec.process_request(span, query="foo=bar")
        assert "_dd.sq.process_ms" in span.metrics
        assert span.metrics.get("_dd.sq.overtime_ms") == span.metrics.get("_dd.sq.process_ms")
