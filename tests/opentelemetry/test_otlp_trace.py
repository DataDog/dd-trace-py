import pytest


@pytest.mark.subprocess(
    env={
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:14318/v1/traces",
        "DD_TRACE_SAMPLE_RATE": "1",
        "DD_SERVICE": "resource-test",
        "DD_TAGS": None,
        "OTEL_RESOURCE_ATTRIBUTES": "team=core,region=us1,workers=7,span.kind=client,_dd.custom=yes,long="
        + "x" * 25001,
    },
    parametrize={"DD_TRACE_OTEL_SEMANTICS_ENABLED": ["true", "false"]},
)
def test_otlp_resource_attributes_are_not_span_attributes():
    import json

    from ddtrace.trace import tracer
    from tests.opentelemetry.utils import _capture_http_requests

    with _capture_http_requests(port=14318) as requests:
        with tracer.trace("resource-root") as root:
            assert root.get_tag("team") == "core"
            with tracer.trace("resource-child") as child:
                child.set_tag("team", "core")
                child.set_tag("region", "eu1")
                child._set_attribute("workers", 7)
                child.set_tag("span.only", "retained")
        tracer.flush()

    assert len(requests) == 1
    resource_spans = json.loads(requests[0])["resourceSpans"][0]
    resource = {a["key"]: a["value"] for a in resource_spans["resource"]["attributes"]}
    spans = {
        span["name"]: {a["key"]: a["value"] for a in span["attributes"]}
        for scope in resource_spans["scopeSpans"]
        for span in scope["spans"]
    }
    assert {"resource-root", "resource-child"} == spans.keys()
    assert resource.get("team") == {"stringValue": "core"}, (resource, spans)
    assert resource.get("region") == {"stringValue": "us1"}
    assert resource.get("workers") == {"stringValue": "7"}
    assert resource["span.kind"] == {"stringValue": "client"}
    assert resource["_dd.custom"] == {"stringValue": "yes"}
    assert resource["long"] == {"stringValue": "x" * 25001}
    for attributes in spans.values():
        assert attributes["long"]["stringValue"].endswith("<truncated>...")
        assert attributes["long"] != resource["long"]
    assert "team" not in spans["resource-root"]
    assert "team" not in spans["resource-child"]
    assert "region" not in spans["resource-root"]
    assert "workers" not in spans["resource-root"]
    assert spans["resource-child"]["region"] == {"stringValue": "eu1"}
    assert spans["resource-child"]["workers"] == {"intValue": "7"}
    assert spans["resource-child"]["span.only"] == {"stringValue": "retained"}


@pytest.mark.subprocess(
    env={
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:14318/v1/traces",
        "DD_TRACE_SAMPLE_RATE": "1",
        "DD_TRACE_WRITER_INTERVAL_SECONDS": "1000",
        "DD_TRACE_PARTIAL_FLUSH_ENABLED": "true",
        "DD_TRACE_PARTIAL_FLUSH_MIN_SPANS": "1",
        "DD_TAGS": "team:initial,removed:yes",
    },
    parametrize={
        "DD_TRACE_OTEL_SEMANTICS_ENABLED": ["true", "false"],
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": ["http/json", "http/protobuf"],
    },
)
def test_otlp_resource_uses_current_tags_at_export():
    import json
    import os

    from google.protobuf.json_format import MessageToDict
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    from ddtrace import config
    from ddtrace import tracer
    from ddtrace._trace.product import _apply_config_change
    from ddtrace._trace.product import apm_tracing_rc
    from tests.opentelemetry.utils import _capture_http_requests

    def decode(body):
        if os.environ["OTEL_EXPORTER_OTLP_TRACES_PROTOCOL"] == "http/json":
            return json.loads(body)
        return MessageToDict(ExportTraceServiceRequest.FromString(body))

    with _capture_http_requests(port=14318) as requests:
        with tracer.trace("buffered") as old:
            assert old.get_tag("team") == "initial"
        with tracer.trace("open") as root:
            apm_tracing_rc({"tracing_tags": ["team:remote"]}, config)
            with tracer.trace("child") as child:
                child.set_tag("team", "override")
            assert root.get_tag("team") == "initial"
        tracer.set_tags({"team": "at-export", "added": "new"})
        tracer.flush()
        tracer.configure(trace_processors=[])
        apm_tracing_rc({}, config)
        tracer.set_tags({"team": "setter"})
        with tracer.trace("reset"):
            pass
        tracer.flush()
        _apply_config_change("tags", {}, config)
        with tracer.trace("empty"):
            pass
        tracer.flush()

    assert len(requests) == 3
    resources = []
    for body in requests:
        [resource_spans] = decode(body)["resourceSpans"]
        resources.append({a["key"]: a["value"] for a in resource_spans["resource"]["attributes"]})
    assert resources[0]["team"] == {"stringValue": "at-export"}
    assert "removed" not in resources[0]
    assert resources[0]["added"] == {"stringValue": "new"}
    assert resources[1]["team"] == {"stringValue": "setter"}
    assert resources[1]["removed"] == {"stringValue": "yes"}
    assert "team" not in resources[2] and "removed" not in resources[2]
    spans = {
        span["name"]: {a["key"]: a["value"] for a in span.get("attributes", [])}
        for scope in decode(requests[0])["resourceSpans"][0]["scopeSpans"]
        for span in scope["spans"]
    }
    assert set(spans) == {"buffered", "open", "child"}
    for name in ("buffered", "open"):
        assert spans[name]["team"] == {"stringValue": "initial"}
        assert spans[name]["removed"] == {"stringValue": "yes"}
    assert all("added" not in span for span in spans.values())
    assert spans["child"]["team"] == {"stringValue": "override"}


