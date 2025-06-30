from ddtrace import config
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


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


def set_otel_sdk_logger_provider():
    """
    Sets the OpenTelemetry logger provider if it is not already set.
    This function is intended to be called during application startup.
    """
    logger_provider = get_configured_logger_provider()

    if logger_provider is not None:
        log.debug(
            "OpenTelemetry Logger Provider already set: %s, skipping initialization. "
            "Datadog attributes will not be applied to LogRecords",
            type(logger_provider),
        )
        return

    try:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk.resources import Resource

        logger_provider = LoggerProvider(
            resource=Resource.create(
                {
                    "service.name": config.service,
                    "service.version": config.version,
                    "deployment.environment": config.env,
                    "host.name": get_hostname(),
                }
            ),
        )
    except ImportError:
        log.warning(
            "OpenTelemetry SDK is not installed, opentelemetry logs will not be enabled. "
            "Please install the OpenTelemetry SDK before enabling ddtrace OpenTelemetry "
            "Logs support (ex: via DD_OTEL_LOGS_ENABLED).",
        )
    else:
        set_logger_provider(logger_provider)
