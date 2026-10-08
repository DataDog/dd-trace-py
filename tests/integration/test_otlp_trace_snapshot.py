import pytest


# The test agent snapshots the exported OTLP traces to <token>_otlp_traces.json as they are sent to a collector.
@pytest.mark.snapshot(ignores=["meta.tracestate"])
@pytest.mark.subprocess(
    env={
        "DD_SERVICE": "otlp-trace-snapshot",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
    },
)
def test_otlp_trace_snapshot():
    from ddtrace.trace import tracer

    with tracer.trace("http.request", resource="GET /users", span_type="http") as span:
        span.set_tag("span.kind", "client")
        span.set_tag("http.method", "GET")
        span.set_tag("http.status_code", "200")
