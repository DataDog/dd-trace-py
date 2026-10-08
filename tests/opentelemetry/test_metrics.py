from importlib import import_module
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
import os
from types import SimpleNamespace

from opentelemetry import version
import pytest


OTEL_VERSION = tuple(int(x) for x in version.__version__.split(".")[:3])


def _exporter_version():
    try:
        version = package_version("opentelemetry-exporter-otlp-proto-common")
    except PackageNotFoundError:
        try:
            from opentelemetry.exporter.otlp.proto.http.version import __version__ as version
        except ImportError:
            return (0, 0, 0)
    return tuple(int(x) for x in version.split(".")[:3])


EXPORTER_VERSION = _exporter_version()


def skipif(
    exporter_installed: bool = False,
    exporter_not_installed: bool = False,
    unsupported_otel_version: bool = False,
):
    if unsupported_otel_version and OTEL_VERSION < (1, 12):
        return pytest.mark.skipif(True, reason="OpenTelemetry 1.12 or newer is required")
    has_exporter = os.getenv("SDK_EXPORTER_INSTALLED", "").lower() in ("true", "1")
    if exporter_installed and has_exporter:
        return pytest.mark.skipif(True, reason="Test requires an API-only OpenTelemetry environment")
    if exporter_not_installed and not has_exporter:
        return pytest.mark.skipif(True, reason="Test requires the OpenTelemetry metrics exporters")
    return pytest.mark.skipif(False, reason="OpenTelemetry dependency set is compatible")


@skipif(exporter_installed=True, unsupported_otel_version=True)
def test_otel_metrics_sdk_not_installed_by_default():
    from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

    set_otel_meter_provider()

    with pytest.raises(ImportError):
        from opentelemetry.sdk.resources import Resource  # noqa: F401


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess()
def test_otel_metrics_exporter_installed():
    from ddtrace.internal.opentelemetry.metrics import _import_exporter

    grpc_exporter = _import_exporter("grpc")()
    http_exporter = _import_exporter("http/protobuf")()
    grpc_exporter.shutdown()
    http_exporter.shutdown()


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(ddtrace_run=True, env={"DD_METRICS_OTEL_ENABLED": "true"})
def test_otel_metrics_enabled():
    """
    Test that the OpenTelemetry metrics exporter is automatically configured when DD_METRICS_OTEL_ENABLED is set.
    """
    from opentelemetry.metrics import get_meter_provider

    meter_provider = get_meter_provider()
    assert meter_provider, "OpenTelemetry metrics exporter should be configured automatically."


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
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


@skipif(exporter_not_installed=True)
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="The lightweight gRPC exporter requires OpenTelemetry 1.18")
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


@skipif(exporter_not_installed=True)
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="The lightweight HTTP exporter requires OpenTelemetry 1.18")
def test_http_exporter_preserves_attribute_types_headers_and_compression(monkeypatch):
    import gzip

    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
    from opentelemetry.sdk.metrics.export import MetricExportResult

    from ddtrace.internal.opentelemetry.http_metric_exporter import OTLPMetricExporter

    calls = []

    def send(payload, timeout):
        calls.append((payload, timeout))
        return 200, None

    provider, metrics_data = _metrics_data()
    exporter = OTLPMetricExporter(
        endpoint="http://127.0.0.1:4318/custom/metrics",
        headers="authorization=Bearer%20token,x-test=value",
        timeout=3,
        compression="gzip",
    )
    monkeypatch.setattr(exporter, "_send", send)
    try:
        assert exporter.export(metrics_data) is MetricExportResult.SUCCESS
    finally:
        exporter.shutdown()
        provider.shutdown()

    request = ExportMetricsServiceRequest.FromString(gzip.decompress(calls[0][0]))
    attributes = _attributes(request)
    assert attributes["route"].string_value == "/items"
    assert attributes["cached"].bool_value is True
    assert attributes["status"].int_value == 200
    assert attributes["ratio"].double_value == 0.5
    assert [value.string_value for value in attributes["regions"].array_value.values] == ["us", "eu"]
    assert calls[0][1] == pytest.approx(3, abs=0.1)
    assert ("authorization", "Bearer token") in exporter._headers
    assert ("x-test", "value") in exporter._headers
    assert ("Content-Encoding", "gzip") in exporter._headers
    assert exporter._path == "/custom/metrics"


