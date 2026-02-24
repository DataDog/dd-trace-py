"""
Tests for OTLP trace export: config, mapper, serializer, exporter, and writer.

Enablement: Option A — OTLP is used when OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
or OTEL_EXPORTER_OTLP_ENDPOINT is set (single export, no Datadog agent).
"""

from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import json
import threading

from ddtrace.internal.settings._otel_exporter import config as otel_config
from ddtrace.internal.writer import OTLPWriter
from ddtrace.internal.writer import create_trace_writer
from ddtrace.internal.writer.otlp import dd_trace_to_otlp_request
from ddtrace.internal.writer.otlp import otlp_request_to_json_bytes
from ddtrace.trace import Span
from tests.utils import BaseTestCase
from tests.utils import override_env


class TestOTLPConfig(BaseTestCase):
    """OTLP exporter config: env vars, precedence, default URL, enablement (Option A)."""

    def test_otlp_disabled_when_no_endpoint(self):
        # Explicitly unset OTLP endpoint vars so Option A disablement is clear
        with override_env(
            {
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "",
            }
        ):
            assert _is_otlp_enabled() is False

    def test_otlp_enabled_when_traces_endpoint_set(self):
        with override_env({"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://collector:4318"}):
            assert _is_otlp_enabled() is True
            assert otel_config.otlp_traces_endpoint == "http://collector:4318/v1/traces"

    def test_otlp_enabled_when_generic_endpoint_set(self):
        with override_env({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}):
            assert _is_otlp_enabled() is True
            assert "4318" in otel_config.otlp_traces_endpoint and "/v1/traces" in otel_config.otlp_traces_endpoint

    def test_traces_endpoint_overrides_generic(self):
        with override_env(
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://generic:4318",
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://traces-only:4319",
            }
        ):
            assert otel_config.otlp_traces_endpoint == "http://traces-only:4319/v1/traces"

    def test_default_url_http_json(self):
        with override_env({"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://localhost:4318"}):
            url = otel_config.otlp_traces_endpoint
            assert url == "http://localhost:4318/v1/traces"

    def test_headers_parsed(self):
        with override_env(
            {
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://localhost:4318",
                "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "a=1,b=2",
            }
        ):
            assert otel_config.otlp_traces_headers.get("a") == "1"
            assert otel_config.otlp_traces_headers.get("b") == "2"


def _is_otlp_enabled():
    """Read enablement from config (Option A)."""
    return otel_config.otlp_traces_enabled


class TestOTLPMapper(BaseTestCase):
    """DD trace -> OTLP ExportTraceServiceRequest structure."""

    def test_empty_trace(self):
        out = dd_trace_to_otlp_request([], service_name="svc", env="env", version="1.0")
        assert "resource_spans" in out
        assert out["resource_spans"] == []

    def test_single_span_trace(self):
        span = Span(name="op", service="svc", resource="/", trace_id=1, span_id=2, parent_id=None)
        span.start_ns = 1000000000
        span.duration_ns = 50000000
        span.finish()
        spans = [span]
        out = dd_trace_to_otlp_request(spans, service_name="svc", env="prod", version="1.0")
        assert len(out["resource_spans"]) == 1
        rs = out["resource_spans"][0]
        assert "resource" in rs
        assert "scope_spans" in rs
        assert len(rs["scope_spans"]) == 1
        scopes = rs["scope_spans"][0]
        assert "spans" in scopes
        assert len(scopes["spans"]) == 1
        otlp_span = scopes["spans"][0]
        assert otlp_span["name"] == "op"
        assert otlp_span["trace_id"] == "00000000000000000000000000000001"
        assert otlp_span["span_id"] == "0000000000000002"
        assert otlp_span["parent_span_id"] in (None, "")
        assert "start_time_unix_nano" in otlp_span
        assert "end_time_unix_nano" in otlp_span
        attrs = rs["resource"]["attributes"]
        keys = [a["key"] for a in attrs]
        assert "service.name" in keys
        assert "telemetry.sdk.name" in keys
        assert "deployment.environment" in keys
        assert "service.version" in keys


class TestOTLPSerializer(BaseTestCase):
    """ExportTraceServiceRequest -> JSON bytes."""

    def test_serialize_roundtrip(self):
        request = {"resource_spans": []}
        raw = otlp_request_to_json_bytes(request)
        assert isinstance(raw, bytes)
        decoded = json.loads(raw.decode("utf-8"))
        assert decoded == request


class TestCreateTraceWriterOTLP(BaseTestCase):
    """create_trace_writer returns OTLPWriter when OTLP is enabled (Option A)."""

    def test_returns_otlp_writer_when_endpoint_set(self):
        with override_env({"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://localhost:4318"}):
            writer = create_trace_writer()
            assert isinstance(writer, OTLPWriter)
            assert "4318" in writer.intake_url

    def test_returns_agent_writer_when_otlp_not_set(self):
        # No OTLP endpoint; DD agent URL set so we get AgentWriter not LogWriter
        with override_env(
            {
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "",
                "DD_TRACE_AGENT_URL": "http://localhost:8126",
            }
        ):
            writer = create_trace_writer()
            assert not isinstance(writer, OTLPWriter)


class TestOTLPWriterExport(BaseTestCase):
    """OTLPWriter write/flush and HTTP export (success and failure)."""

    def test_otlp_writer_write_flush_success(self):
        received = []
        lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                with lock:
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length)
                    received.append((self.path, json.loads(body.decode("utf-8"))))
                self.send_response(200)
                self.end_headers()

            def log_message(self, fmt, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        url = "http://127.0.0.1:%d/v1/traces" % port

        def run_server():
            server.serve_forever()

        t = threading.Thread(target=run_server)
        t.daemon = True
        t.start()
        try:
            writer = OTLPWriter(
                endpoint_url=url,
                headers={},
                timeout_seconds=1.0,
                processing_interval=0.1,
                sync_mode=True,
            )
            span = Span(name="test", service="svc", resource="/", trace_id=1, span_id=2, parent_id=None)
            span.start_ns = 1000000000
            span.duration_ns = 1000000
            span.finish()
            writer.write([span])
            writer.flush_queue()
            writer.stop(1.0)
            with lock:
                assert len(received) == 1
                path, body = received[0]
                assert "traces" in path or body.get("resource_spans")
                assert len(body["resource_spans"]) == 1
                assert len(body["resource_spans"][0]["scope_spans"][0]["spans"]) == 1
        finally:
            server.shutdown()
            server.server_close()
