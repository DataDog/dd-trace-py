from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import threading

import pytest


OTEL_VERSION = tuple(int(part) for part in pytest.importorskip("opentelemetry.version").__version__.split(".")[:3])
pytestmark = pytest.mark.skipif(
    OTEL_VERSION < (1, 34, 0),
    reason="the vendored metrics SDK requires OpenTelemetry API 1.34 or newer",
)


def _metrics_data():
    from ddtrace.vendor.otel.sdk.metrics import MeterProvider
    from ddtrace.vendor.otel.sdk.metrics.export import InMemoryMetricReader
    from ddtrace.vendor.otel.sdk.resources import Resource

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=(reader,), resource=Resource.create({"service.name": "vendoring-test"}))
    meter = provider.get_meter("vendoring-test")
    meter.create_counter("requests").add(
        2,
        {
            "route": "/items",
            "cached": True,
            "status": 200,
            "ratio": 0.5,
            "regions": ["us", "eu"],
        },
    )
    return provider, reader.get_metrics_data()


def _attributes(request):
    point = request.resource_metrics[0].scope_metrics[0].metrics[0].sum.data_points[0]
    return {attribute.key: attribute.value for attribute in point.attributes}


def test_vendored_http_exporter_preserves_attribute_types():
    from ddtrace.vendor.otel.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from ddtrace.vendor.otel.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
    from ddtrace.vendor.otel.sdk.metrics.export import MetricExportResult

    payloads = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payloads.append(self.rfile.read(int(self.headers["content-length"])))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        provider, metrics_data = _metrics_data()
        exporter = OTLPMetricExporter(endpoint=f"http://127.0.0.1:{server.server_port}/v1/metrics")
        try:
            assert exporter.export(metrics_data) is MetricExportResult.SUCCESS
        finally:
            exporter.shutdown()
            provider.shutdown()
            server.shutdown()
            thread.join()

    attributes = _attributes(ExportMetricsServiceRequest.FromString(payloads[0]))
    assert attributes["route"].string_value == "/items"
    assert attributes["cached"].bool_value is True
    assert attributes["status"].int_value == 200
    assert attributes["ratio"].double_value == 0.5
    assert [value.string_value for value in attributes["regions"].array_value.values] == ["us", "eu"]


def test_grpclib_exporter_encodes_metrics_and_headers(monkeypatch):
    from ddtrace.internal.opentelemetry.grpclib_metric_exporter import OTLPMetricExporter
    from ddtrace.vendor.otel.sdk.metrics.export import MetricExportResult

    requests = []
    calls = []

    async def export(request, *, timeout, metadata):
        requests.append(request)
        calls.append((timeout, metadata))

    provider, metrics_data = _metrics_data()
    exporter = OTLPMetricExporter(
        endpoint="http://127.0.0.1:4317",
        headers="authorization=Bearer%20token,x-test=value",
        timeout=3,
    )
    monkeypatch.setattr(exporter, "_method", export)
    try:
        assert exporter.export(metrics_data) is MetricExportResult.SUCCESS
    finally:
        exporter.shutdown()
        provider.shutdown()

    attributes = _attributes(requests[0])
    assert attributes["cached"].bool_value is True
    assert attributes["status"].int_value == 200
    assert calls == [(3, (("authorization", "Bearer token"), ("x-test", "value")))]


@pytest.mark.parametrize(
    ("protocol", "module"),
    [
        ("grpc", "ddtrace.internal.opentelemetry.grpclib_metric_exporter"),
        ("http/protobuf", "ddtrace.vendor.otel.exporter.otlp.proto.http.metric_exporter"),
    ],
)
def test_bundled_exporter_is_used(protocol, module):
    from ddtrace.internal.opentelemetry.metrics import _import_exporter

    assert _import_exporter(protocol).__mro__[1].__module__ == module
