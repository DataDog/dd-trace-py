import os

from ddtrace import config
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


MINIMUM_SUPPORTED_VERSION = (1, 15, 0)


DD_LOGS_PROVIDER_CONFIGURED = False


def should_configure_logs_exporter() -> bool:
    """Check if the OpenTelemetry Logs exporter should be configured."""
    if DD_LOGS_PROVIDER_CONFIGURED:
        log.warning("OpenTelemetry Logs exporter was already configured by ddtrace, skipping setup.")
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


def set_otel_logs_exporter():
    """Set up the OpenTelemetry Logs exporter if not already configured."""
    if not should_configure_logs_exporter():
        return

    resource = _build_resource()
    if resource is None:
        return

    protocol = os.environ.get(
        "OTEL_EXPORTER_OTLP_PROTOCOL", os.environ.get("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", "grpc").lower()
    )
    exporter_class = _import_exporter(protocol)
    if exporter_class is None:
        return

    if not _initialize_logging(exporter_class, protocol, resource):
        return

    global DD_LOGS_PROVIDER_CONFIGURED
    DD_LOGS_PROVIDER_CONFIGURED = True


def _build_resource():
    try:
        from opentelemetry.sdk.resources import Resource

        resource_attributes = {
            "host.name": get_hostname(),
            **config.tags,
            "service.name": config.service,
            "service.version": config.version,
            "deployment.environment": config.env,
        }

        # Convert all attributes to strings, use empty string for None
        resource_attributes = {k: str(v) if v is not None else "" for k, v in resource_attributes.items()}

        return Resource.create(resource_attributes)
    except ImportError:
        log.warning(
            "OpenTelemetry SDK is not installed, opentelemetry logs will not be enabled. "
            "Please install the OpenTelemetry SDK before enabling ddtrace OpenTelemetry Logs support."
        )
        return None


def _import_exporter(protocol):
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

        return OTLPLogExporter

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
    try:
        from opentelemetry.sdk._configuration import _init_logging

        _init_logging({protocol: exporter_class}, resource=resource)
        return True
    except ImportError as e:
        log.warning(
            "The installed OpenTelemetry SDK is missing required components: %s. "
            "Logging exporter not initialized. Please file an issue at github.com/Datadog/dd-trace-py.",
            str(e),
        )
        return False
