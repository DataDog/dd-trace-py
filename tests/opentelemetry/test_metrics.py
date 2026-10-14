from concurrent import futures
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import os
from threading import Thread

from opentelemetry import version
import pytest


OTEL_VERSION = tuple(int(x) for x in version.__version__.split(".")[:3])


def metric_names(requests):
    return {
        metric.name
        for request in requests
        for resource_metrics in request.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }


@contextmanager
def capture_grpc_metrics():
    import grpc
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceResponse
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import MetricsServiceServicer
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import add_MetricsServiceServicer_to_server

    class MetricsService(MetricsServiceServicer):
        def __init__(self):
            self.requests = []

        def Export(self, request, context):
            self.requests.append(request)
            return ExportMetricsServiceResponse()

    service = MetricsService()
    server_factory = getattr(grpc.server, "__wrapped__", grpc.server)
    server = server_factory(futures.ThreadPoolExecutor(max_workers=1))
    add_MetricsServiceServicer_to_server(service, server)
    assert server.add_insecure_port("127.0.0.1:4317") == 4317
    server.start()
    try:
        yield service.requests
    finally:
        server.stop(2).wait()


@contextmanager
def capture_http_metrics():
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

    requests = []

    class MetricsHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = ExportMetricsServiceRequest()
            request.ParseFromString(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format_string, *args):
            pass

    server = HTTPServer(("127.0.0.1", 4317), MetricsHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield requests
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def skipif(
    exporter_installed: bool = False, exporter_not_installed: bool = False, unsupported_otel_version: bool = False
):
    """
    Returns a pytest skip marker based on OpenTelemetry version and exporter installation.
    Parameters:
    - exporter_installed: If True, skip tests that require OpenTelemetry exporters.
    - exporter_not_installed: If True, skip tests that do not require OpenTelemetry exporters.
    - unsupported_otel_version: If True, skip tests that require OpenTelemetry version 1.12 or higher.
      - v1.12.0 is the first version that exposes metrics in the public API
    """
    if unsupported_otel_version and OTEL_VERSION < (1, 12):
        return pytest.mark.skipif(True, reason="OpenTelemetry version 1.12 or higher is required for these tests")

    has_exporter = os.getenv("SDK_EXPORTER_INSTALLED", "").lower() in ("true", "1")
    if exporter_installed and has_exporter:
        return pytest.mark.skipif(True, reason="Tests not compatible with the opentelemetry exporters")
    elif exporter_not_installed and not has_exporter:
        return pytest.mark.skipif(True, reason="Tests only compatible with the opentelemetry exporters")
    return pytest.mark.skipif(False, reason="No skip condition met for OpenTelemetry logs exporter tests")


@skipif(exporter_installed=True, unsupported_otel_version=True)
def test_otel_metrics_sdk_not_installed_by_default():
    """
    Test that the OpenTelemetry metrics exporter can be set up correctly.
    """
    from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

    # This should not raise an ImportError
    set_otel_meter_provider()

    # If the OpenTelemetry SDK is not installed
    with pytest.raises(ImportError):
        from opentelemetry.sdk.resources import Resource  # noqa: F401


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess()
def test_otel_metrics_exporter_installed():
    """
    Test that the OpenTelemetry metrics exporter can be set up correctly.
    """
    from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

    # This should not raise an ImportError
    set_otel_meter_provider()

    # Check if the GRPC/protobuf exporter is available
    try:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

        assert OTLPMetricExporter() is not None
    except ImportError:
        pytest.fail("OTLPMetricExporter for gRPC protobuf should be available")

    # Check if HTTP/protobuf exporter is available
    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        assert OTLPMetricExporter() is not None
    except ImportError:
        pytest.fail("OTLPMetricExporter for HTTP/protobuf should be available")


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


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_METRICS_OTEL_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4317",
    },
    parametrize={"OTEL_EXPORTER_OTLP_PROTOCOL": ["grpc", "http/protobuf"]},
)
def test_otel_metrics_export_does_not_generate_client_spans():
    import os

    from opentelemetry.metrics import get_meter
    from opentelemetry.metrics import get_meter_provider

    from ddtrace.trace import tracer
    from tests.opentelemetry.test_metrics import capture_grpc_metrics
    from tests.opentelemetry.test_metrics import capture_http_metrics
    from tests.opentelemetry.test_metrics import metric_names
    from tests.utils import DummyWriter

    tracer._span_aggregator.writer = DummyWriter()
    capture_metrics = (
        capture_grpc_metrics if os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] == "grpc" else capture_http_metrics
    )

    with capture_metrics() as requests:
        get_meter("test.metrics").create_counter("test.metric").add(1)
        assert get_meter_provider().force_flush()

    assert "test.metric" in metric_names(requests)
    assert tracer._span_aggregator.writer.pop() == []
