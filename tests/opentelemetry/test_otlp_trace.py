import pytest


@pytest.mark.subprocess(
    env={
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/json",
        "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED": "true",
        "DD_TRACE_SAMPLE_RATE": "1",
    }
)
def test_otlp_traces_sent_via_http():
    """With OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/json, traces are exported as OTLP JSON."""
    from http.server import BaseHTTPRequestHandler
    import json
    import os
    import queue
    import socketserver
    import threading

    received = queue.Queue()

    class OtlpHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            received.put((self.path, self.headers.get("Content-Type", ""), body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), OtlpHandler) as server:
        port = server.server_address[1]
        # Set the endpoint before importing ddtrace so the NativeWriter picks it up at init time.
        os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] = f"http://127.0.0.1:{port}/v1/traces"

        t = threading.Thread(target=server.serve_forever)
        t.daemon = True
        t.start()

        from ddtrace.trace import tracer

        with tracer.trace("test-span", service="test-svc"):
            pass

        tracer.flush()
        server.shutdown()

    assert not received.empty(), "No OTLP payload received by mock server"
    path, content_type, body = received.get_nowait()
    assert path == "/v1/traces", f"Unexpected path: {path}"
    assert "json" in content_type, f"Expected JSON content type, got: {content_type}"
    payload = json.loads(body)
    assert "resourceSpans" in payload, f"Missing resourceSpans in payload: {payload}"
    resource_spans = payload["resourceSpans"]
    assert len(resource_spans) >= 1
    scope_spans = resource_spans[0]["scopeSpans"]
    assert len(scope_spans) >= 1
    spans = scope_spans[0]["spans"]
    assert len(spans) >= 1
    assert spans[0]["name"] == "test-span"


@pytest.mark.subprocess(
    env={
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_TRACES_SPAN_METRICS_ENABLED": "true",
        # Note: OTEL_EXPORTER_OTLP_METRICS_PROTOCOL is intentionally NOT set. The native
        # trace-metrics exporter is HTTP/JSON only and derives an HTTP /v1/metrics endpoint
        # regardless of the configured metrics protocol.
        "DD_SERVICE": "test-svc",
    }
)
def test_otlp_trace_metrics_exported_via_http():
    """End-to-end: libdatadog computes span stats and exports the traces.span.sdk.metrics.duration histogram.

    Stats computation, OTLP encoding and export all happen natively in libdatadog; dd-trace-py only
    supplies the OTLP metrics endpoint to the NativeWriter. The native stats worker flushes the
    concentrator on its periodic tick and POSTs the histogram to the OTLP /v1/metrics endpoint.
    Service identity is reported as resource attributes; a span whose service differs from the
    configured default additionally carries service.name on its data point.
    """
    from http.server import BaseHTTPRequestHandler
    from http.server import ThreadingHTTPServer
    import json
    import os
    import queue
    import threading
    import time

    received = queue.Queue()

    class OtlpHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            received.put((self.path, self.headers.get("Content-Type", ""), self.rfile.read(length)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), OtlpHandler) as server:
        port = server.server_address[1]
        # OTEL_EXPORTER_OTLP_METRICS_ENDPOINT is used as-is (full path) by libdatadog.
        os.environ["OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"] = f"http://127.0.0.1:{port}/v1/metrics"
        # Route traces to the mock server too so the native writer doesn't log send failures.
        os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] = f"http://127.0.0.1:{port}/v1/traces"
        # The flush cadence is fixed at 10s; shorten it for the test so the bucket exports quickly.
        os.environ["_DD_TRACE_METRICS_OTEL_FLUSH_INTERVAL"] = "1000"

        t = threading.Thread(target=server.serve_forever)
        t.daemon = True
        t.start()

        from ddtrace.trace import tracer

        with tracer.trace("test-span", service="test-svc"):
            pass

        # flush() sends the trace and feeds the native concentrator. The native stats worker then
        # flushes the aged-out bucket on its periodic tick and exports it to /v1/metrics.
        tracer.flush()

        metrics_payload = None
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                path, content_type, body = received.get(timeout=1)
            except queue.Empty:
                continue
            if path == "/v1/metrics":
                metrics_payload = (content_type, body)
                break

        server.shutdown()

    assert metrics_payload is not None, "No OTLP metrics payload received by mock server"
    content_type, body = metrics_payload
    assert "json" in content_type, f"Expected JSON content type, got: {content_type}"
    payload = json.loads(body)
    resource_metrics = payload["resourceMetrics"][0]
    scope_metrics = resource_metrics["scopeMetrics"][0]
    metric = scope_metrics["metrics"][0]
    assert metric["name"] == "traces.span.sdk.metrics.duration"
    # No InstrumentationScope is emitted (redundant with the resource's telemetry.sdk.* attributes).
    assert "scope" not in scope_metrics
    # Service identity lives on the resource.
    resource_attrs = {a["key"]: a["value"]["stringValue"] for a in resource_metrics["resource"]["attributes"]}
    assert resource_attrs["service.name"] == "test-svc"
    assert metric["histogram"]["dataPoints"], "No data points in exported histogram"


@pytest.mark.subprocess(
    env={
        "OTEL_TRACES_EXPORTER": "otlp",
        "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED": "true",
        "DD_TRACE_SAMPLE_RATE": "1",
    }
)
def test_otlp_traces_sent_via_http_protobuf_default():
    """With no protocol configured, OTLP traces default to http/protobuf on the HTTP endpoint."""
    from http.server import BaseHTTPRequestHandler
    import os
    import queue
    import socketserver
    import threading

    received = queue.Queue()

    class OtlpHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            received.put((self.path, self.headers.get("Content-Type", ""), body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), OtlpHandler) as server:
        port = server.server_address[1]
        # Set the endpoint before importing ddtrace so the NativeWriter picks it up at init time.
        os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] = f"http://127.0.0.1:{port}/v1/traces"

        t = threading.Thread(target=server.serve_forever)
        t.daemon = True
        t.start()

        from ddtrace.trace import tracer

        with tracer.trace("test-span", service="test-svc"):
            pass

        tracer.flush()
        server.shutdown()

    assert not received.empty(), "No OTLP payload received by mock server"
    path, content_type, body = received.get_nowait()
    assert path == "/v1/traces", f"Unexpected path: {path}"
    assert "protobuf" in content_type, f"Expected protobuf content type, got: {content_type}"
    # Decode-free protobuf checks (opentelemetry-proto isn't a dependency of this venv): an OTLP
    # ExportTraceServiceRequest starts with field 1 (resource_spans), wire type 2 -> byte 0x0a, and
    # the span name is a UTF-8 string field whose bytes appear verbatim in the encoded payload.
    assert body, "Empty protobuf body"
    assert body[0] == 0x0A, f"Body is not an OTLP protobuf payload: {body[:8]!r}"
    assert b"test-span" in body, "Span name not found in protobuf payload"
