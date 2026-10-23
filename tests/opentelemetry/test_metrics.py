from importlib import import_module
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
import os
import sys
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
def test_otel_metrics_exporter_not_installed_by_default(caplog):
    from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

    set_otel_meter_provider()

    assert ("Install ddtrace[opentelemetry] before enabling OpenTelemetry Metrics support.") in caplog.text

    # The OpenTelemetry exporter dependencies are not installed.
    with pytest.raises(ImportError):
        from opentelemetry.sdk.resources import Resource  # noqa: F401


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess()
def test_otel_metrics_exporter_installed():
    from ddtrace.internal.opentelemetry.exporters import get_metrics_exporter

    grpc_exporter = get_metrics_exporter("grpc")()
    http_exporter = get_metrics_exporter("http/protobuf")()
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
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_SERVICE": "test_service",
        "DD_VERSION": "test_version",
        "DD_ENV": "test_env",
        "OTEL_RESOURCE_ATTRIBUTES": "service.name=tag_service,service.version=tag_version,"
        "deployment.environment.name=tag_env",
    },
)
def test_otel_metrics_resource_uses_canonical_semantic_attributes():
    from ddtrace.internal.opentelemetry.metrics import _build_resource

    resource = _build_resource()
    assert resource is not None
    assert resource.attributes["service.name"] == "test_service"
    assert resource.attributes["service.version"] == "test_version"
    assert resource.attributes["deployment.environment.name"] == "test_env"
    assert not {"service", "version", "env", "deployment.environment"} & resource.attributes.keys()


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "OTEL_RESOURCE_ATTRIBUTES": "service.name=tag_service,service.version=tag_version,"
        "deployment.environment.name=tag_env"
    },
)
def test_otel_metrics_resource_remaps_semantic_tags():
    from ddtrace import config
    from ddtrace.internal.opentelemetry.metrics import _build_resource

    assert config.service == "tag_service"
    assert config.version == "tag_version"
    assert config.env == "tag_env"
    assert config.tags == {"env": "tag_env"}
    resource = _build_resource()
    assert resource is not None
    assert resource.attributes["service.name"] == "tag_service"
    assert resource.attributes["service.version"] == "tag_version"
    assert resource.attributes["deployment.environment.name"] == "tag_env"
    assert not {"service", "version", "env", "deployment.environment"} & resource.attributes.keys()


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

    from ddtrace.internal.opentelemetry.exporters.grpclib_metric_exporter import OTLPMetricExporter

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
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="The lightweight gRPC exporter requires OpenTelemetry 1.18")
@pytest.mark.asyncio
async def test_grpclib_exporter_runs_from_active_event_loop(monkeypatch):
    from opentelemetry.sdk.metrics.export import MetricExportResult

    from ddtrace.internal.opentelemetry.exporters.grpclib_metric_exporter import OTLPMetricExporter

    requests = []

    async def export(request, *, timeout, metadata):
        requests.append(request)

    provider, metrics_data = _metrics_data()
    exporter = OTLPMetricExporter(endpoint="http://127.0.0.1:4317")
    monkeypatch.setattr(exporter, "_method", export)
    try:
        assert exporter.export(metrics_data) is MetricExportResult.SUCCESS
    finally:
        exporter.shutdown()
        provider.shutdown()

    assert len(requests) == 1


@skipif(exporter_not_installed=True)
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="The lightweight gRPC exporter requires OpenTelemetry 1.18")
def test_grpclib_exporter_tls_defaults_and_configuration(monkeypatch):
    from ddtrace.internal.opentelemetry.exporters import grpclib_exporter

    class Context:
        def __init__(self):
            self.alpn_protocols = None
            self.cert_chain = None

        def set_alpn_protocols(self, protocols):
            self.alpn_protocols = protocols

        def load_cert_chain(self, certificate, key):
            self.cert_chain = (certificate, key)

    context = Context()
    monkeypatch.setattr(grpclib_exporter.ssl, "create_default_context", lambda cafile: context)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_CLIENT_CERTIFICATE", "client.crt")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_CLIENT_KEY", "client.key")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_INSECURE", "false")

    assert (
        grpclib_exporter._resolve_insecure("collector.example:4317", None, "OTEL_EXPORTER_OTLP_METRICS_INSECURE")
        is False
    )
    assert grpclib_exporter._build_ssl_context("OTEL_EXPORTER_OTLP_METRICS", None, None, None) is context
    assert context.alpn_protocols == ["h2"]
    assert context.cert_chain == ("client.crt", "client.key")


