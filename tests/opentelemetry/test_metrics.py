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
@pytest.mark.subprocess(ddtrace_run=True, parametrize={"DD_METRICS_OTEL_ENABLED": [None, "false"]})
def test_otel_metrics_disabled_and_unset():
    """
    Test that the OpenTelemetry metrics exporter is NOT automatically configured when DD_METRICS_OTEL_ENABLED is set.
    """
    from opentelemetry.metrics import get_meter_provider

    meter_provider = get_meter_provider()
    assert (meter_provider is None) or (
        type(meter_provider).__name__ == "_ProxyMeterProvider"
    ), "OpenTelemetry mterics exporter should not be configured automatically."
