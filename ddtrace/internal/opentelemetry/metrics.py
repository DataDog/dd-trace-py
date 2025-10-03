import os
from typing import Any
from typing import Optional
from typing import Type

import opentelemetry.version

from ddtrace import config
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.settings._opentelemetry import otel_config


log = get_logger(__name__)


MINIMUM_SUPPORTED_VERSION = (1, 15, 0)
API_VERSION = tuple(int(x) for x in opentelemetry.version.__version__.split(".")[:3])

DD_METRICS_PROVIDER_CONFIGURED = False


def set_otel_meter_provider():
    """Set up the OpenTelemetry Metrics exporter if not already configured."""
    if not _should_configure_metrics_exporter():
        return

    resource = _build_resource()
    if resource is None:
        return

    protocol = otel_config.exporter.METRICS_PROTOCOL
    exporter_class = _import_exporter(protocol)
    if exporter_class is None:
        return

    if not _initialize_metrics(exporter_class, protocol, resource):
        return

    global DD_METRICS_PROVIDER_CONFIGURED
    DD_METRICS_PROVIDER_CONFIGURED = True


def _should_configure_metrics_exporter() -> bool:
    """Check if the OpenTelemetry Metrics exporter should be configured."""
    if DD_METRICS_PROVIDER_CONFIGURED:
        log.warning("OpenTelemetry Metrics exporter was already configured by ddtrace, skipping setup.")
        return False

    if API_VERSION < MINIMUM_SUPPORTED_VERSION:
        log.warning(
            "OpenTelemetry API requires version %s or higher to enable logs collection. Found version %s. "
            "Please upgrade the opentelemetry-api package before enabling ddtrace OpenTelemetry Logs support.",
            ".".join(str(x) for x in MINIMUM_SUPPORTED_VERSION),
            ".".join(str(x) for x in API_VERSION),
        )
        return False

    try:
        from opentelemetry.metrics._internal import _METER_PROVIDER as meter_provider

        if meter_provider is not None:
            log.warning("OpenTelemetry Metrics provider was configured before ddtrace setup, skipping setup.")
            return False
    except ImportError as e:
        log.warning(
            "OpenTelemetry Metrics support is not available: %s.",
            str(e),
        )
        return False

    log.debug("OpenTelemetry Metrics exporter is not configured, proceeding with ddtrace setup.")
    return True


# TODO: We should build one set of resource attributes for both logs and metrics.
def _build_resource() -> Optional[Any]:
    """Build an OpenTelemetry Resource using DD_TAGS and OTEL_RESOURCE_ATTRIBUTES."""
    try:
        from opentelemetry.sdk.resources import Resource

        resource_attributes = {
            **config.tags,
            "service.name": config.service,
            "service.version": config.version,
            "deployment.environment": config.env,
        }

        if config._report_hostname and "host.name" not in resource_attributes:
            resource_attributes["host.name"] = get_hostname()

        resource_attributes = {k: str(v) if v is not None else "" for k, v in resource_attributes.items()}

        return Resource.create(resource_attributes)
    except ImportError:
        log.warning(
            "OpenTelemetry SDK is not installed, opentelemetry metrics will not be enabled. "
            "Please install the OpenTelemetry SDK before enabling ddtrace OpenTelemetry Metrics support."
        )
        return None


def _dd_metrics_exporter(otel_exporter: Type[Any], protocol: str, encoding: str) -> Type[Any]:
    """Create a custom OpenTelemetry Metrics exporter that adds telemetry metrics and debug logs."""

    class DDMetricsExporter(otel_exporter):
        """A custom OpenTelemetry Metrics exporter that adds telemetry metrics and debug logs."""

        def export(self, metrics_data: Any, timeout_millis: Any, *args: Any, **kwargs: Any) -> Any:
            """Export metrics and queues telemetry metrics."""
            telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.TRACERS,
                "otel.metrics_export_attempts",
                1,
                (
                    ("protocol", protocol),
                    ("encoding", encoding),
                ),
            )
            # TODO: Count the number of unique metrics streams in this export
            log.debug("Exporting OpenTelemetry Metrics with %s protocol and %s encoding", protocol, encoding)
            result = super().export(metrics_data, timeout_millis, *args, **kwargs)

            if result.value == 0 or result.value == 1:
                telemetry_writer.add_count_metric(
                    TELEMETRY_NAMESPACE.TRACERS,
                    "otel.metrics_export_successes" if result.value == 0 else "otel.metrics_export_failures",
                    1,
                    (
                        ("protocol", protocol),
                        ("encoding", encoding),
                    ),
                )

            return result

    return DDMetricsExporter


def _import_exporter(protocol):
    """Import the appropriate OpenTelemetry Metrics exporter based on the set protocol"""
    try:
        if protocol == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.grpc.version import __version__ as exporter_version
        elif protocol == "http/protobuf":
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.http.version import __version__ as exporter_version
        else:
            log.warning(
                "OpenTelemetry Metrics exporter protocol '%s' is not supported. " "Use 'grpc' or 'http/protobuf'.",
                protocol,
            )
            return None

        if tuple(int(x) for x in exporter_version.split(".")[:3]) < MINIMUM_SUPPORTED_VERSION:
            log.warning(
                "OpenTelemetry Metrics exporter for %s requires version %r or higher, but found version %r. "
                "Please upgrade the appropriate opentelemetry-exporter package.",
                protocol,
                MINIMUM_SUPPORTED_VERSION,
                exporter_version,
            )
            return None

        protocol_name = "grpc" if protocol == "grpc" else "http"
        return _dd_metrics_exporter(OTLPMetricExporter, protocol_name, "protobuf")

    except ImportError as e:
        log.warning(
            "OpenTelemetry Metrics exporter for %s is not available. "
            "Please install a supported package (ex: opentelemetry-exporter-otlp-proto-%s): %s",
            protocol,
            "grpc" if protocol == "grpc" else "http",
            str(e),
        )
        return None


def _initialize_metrics(exporter_class, protocol, resource):
    """Configures and sets up the OpenTelemetry Metrics exporter."""
    try:
        from opentelemetry.sdk._configuration import _init_metrics

        # Ensure metrics exporter is configured to send payloads to a Datadog Agent.
        # The default endpoint is resolved using the hostname from DD_AGENT.. and DD_TRACE_AGENT_... configs
        os.environ["OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"] = otel_config.exporter.METRICS_ENDPOINT
        os.environ[
            "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"
        ] = otel_config.exporter.METRICS_TEMPORALITY_PREFERENCE
        os.environ["OTEL_METRIC_EXPORT_INTERVAL"] = str(otel_config.exporter.METRICS_METRIC_READER_EXPORT_INTERVAL)
        os.environ["OTEL_METRIC_EXPORT_TIMEOUT"] = str(otel_config.exporter.METRICS_METRIC_READER_EXPORT_TIMEOUT)
        _init_metrics({protocol: exporter_class}, resource=resource)
        return True
    except ImportError as e:
        log.warning(
            "The installed OpenTelemetry SDK is missing required components: %s. "
            "Metrics exporter not initialized. Please file an issue at github.com/Datadog/dd-trace-py.",
            str(e),
        )
        return False