@skipif(exporter_not_installed=True)
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="The lightweight gRPC exporter requires OpenTelemetry 1.18")
def test_grpclib_exporter_resolves_hosts_without_asyncio_executor(monkeypatch):
    from ddtrace.internal.opentelemetry.exporters import grpclib_exporter
    from ddtrace.internal.opentelemetry.exporters.grpclib_metric_exporter import OTLPMetricExporter

    addresses = [(2, 1, 6, "", ("127.0.0.1", 4317))]
    monkeypatch.setattr(grpclib_exporter.socket, "getaddrinfo", lambda *args, **kwargs: addresses)
    exporter = OTLPMetricExporter(endpoint="http://collector.example:4317")
    try:
        resolved = exporter._loop.run_until_complete(exporter._loop.getaddrinfo("collector.example", 4317))
    finally:
        exporter.shutdown()

    assert resolved == addresses


@skipif(exporter_not_installed=True)
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="The lightweight HTTP exporter requires OpenTelemetry 1.18")
def test_http_exporter_preserves_attribute_types_headers_and_compression(monkeypatch):
    import gzip

    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
    from opentelemetry.sdk.metrics.export import MetricExportResult

    from ddtrace.internal.opentelemetry.exporters.http_metric_exporter import OTLPMetricExporter

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
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="The lightweight HTTP exporter requires OpenTelemetry 1.18")
def test_http_exporter_rejects_redirects(monkeypatch):
    from opentelemetry.sdk.metrics.export import MetricExportResult

    from ddtrace.internal.opentelemetry.exporters.http_metric_exporter import OTLPMetricExporter

    provider, metrics_data = _metrics_data()
    exporter = OTLPMetricExporter(endpoint="http://127.0.0.1:4318")
    monkeypatch.setattr(exporter, "_send", lambda payload, timeout: (302, None))
    try:
        assert exporter.export(metrics_data) is MetricExportResult.FAILURE
    finally:
        exporter.shutdown()
        provider.shutdown()


@skipif(exporter_not_installed=True)
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="The lightweight HTTP exporter requires OpenTelemetry 1.18")
def test_http_exporter_uses_proxy_without_following_redirects(monkeypatch):
    from urllib.request import ProxyHandler

    from ddtrace.internal.opentelemetry.exporters import http_exporter
    from ddtrace.internal.opentelemetry.exporters.http_metric_exporter import OTLPMetricExporter

    monkeypatch.setattr(http_exporter, "getproxies", lambda: {"http": "http://proxy.example.com:8080"})
    monkeypatch.setattr(http_exporter, "proxy_bypass", lambda hostname: False)
    exporter = OTLPMetricExporter(endpoint="http://collector.example.com:4318/v1/metrics")
    try:
        assert exporter._client is None
        assert any(isinstance(handler, ProxyHandler) for handler in exporter._opener.handlers)
        assert any(isinstance(handler, http_exporter._NoRedirectHandler) for handler in exporter._opener.handlers)
    finally:
        exporter.shutdown()


@skipif(exporter_not_installed=True)
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="The lightweight HTTP exporter requires OpenTelemetry 1.18")
def test_http_exporter_uses_signal_mtls_configuration(monkeypatch):
    from ddtrace.internal.opentelemetry.exporters import http_exporter
    from ddtrace.internal.opentelemetry.exporters.http_metric_exporter import OTLPMetricExporter

    class Context:
        cert_chain = None

        def load_cert_chain(self, certificate, key):
            self.cert_chain = (certificate, key)

    context = Context()
    monkeypatch.setattr(http_exporter.ssl, "create_default_context", lambda cafile: context)
    monkeypatch.setattr(http_exporter, "getproxies", lambda: {})
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_CLIENT_CERTIFICATE", "client.crt")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_CLIENT_KEY", "client.key")
    exporter = OTLPMetricExporter(endpoint="https://collector.example:4318/v1/metrics")
    try:
        assert exporter._client is None
        assert context.cert_chain == ("client.crt", "client.key")
    finally:
        exporter.shutdown()


