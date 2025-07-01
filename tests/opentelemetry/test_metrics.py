import os

from opentelemetry import version
import pytest


OTEL_VERSION = tuple(int(x) for x in version.__version__.split(".")[:3])


def skipif(exporter_installed: bool = False, unsupported_otel_version: bool = False):
    if unsupported_otel_version and OTEL_VERSION < (1, 16):
        return pytest.mark.skipif(True, reason="OpenTelemetry version 1.16 or higher is required for these tests")

    is_exporter = os.getenv("OTEL_SDK_EXPORTER_INSTALLED", "").lower() in ("true", "1")
    if exporter_installed:
        return pytest.mark.skipif(is_exporter, reason="Tests not compatible with the opentelemetry exporters")
    else:
        return pytest.mark.skipif(not is_exporter, reason="Tests only compatible with the opentelemetry exporters")


@skipif(exporter_installed=True, unsupported_otel_version=True)
def test_otel_sdk_not_installed_by_default():
    """
    Test that the OpenTelemetry metrics exporter can be set up correctly.
    """
    from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

    # This should not raise an ImportError
    set_otel_meter_provider()

    # If the OpenTelemetry SDK is not installed
    with pytest.raises(ImportError):
        from opentelemetry.sdk.resources import Resource  # noqa: F401


@skipif(unsupported_otel_version=True)
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


@skipif(unsupported_otel_version=True)
@pytest.mark.subprocess(ddtrace_run=True, env={"DD_TRACE_OTEL_METRICS_ENABLED": "true"})
def test_otel_metrics_enabled():
    """
    Test that the OpenTelemetry metrics exporter is automatically configured when DD_TRACE_OTEL_METRICS_ENABLED is set.
    """
    from opentelemetry.metrics import get_meter_provider

    meter_provider = get_meter_provider()
    assert meter_provider, f"OpenTelemetry metrics exporter should be configured automatically."

@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(ddtrace_run=True, parametrize={"DD_TRACE_OTEL_METRICS_ENABLED": [None, "false"]})
def test_otel_metrics_disabled_and_unset():
    """
    Test that the OpenTelemetry metrics exporter is NOT automatically configured when DD_TRACE_OTEL_METRICS_ENABLED is set.
    """
    from opentelemetry.metrics import get_meter_provider

    from ddtrace.internal.opentelemetry.logs import LOGS_PROVIDER_CONFIGURED

    meter_provider = get_meter_provider()
    assert (
        not meter_provider
    ), f"OpenTelemetry mterics exporter should not be configured automatically."
