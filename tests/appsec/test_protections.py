from tests.utils import override_env


def test_sqreen_library_report(appsec, tracer):
    appsec.enable()

    with tracer.trace("test") as span:
        appsec.process_request(span, query="foo=bar")
    assert "_dd.appsec.waf_eval_ms" in span.metrics


def test_sqreen_library_overtime(appsec, tracer):
    with override_env({"DD_APPSEC_WAF_BUDGET_MS": "0"}):
        appsec.enable()
        with tracer.trace("test") as span:
            appsec.process_request(span, query="foo=bar")
        assert "_dd.appsec.waf_eval_ms" in span.metrics
        assert span.metrics.get("_dd.appsec.waf_overtime_ms") == span.metrics.get("_dd.appsec.waf_eval_ms")


def test_sqreen_library_attack_event(appsec, appsec_dummy_writer, tracer):
    with tracer.trace("test") as span:
        appsec.process_request(span, query="q=<script>alert(1);")
    assert appsec_dummy_writer.events[0].event_type == "appsec.threat.attack"
