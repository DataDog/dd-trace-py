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

DEFAULT_PROTOCOL = "grpc"
DD_LOGS_PROVIDER_CONFIGURED = False
GRPC_PORT = 4317
HTTP_PORT = 4318
HTTP_LOGS_ENDPOINT = "/v1/logs"


def set_otel_logs_provider() -> None:
    """Set up the OpenTelemetry Logs exporter if not already configured."""
    if not _should_configure_logs_exporter():
        return

    resource = _build_resource()
    if resource is None:
        return

    protocol = otel_config.exporter.LOGS_PROTOCOL
    exporter_class = _import_exporter(protocol)
    if exporter_class is None:
        return

    if not _initialize_logging(exporter_class, protocol, resource):
        return

    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "logging_provider_configured", 1, (("type", "dd"),))
    global DD_LOGS_PROVIDER_CONFIGURED
    DD_LOGS_PROVIDER_CONFIGURED = True
    # Disable log injection to prevent duplicate log attributes from being sent.
    config._logs_injection = False


def _should_configure_logs_exporter() -> bool:
    """Check if the OpenTelemetry Logs exporter should be configured."""
    if DD_LOGS_PROVIDER_CONFIGURED:
        log.warning("OpenTelemetry Logs exporter was already configured by ddtrace, skipping setup.")
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
        from opentelemetry._logs._internal import _LOGGER_PROVIDER as logger_provider

        if logger_provider is not None:
            log.warning(
                "OpenTelemetry Logs provider was configured before ddtrace instrumentation was applied, skipping setup."
            )
            return False
    except ImportError as e:
        log.warning(
            "OpenTelemetry Logs support is not available: %s.",
            str(e),
        )
        return False

    log.debug("OpenTelemetry Logs exporter is not configured, proceeding with ddtrace setup.")
    return True


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
            "OpenTelemetry SDK is not installed, opentelemetry logs will not be enabled. "
            "Please install the OpenTelemetry SDK before enabling ddtrace OpenTelemetry Logs support."
        )
        return None


def _dd_logs_exporter(otel_exporter: Type[Any], protocol: str, encoding: str) -> Type[Any]:
    """Create a custom OpenTelemetry Logs exporter that adds telemetry metrics and debug logs."""

    class DDLogsExporter(otel_exporter):
        """A custom OpenTelemetry Logs exporter that adds telemetry metrics and debug logs."""

        def export(self, batch: Any, *args: Any, **kwargs: Any) -> Any:
            """Export logs and queues telemetry metrics."""
            telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.TRACERS,
                "otel.log_records",
                len(batch),
                (
                    ("protocol", protocol),
                    ("encoding", encoding),
                ),
            )
            log.debug(
                "Exporting %d OpenTelemetry Logs with %s protocol and %s encoding", len(batch), protocol, encoding
            )
            return super().export(batch, *args, **kwargs)

    return DDLogsExporter


def _import_exporter(protocol):
    """Import the appropriate OpenTelemetry Logs exporter based on the set protocol"""
    try:
        if protocol == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
            from opentelemetry.exporter.otlp.proto.grpc.version import __version__ as exporter_version
        elif protocol == "http/protobuf":
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
            from opentelemetry.exporter.otlp.proto.http.version import __version__ as exporter_version
        else:
            log.warning(
                "OpenTelemetry Logs exporter protocol '%s' is not supported. " "Use 'grpc' or 'http/protobuf'.",
                protocol,
            )
            return None

        if tuple(int(x) for x in exporter_version.split(".")[:3]) < MINIMUM_SUPPORTED_VERSION:
            log.warning(
                "OpenTelemetry Logs exporter for %s requires version %r or higher, but found version %r. "
                "Please upgrade the appropriate opentelemetry-exporter package.",
                protocol,
                MINIMUM_SUPPORTED_VERSION,
                exporter_version,
            )
            return None

        return _dd_logs_exporter(OTLPLogExporter, protocol, "protobuf")

    except ImportError as e:
        log.warning(
            "OpenTelemetry Logs exporter for %s is not available. "
            "Please install a supported package (ex: opentelemetry-exporter-otlp-proto-%s): %s",
            protocol,
            "grpc" if protocol == "grpc" else "http",
            str(e),
        )
        return None


def _initialize_logging(exporter_class, protocol, resource):
    """Configures and sets up the OpenTelemetry Logs exporter."""
    try:
        from opentelemetry.sdk._configuration import _init_logging

        # Ensure logging exporter is configured to send payloads to a Datadog Agent.
        # The default endpoint is resolved using the hostname from DD_AGENT.. and DD_TRACE_AGENT_... configs
        os.environ["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] = otel_config.exporter.LOGS_ENDPOINT
        _init_logging({protocol: exporter_class}, resource=resource)
        return True
    except ImportError as e:
        log.warning(
            "The installed OpenTelemetry SDK is missing required components: %s. "
            "Logging exporter not initialized. Please file an issue at github.com/Datadog/dd-trace-py.",
            str(e),
        )
        return False