@skipif(exporter_not_installed=True)
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="The lightweight gRPC exporter requires OpenTelemetry 1.18")
def test_grpclib_exporter_uses_otlp_temporality_preference(monkeypatch):
    from opentelemetry.sdk.metrics._internal.instrument import Counter
    from opentelemetry.sdk.metrics.export import AggregationTemporality

    from ddtrace.internal.opentelemetry.exporters.grpclib_metric_exporter import OTLPMetricExporter

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

    from ddtrace.internal.opentelemetry.exporters.grpclib_metric_exporter import OTLPMetricExporter

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
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="The lightweight gRPC exporter requires OpenTelemetry 1.18")
def test_grpclib_exporter_retries_transport_errors(monkeypatch):
    from opentelemetry.sdk.metrics.export import MetricExportResult

    from ddtrace.internal.opentelemetry.exporters import grpclib_exporter
    from ddtrace.internal.opentelemetry.exporters.grpclib_metric_exporter import OTLPMetricExporter

    calls = []

    async def export(request, *, timeout, metadata):
        calls.append(request)
        if len(calls) == 1:
            raise ConnectionRefusedError

    provider, metrics_data = _metrics_data()
    exporter = OTLPMetricExporter(endpoint="http://127.0.0.1:4317", timeout=3)
    monkeypatch.setattr(exporter, "_method", export)
    monkeypatch.setattr(grpclib_exporter, "sleep", lambda delay: None)
    try:
        assert exporter.export(metrics_data) is MetricExportResult.SUCCESS
    finally:
        exporter.shutdown()
        provider.shutdown()

    assert len(calls) == 2


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
        ("grpc", "ddtrace.internal.opentelemetry.exporters.grpclib_metric_exporter"),
        ("http/protobuf", "ddtrace.internal.opentelemetry.exporters.http_metric_exporter"),
    ],
)
@skipif(exporter_not_installed=True)
def test_protocol_selects_exporter(protocol, module):
    from ddtrace.internal.opentelemetry.exporters import get_metrics_exporter

    if EXPORTER_VERSION < (1, 18):
        module = "ddtrace.internal.opentelemetry.exporters.exporter_telemetry"
    assert get_metrics_exporter(protocol).__module__ == module


@pytest.mark.parametrize("protocol", ["grpc", "http/protobuf"])
def test_exporter_resolution_without_proto_package_returns_none(monkeypatch, protocol):
    from ddtrace.internal.opentelemetry import exporters

    def missing_exporter_version(protocol):
        raise ImportError("OpenTelemetry exporter is not installed")

    monkeypatch.setattr(exporters, "_exporter_version", missing_exporter_version)

    assert exporters.get_metrics_exporter(protocol) is None


@pytest.mark.parametrize(
    ("protocol", "distribution"),
    [
        ("grpc", "opentelemetry-exporter-otlp-proto-grpc"),
        ("http/protobuf", "opentelemetry-exporter-otlp-proto-http"),
    ],
)
def test_exporter_version_falls_back_to_protocol_package(monkeypatch, protocol, distribution):
    from ddtrace.internal.opentelemetry import exporters

    requested = []

    def package_version(name):
        requested.append(name)
        if name == "opentelemetry-exporter-otlp-proto-common":
            raise PackageNotFoundError
        return "1.17.0"

    monkeypatch.setattr(exporters, "version", package_version)

    assert exporters._exporter_version(protocol) == "1.17.0"
    assert requested == ["opentelemetry-exporter-otlp-proto-common", distribution]


@skipif(exporter_not_installed=True)
def test_grpc_compression_uses_upstream_exporter(monkeypatch):
    from ddtrace.internal.opentelemetry import exporters

    class UpstreamExporter:
        pass

    imported = []

    def import_exporter(module_name):
        imported.append(module_name)
        return SimpleNamespace(OTLPMetricExporter=UpstreamExporter)

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_COMPRESSION", "gzip")
    monkeypatch.setattr(exporters, "_exporter_version", lambda protocol: "1.41.0")
    monkeypatch.setattr(exporters, "import_module", import_exporter)

    exporter_class = exporters.get_metrics_exporter("grpc")

    assert UpstreamExporter in exporter_class.__mro__
    assert imported == ["opentelemetry.exporter.otlp.proto.grpc.metric_exporter"]