@skipif(exporter_not_installed=True)
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="The lightweight gRPC exporter requires OpenTelemetry 1.18")
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


@skipif(exporter_not_installed=True)
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="The lightweight gRPC exporter requires OpenTelemetry 1.18")
def test_grpclib_exporter_ignores_interpreter_shutdown_error(monkeypatch, caplog):
    from opentelemetry.sdk.metrics.export import MetricExportResult

    from ddtrace.internal.opentelemetry.grpclib_metric_exporter import OTLPMetricExporter

    async def export(request, *, timeout, metadata):
        raise RuntimeError("cannot schedule new futures after interpreter shutdown")

    provider, metrics_data = _metrics_data()
    exporter = OTLPMetricExporter(endpoint="http://127.0.0.1:4317")
    monkeypatch.setattr(exporter, "_method", export)
    try:
        assert exporter.export(metrics_data) is MetricExportResult.FAILURE
    finally:
        exporter.shutdown()
        provider.shutdown()

    assert not [record for record in caplog.records if record.levelno >= 30]


@skipif(exporter_not_installed=True)
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
        ("http/protobuf", "ddtrace.internal.opentelemetry.http_metric_exporter"),
    ],
)
@skipif(exporter_not_installed=True)
def test_protocol_selects_exporter(protocol, module):
    from ddtrace.internal.opentelemetry.metrics import _import_exporter

    if EXPORTER_VERSION < (1, 18):
        module = f"opentelemetry.exporter.otlp.proto.{protocol.split('/')[0]}.metric_exporter"
    assert _import_exporter(protocol).__module__ == module


@pytest.mark.parametrize(
    ("module_name", "protocol"),
    [
        ("ddtrace.internal.opentelemetry.grpclib_metric_exporter", "grpc"),
        ("ddtrace.internal.opentelemetry.http_metric_exporter", "http"),
    ],
)
@skipif(exporter_not_installed=True)
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="The lightweight exporters require OpenTelemetry 1.18")
def test_lightweight_exporter_records_telemetry(monkeypatch, module_name, protocol):
    from opentelemetry.sdk.metrics.export import MetricExportResult

    from ddtrace.internal.opentelemetry import exporter_telemetry
    from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE

    telemetry = []
    monkeypatch.setattr(
        exporter_telemetry.telemetry_writer,
        "add_count_metric",
        lambda namespace, name, value, tags: telemetry.append((namespace, name, value, tags)),
    )

    module = import_module(module_name)
    provider, metrics_data = _metrics_data()
    exporter = module.OTLPMetricExporter(endpoint="http://127.0.0.1:4318")
    results = iter((MetricExportResult.SUCCESS, MetricExportResult.FAILURE))
    monkeypatch.setattr(exporter, "_export", lambda *args, **kwargs: next(results))
    try:
        assert exporter.export(metrics_data) is MetricExportResult.SUCCESS
        assert exporter.export(metrics_data) is MetricExportResult.FAILURE
    finally:
        exporter.shutdown()
        provider.shutdown()

    tags = (("protocol", protocol), ("encoding", "protobuf"))
    assert telemetry == [
        (TELEMETRY_NAMESPACE.TRACERS, "otel.metrics_export_attempts", 1, tags),
        (TELEMETRY_NAMESPACE.TRACERS, "otel.metrics_export_successes", 1, tags),
        (TELEMETRY_NAMESPACE.TRACERS, "otel.metrics_export_attempts", 1, tags),
        (TELEMETRY_NAMESPACE.TRACERS, "otel.metrics_export_failures", 1, tags),
    ]


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