@pytest.mark.subprocess(
    env={
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:14318/v1/traces",
        "DD_TRACE_SAMPLING_RULES": '[{"sample_rate":0}]',
        "DD_SPAN_SAMPLING_RULES": '[{"name":"keep","sample_rate":1}]',
        "DD_TAGS": "team:core",
    },
    parametrize={"DD_TRACE_OTEL_SEMANTICS_ENABLED": ["true", "false"]},
)
def test_otlp_resource_survives_root_drop():
    import json

    from ddtrace import tracer
    from tests.opentelemetry.utils import _capture_http_requests

    with _capture_http_requests(port=14318) as requests:
        with tracer.trace("drop") as root:
            with tracer.trace("keep"):
                pass
        assert root.context.sampling_priority < 0
        tracer.flush()

    assert len(requests) == 1
    [resource_spans] = json.loads(requests[0])["resourceSpans"]
    resource = {a["key"]: a["value"] for a in resource_spans["resource"]["attributes"]}
    assert resource["team"] == {"stringValue": "core"}
    spans = [span for scope in resource_spans["scopeSpans"] for span in scope["spans"]]
    assert [span["name"] for span in spans] == ["keep"]
    assert "team" not in {a["key"] for a in spans[0]["attributes"]}


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
            with tracer.trace("test-child", service="test-svc"):
                pass

        from ddtrace.trace import Context

        inherited_context = Context(
            trace_id=0xFFF972474538EFFF,
            span_id=1,
            sampling_priority=2,
            meta={"tracestate": ("dd=s:2;t.dm:-3,ot=rv:ef284ace7a91e1;th:e6666666666668,future=value")},
        )
        with tracer.start_span("inherited-span", child_of=inherited_context, service="test-svc"):
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
    spans_by_name = {span["name"]: span for span in spans}
    assert {"test-span", "test-child", "inherited-span"} <= spans_by_name.keys()

    for name in ("test-span", "test-child"):
        span = spans_by_name[name]
        assert span["flags"] == 1
        assert "ot=rv:" in span["traceState"]
        assert ";th:0" in span["traceState"]
        assert "p:{}".format(span["spanId"].lower()) in span["traceState"]

    inherited_span = spans_by_name["inherited-span"]
    assert inherited_span["flags"] == 1
    assert "ot=rv:ef284ace7a91e1;th:e6666666666668" in inherited_span["traceState"]
    assert "future=value" in inherited_span["traceState"]


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
    resource_attrs = {a["key"]: a["value"] for a in resource_metrics["resource"]["attributes"]}
    assert resource_attrs["service.name"]["stringValue"] == "test-svc"
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
