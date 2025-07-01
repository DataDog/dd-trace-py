import os

from ddtrace import config
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


LOGS_PROVIDER_CONFIGURED = False


def get_configured_logger_provider():
    """
    Returns the configured OpenTelemetry logger provider, or None if not available.
    """
    try:
        from opentelemetry._logs_internal import _LOGGER_PROVIDER as logger_provider
    except ImportError as e:
        log.warning(
            "OpenTelemetry Logs support is not available: %s. " "Please install opentelemetry-api>=1.15.0",
            str(e),
        )
        return None
    return logger_provider


def set_otel_logs_exporter():
    global LOGS_PROVIDER_CONFIGURED
    if LOGS_PROVIDER_CONFIGURED:
        log.debug("OpenTelemetry Logs exporter is already configured, skipping setup.")
        return

    try:
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create(
            {
                "service.name": config.service or "",
                "service.version": config.version or "",
                "deployment.environment": config.env or "",
                "host.name": get_hostname(),
            }
        )
    except ImportError:
        log.warning(
            "OpenTelemetry SDK is not installed, opentelemetry logs will not be enabled. "
            "Please install the OpenTelemetry SDK before enabling ddtrace OpenTelemetry "
            "Logs support (ex: via DD_OTEL_LOGS_ENABLED).",
        )
        return

    protocol = os.environ.get(
        "OTEL_EXPORTER_OTLP_PROTOCOL", os.environ.get("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", "grpc").lower()
    )
    if "grpc" == protocol:
        try:
            from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        except ImportError:
            return
    elif "http/protobuf" == protocol:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    else:
        log.warning(
            "OpenTelemetry Logs exporter protocol '%s' is not supported. "
            "Please use OTLP gRPC or HTTP/Protobuf exporter instead. "
            "Set OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=grpc or OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=http/protobuf",
            protocol,
        )
        return

    try:
        from opentelemetry.sdk._configuration import _init_logging

        _init_logging({protocol: OTLPLogExporter}, resource=resource)
    except ImportError as e:
        log.warning(
            "The installed version of the OpenTelemetry SDK does not define required component: '%s'. "
            "An OpenTelemetry Exporter will not be automatically configured. Open an issue at "
            "github.com/Datadog/dd-trace-py to report this bug.",
            str(e),
        )
        return
    LOGS_PROVIDER_CONFIGURED = True