@skipif(exporter_not_installed=True)
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="Test requires the lightweight exporter selection path")
def test_grpc_protocol_without_grpclib_falls_back_to_upstream_exporter(monkeypatch):
    from ddtrace.internal.opentelemetry import exporters

    class UpstreamMetricsExporter:
        pass

    monkeypatch.setitem(sys.modules, "ddtrace.internal.opentelemetry.exporters.grpclib_metric_exporter", None)
    monkeypatch.setattr(
        exporters,
        "import_module",
        lambda name: SimpleNamespace(OTLPMetricExporter=UpstreamMetricsExporter),
    )

    exporter = exporters.get_metrics_exporter("grpc")
    assert exporter is not None
    assert issubclass(exporter, UpstreamMetricsExporter)


@skipif(exporter_not_installed=True)
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="Test requires the lightweight exporter selection path")
def test_grpc_protocol_without_grpclib_or_upstream_exporter_returns_none(monkeypatch):
    from ddtrace.internal.opentelemetry import exporters

    def missing_upstream_exporter(name):
        raise ImportError("OpenTelemetry gRPC exporter is not installed")

    monkeypatch.setitem(sys.modules, "ddtrace.internal.opentelemetry.exporters.grpclib_metric_exporter", None)
    monkeypatch.setattr(exporters, "import_module", missing_upstream_exporter)

    assert exporters.get_metrics_exporter("grpc") is None


@skipif(exporter_not_installed=True)
@pytest.mark.skipif(EXPORTER_VERSION >= (1, 18), reason="Test requires the upstream exporter fallback")
def test_upstream_exporter_fallback_records_telemetry(monkeypatch):
    from opentelemetry.sdk.metrics.export import MetricExportResult

    from ddtrace.internal.opentelemetry.exporters import exporter_telemetry
    from ddtrace.internal.opentelemetry.exporters import get_metrics_exporter

    telemetry = []
    monkeypatch.setattr(
        exporter_telemetry.telemetry_writer,
        "add_count_metric",
        lambda namespace, name, value, tags: telemetry.append((name, value, tags)),
    )

    exporter = get_metrics_exporter("grpc")(endpoint="http://127.0.0.1:4317")
    upstream_exporter = next(
        cls for cls in type(exporter).__mro__ if cls.__module__.startswith("opentelemetry.exporter.otlp.")
    )
    monkeypatch.setattr(upstream_exporter, "export", lambda *args, **kwargs: MetricExportResult.SUCCESS)
    try:
        assert exporter.export(object()) is MetricExportResult.SUCCESS
    finally:
        exporter.shutdown()

    tags = (("protocol", "grpc"), ("encoding", "protobuf"))
    assert telemetry == [
        ("otel.metrics_export_attempts", 1, tags),
        ("otel.metrics_export_successes", 1, tags),
    ]


@pytest.mark.parametrize(
    ("module_name", "protocol"),
    [
        ("ddtrace.internal.opentelemetry.exporters.grpclib_metric_exporter", "grpc"),
        ("ddtrace.internal.opentelemetry.exporters.http_metric_exporter", "http"),
    ],
)
@skipif(exporter_not_installed=True)
@pytest.mark.skipif(EXPORTER_VERSION < (1, 18), reason="The lightweight exporters require OpenTelemetry 1.18")
def test_lightweight_exporter_records_telemetry(monkeypatch, module_name, protocol):
    from opentelemetry.sdk.metrics.export import MetricExportResult

    from ddtrace.internal.opentelemetry.exporters import exporter_telemetry
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


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_METRICS_OTEL_ENABLED": "true",
        "DD_TRACE_WRITER_INTERVAL_SECONDS": "30",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4317",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:14318/v1/traces",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
        "OTEL_TRACES_EXPORTER": "otlp",
    },
    parametrize={"OTEL_EXPORTER_OTLP_PROTOCOL": ["grpc", "http/protobuf"]},
)
def test_otel_metrics_export_does_not_generate_client_spans():
    import os

    from opentelemetry.metrics import get_meter
    from opentelemetry.metrics import get_meter_provider

    from ddtrace.trace import tracer
    from tests.opentelemetry.utils import capture_otlp_metrics
    from tests.opentelemetry.utils import capture_otlp_traces
    from tests.opentelemetry.utils import metric_names

    with (
        capture_otlp_metrics(os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"]) as metric_requests,
        capture_otlp_traces("http/protobuf", port=14318) as trace_requests,
    ):
        get_meter("test.metrics").create_counter("test.metric").add(1)
        meter_provider = get_meter_provider()
        assert meter_provider.force_flush()
        meter_provider.shutdown()
        tracer.flush()

    assert "test.metric" in metric_names(metric_requests)
    assert trace_requests == []
