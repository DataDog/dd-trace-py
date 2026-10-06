from types import SimpleNamespace

import pytest


@pytest.mark.subprocess()
def test_otel_metrics_exporter_installed():
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter as HTTPExporter

    from ddtrace.internal.opentelemetry.grpclib_metric_exporter import OTLPMetricExporter as GRPCExporter

    grpc_exporter = GRPCExporter()
    http_exporter = HTTPExporter()
    grpc_exporter.shutdown()
    http_exporter.shutdown()


@pytest.mark.subprocess(ddtrace_run=True, env={"DD_METRICS_OTEL_ENABLED": "true"})
def test_otel_metrics_enabled():
    """
    Test that the OpenTelemetry metrics exporter is automatically configured when DD_METRICS_OTEL_ENABLED is set.
    """
    from opentelemetry.metrics import get_meter_provider

    meter_provider = get_meter_provider()
    assert meter_provider, "OpenTelemetry metrics exporter should be configured automatically."


@pytest.mark.subprocess(ddtrace_run=True, parametrize={"DD_METRICS_OTEL_ENABLED": [None, "false"]})
def test_otel_metrics_disabled_and_unset():
    """
    Test that the OpenTelemetry metrics exporter is NOT automatically configured when DD_METRICS_OTEL_ENABLED is set.
    """
    from opentelemetry.metrics import get_meter_provider

    meter_provider = get_meter_provider()
    assert (meter_provider is None) or (type(meter_provider).__name__ == "_ProxyMeterProvider"), (
        "OpenTelemetry mterics exporter should not be configured automatically."
    )


def _metrics_data():
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.resources import Resource

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=(reader,), resource=Resource.create({"service.name": "test"}))
    provider.get_meter("test").create_counter("requests").add(
        2,
        {
            "route": "/items",
            "cached": True,
            "status": 200,
            "ratio": 0.5,
            "regions": ("us", "eu"),
        },
    )
    return provider, reader.get_metrics_data()


def _attributes(request):
    point = request.resource_metrics[0].scope_metrics[0].metrics[0].sum.data_points[0]
    return {attribute.key: attribute.value for attribute in point.attributes}


def test_grpclib_exporter_preserves_attribute_types_and_headers(monkeypatch):
    from opentelemetry.sdk.metrics.export import MetricExportResult

    from ddtrace.internal.opentelemetry.grpclib_metric_exporter import OTLPMetricExporter

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
    assert attributes["route"].string_value == "/items"
    assert attributes["cached"].bool_value is True
    assert attributes["status"].int_value == 200
    assert attributes["ratio"].double_value == 0.5
    assert [value.string_value for value in attributes["regions"].array_value.values] == ["us", "eu"]
    assert calls[0][0] == pytest.approx(3, abs=0.1)
    assert calls[0][1] == (("authorization", "Bearer token"), ("x-test", "value"))


def test_grpclib_exporter_uses_otlp_temporality_preference(monkeypatch):
    from opentelemetry.sdk.metrics._internal.instrument import Counter
    from opentelemetry.sdk.metrics.export import AggregationTemporality

    from ddtrace.internal.opentelemetry.grpclib_metric_exporter import OTLPMetricExporter

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE", "delta")
    exporter = OTLPMetricExporter()
    try:
        assert exporter._preferred_temporality[Counter] is AggregationTemporality.DELTA
    finally:
        exporter.shutdown()


def test_resource_attributes_preserve_types(monkeypatch):
    from ddtrace.internal.opentelemetry import metrics

    monkeypatch.setattr(
        metrics,
        "config",
        SimpleNamespace(
            tags={"enabled": True, "retries": 2, "regions": ("us", "eu")},
            service="test",
            version=None,
            env=None,
            _report_hostname=False,
        ),
    )
    attributes = metrics._build_resource().attributes

    assert attributes["enabled"] is True
    assert attributes["retries"] == 2
    assert attributes["regions"] == ("us", "eu")


@pytest.mark.parametrize(
    ("protocol", "module"),
    [
        ("grpc", "ddtrace.internal.opentelemetry.grpclib_metric_exporter"),
        ("http/protobuf", "opentelemetry.exporter.otlp.proto.http.metric_exporter"),
    ],
)
def test_protocol_selects_exporter(protocol, module):
    from ddtrace.internal.opentelemetry.metrics import _import_exporter

    assert _import_exporter(protocol).__mro__[1].__module__ == module


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_LOGS_OTEL_ENABLED": "true",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector.example:4318",
    },
)
def test_otlp_export_requests_are_not_traced():
    """OTLP exporter requests must not be traced.

    The OTLP HTTP metrics exporter omits the OTLP user-agent header that the trace and log
    exporters set, so detection falls back to matching the enabled export URLs by full path.
    """
    import requests

    from ddtrace.contrib.internal.requests.connection import is_otlp_export

    def prepared(url):
        return requests.Request("POST", url, headers={"User-Agent": "python-requests/2.34.2"}).prepare()

    # Exports for enabled signals are matched by their full URL.
    assert is_otlp_export(prepared("http://collector.example:4318/v1/logs")) is True
    assert is_otlp_export(prepared("http://collector.example:4318/v1/traces")) is True
    # A disabled signal's endpoint, a different path or scheme, and other hosts are user traffic.
    assert is_otlp_export(prepared("http://collector.example:4318/v1/metrics")) is False
    assert is_otlp_export(prepared("http://collector.example:4318/api/data")) is False
    assert is_otlp_export(prepared("https://collector.example:4318/v1/logs")) is False
    assert is_otlp_export(prepared("http://api.example:8080/v1/logs")) is False
