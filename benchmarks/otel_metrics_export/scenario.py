from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import os
import threading

import bm
import grpc
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


def _start_http_receiver():
    server = HTTPServer(("127.0.0.1", 0), _Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/v1/metrics"


def _start_grpc_receiver():
    def export(_request, _context):
        return b""

    handler = grpc.unary_unary_rpc_method_handler(
        export,
        request_deserializer=lambda payload: payload,
        response_serializer=lambda payload: payload,
    )
    service = grpc.method_handlers_generic_handler(
        "opentelemetry.proto.collector.metrics.v1.MetricsService", {"Export": handler}
    )
    server = grpc.server(
        ThreadPoolExecutor(max_workers=1), options=(("grpc.max_receive_message_length", 64 * 1024 * 1024),)
    )
    server.add_generic_rpc_handlers((service,))
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, f"http://127.0.0.1:{port}"


class OtelMetricsExport(bm.Scenario):
    metric_count: int
    protocol: str

    def _setup(self):
        if hasattr(self, "_provider"):
            return

        if self.protocol == "grpc":
            self._receiver, endpoint = _start_grpc_receiver()
        else:
            self._receiver, endpoint = _start_http_receiver()

        os.environ.update(
            {
                "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false",
                "DD_METRICS_OTEL_ENABLED": "true",
                "OTEL_METRICS_EXPORTER": "otlp",
                "OTEL_EXPORTER_OTLP_PROTOCOL": self.protocol,
                "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL": self.protocol,
                "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": endpoint,
                "OTEL_METRIC_EXPORT_INTERVAL": "3600000",
            }
        )

        from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

        set_otel_meter_provider()
        self._provider = get_meter_provider()
        meter = self._provider.get_meter("ddtrace.benchmark")
        self._counter = meter.create_counter("requests", unit="1")
        self._histogram = meter.create_histogram("latency", unit="ms")

    def run(self):
        self._setup()
        operations = []
        for index in range(self.metric_count):
            attributes = {"route": f"/benchmark/{index}"}
            if index % 2:
                operations.append((self._histogram.record, index + 0.5, attributes))
            else:
                operations.append((self._counter.add, 1, attributes))

        def export(loops):
            for _ in range(loops):
                for record, value, attributes in operations:
                    record(value, attributes)
                self._provider.force_flush()

        yield export
