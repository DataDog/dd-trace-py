import pytest


@pytest.mark.subprocess(
    env={
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "grpc",
        "DD_TRACE_SAMPLE_RATE": "1",
    }
)
def test_otlp_traces_sent_via_grpc():
    from importlib import metadata

    for package in ("opentelemetry-sdk", "opentelemetry-exporter-otlp"):
        try:
            metadata.version(package)
        except metadata.PackageNotFoundError:
            continue
        raise AssertionError("%s must not be installed for this test" % package)

    from tests.opentelemetry.utils import capture_otlp_traces

    with capture_otlp_traces("grpc") as requests:
        from ddtrace.trace import tracer

        with tracer.trace("test-span", service="test-svc"):
            with tracer.trace("test-child", service="test-svc"):
                pass
        tracer.flush()

    spans = [
        span
        for request in requests
        for resource_spans in request.resource_spans
        for scope_spans in resource_spans.scope_spans
        for span in scope_spans.spans
    ]
    assert {span.name for span in spans} == {"test-span", "test-child"}
