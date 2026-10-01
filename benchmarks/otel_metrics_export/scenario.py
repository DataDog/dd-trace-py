from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import os
import threading

import bm
from opentelemetry.metrics import get_meter_provider


class _Receiver(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", "0")))
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, _format, *args):
        pass


_server = HTTPServer(("127.0.0.1", 0), _Receiver)
threading.Thread(target=_server.serve_forever, daemon=True).start()

os.environ.update(
    {
        "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false",
        "DD_METRICS_OTEL_ENABLED": "true",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL": "http/protobuf",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": f"http://127.0.0.1:{_server.server_port}/v1/metrics",
        "OTEL_METRIC_EXPORT_INTERVAL": "3600000",
    }
)

from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider  # noqa: E402


set_otel_meter_provider()
_provider = get_meter_provider()
_meter = _provider.get_meter("ddtrace.benchmark")
_counter = _meter.create_counter("requests", unit="1")
_histogram = _meter.create_histogram("latency", unit="ms")


class OtelMetricsExport(bm.Scenario):
    metric_count: int

    def run(self):
        operations = []
        for index in range(self.metric_count):
            attributes = {"route": f"/benchmark/{index}"}
            if index % 2:
                operations.append((_histogram.record, index + 0.5, attributes))
            else:
                operations.append((_counter.add, 1, attributes))

        def export(loops):
            for _ in range(loops):
                for record, value, attributes in operations:
                    record(value, attributes)
                _provider.force_flush()

        yield export
