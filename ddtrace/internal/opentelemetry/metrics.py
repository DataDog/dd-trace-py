import opentelemetry.version

from ddtrace import config
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._opentelemetry import otel_config


log = get_logger(__name__)

MINIMUM_SUPPORTED_VERSION = (1, 15, 0)
API_VERSION = tuple(int(x) for x in opentelemetry.version.__version__.split(".")[:3])
SUPPORTED_PROTOCOLS = ("grpc", "http/protobuf")

DD_METRICS_PROVIDER_CONFIGURED = False


def set_otel_meter_provider():
    """Install a native (libdatadog-backed) OpenTelemetry MeterProvider if not already configured.

    ddtrace no longer depends on the opentelemetry-sdk for metrics: the MeterProvider installed
    here forwards every instrument call into libdatadog's native aggregator, which owns the OTLP
    export. Only the lightweight opentelemetry-api package is required.
    """
    if not _should_configure_metrics_exporter():
        return

    protocol = otel_config.exporter.METRICS_PROTOCOL
    if protocol not in SUPPORTED_PROTOCOLS:
        log.warning(
            "OpenTelemetry Metrics exporter protocol '%s' is not supported. Use 'grpc' or 'http/protobuf'.",
            protocol,
        )
        return

    try:
        from opentelemetry.metrics import set_meter_provider

        from ddtrace.internal.opentelemetry._native_metrics_provider import build_native_meter_provider

        provider = build_native_meter_provider(
            resource_attributes=_build_resource_attributes(),
            endpoint=otel_config.exporter.METRICS_ENDPOINT,
            protocol=protocol,
            timeout_ms=otel_config.exporter.METRICS_TIMEOUT,
            headers=otel_config.exporter.METRICS_HEADERS,
            temporality=otel_config.exporter.METRICS_TEMPORALITY_PREFERENCE,
            export_interval_ms=otel_config.exporter.METRICS_METRIC_READER_EXPORT_INTERVAL,
        )
        set_meter_provider(provider)
    except Exception:
        log.warning("Failed to configure the native OpenTelemetry Metrics provider.", exc_info=True)
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
            "OpenTelemetry API requires version %s or higher to enable metrics collection. Found version %s. "
            "Please upgrade the opentelemetry-api package before enabling ddtrace OpenTelemetry Metrics support.",
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
def _build_resource_attributes() -> dict[str, str]:
    """Build OpenTelemetry resource attributes from DD_TAGS, DD_SERVICE/ENV/VERSION, and hostname."""
    resource_attributes = {
        **config.tags,
        "service.name": config.service,
        "service.version": config.version,
        "deployment.environment": config.env,
    }

    if config._report_hostname and "host.name" not in resource_attributes:
        resource_attributes["host.name"] = get_hostname()

    return {k: str(v) if v is not None else "" for k, v in resource_attributes.items()}
