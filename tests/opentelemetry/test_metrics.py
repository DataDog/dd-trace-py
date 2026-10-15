import os

from opentelemetry import version
import pytest


OTEL_VERSION = tuple(int(x) for x in version.__version__.split(".")[:3])


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
        assert get_meter_provider().force_flush()
        tracer.flush()

    assert "test.metric" in metric_names(metric_requests)
    assert trace_requests == []
