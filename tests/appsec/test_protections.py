import json


def test_sqreen_library_report(appsec, tracer):
    appsec.enable()

    with tracer.trace("test") as span:
        appsec.process_request(span, query="foo=bar")
    assert "_dd.appsec.events_ms" in span.metrics


def test_sqreen_library_attack_event(appsec, tracer):
    appsec.enable()

    with tracer.trace("test") as span:
        appsec.process_request(span, query="q=<script>alert(1);")
    evt = json.loads(span.get_tag("_dd.appsec.events"))[0]
    assert evt.get("event_type") == "appsec.threat.attack"
    assert evt["rule_match"]["operator"] == "match_regex"
    assert evt["rule_match"]["parameters"][0]["address"] == "http.server.query"
