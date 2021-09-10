def test_sqreen_library_report(appsec, tracer):
    appsec.enable()

    with tracer.trace("test") as span:
        appsec.process_request(span, query="foo=bar")
    assert "_dd.appsec.events_ms" in span.metrics
