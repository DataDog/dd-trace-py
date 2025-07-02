import os

from opentelemetry import version

from ddtrace import config
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger


OTEL_VERSION = tuple(int(x) for x in version.__version__.split(".")[:3])


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


def configure_legacy_logger_provider(resource, exporter_class):
    # From: https://github.com/open-telemetry/opentelemetry-python/blob/v1.7.0/docs/examples/logs/example.py
    import logging

    from opentelemetry.sdk._logs import LogEmitterProvider
    from opentelemetry.sdk._logs import OTLPHandler
    from opentelemetry.sdk._logs import set_log_emitter_provider
    from opentelemetry.sdk._logs.export import BatchLogProcessor

    log_emitter_provider = LogEmitterProvider(resource=resource)
    set_log_emitter_provider(log_emitter_provider)

    exporter = exporter_class()
    log_emitter_provider.add_log_processor(BatchLogProcessor(exporter))
    log_emitter = log_emitter_provider.get_log_emitter(__name__, "0.1")
    handler = OTLPHandler(level=logging.NOTSET, log_emitter=log_emitter)
    # Attach OTLP handler to root logger
    logging.getLogger("root").addHandler(handler)


def set_otel_logs_exporter():
    global LOGS_PROVIDER_CONFIGURED
    if LOGS_PROVIDER_CONFIGURED:
        log.debug("OpenTelemetry Logs exporter is already configured, skipping setup.")
        return

    try:
        from opentelemetry.sdk.resources import Resource

        resource_attributes = {
            "host.name": get_hostname(),  # lowest precedence
            **config.tags,  # medium precedence (contains ddtags and otel resource tags)
            "service.name": config.service,  # highest precedence (reference global ddtrace USTs)
            "service.version": config.version,
            "deployment.environment": config.env,
        }
        for k in resource_attributes:
            resource_attributes[k] = str(resource_attributes[k]) if resource_attributes[k] is not None else ""
        resource = Resource.create(resource_attributes)
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
        if OTEL_VERSION >= (1, 8, 0):
            # Introduced in https://github.com/open-telemetry/opentelemetry-python/commit/46f77d0dee69dd9781fd9fae9280a12317b51606#diff-939ca0ff670526b31497ecc0309aecc7f2fb375ae10bb55798b319c2ca1c9e39
            # This helper is used to initialize the OpenTelemetry logging in otel automatic instrumentation.
            from opentelemetry.sdk._configuration import _init_logging

            _init_logging({protocol: OTLPLogExporter}, resource=resource)
        else:
            configure_legacy_logger_provider(resource, OTLPLogExporter)
    except ImportError as e:
        log.warning(
            "The installed version of the OpenTelemetry SDK does not define required component: '%s'. "
            "An OpenTelemetry Exporter will not be automatically configured. Open an issue at "
            "github.com/Datadog/dd-trace-py to report this bug.",
            str(e),
        )
        return
    LOGS_PROVIDER_CONFIGURED = True
