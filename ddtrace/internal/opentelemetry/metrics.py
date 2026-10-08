from typing import Any
from typing import Optional

import opentelemetry.version

from ddtrace import config
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.opentelemetry.exporters import get_metrics_exporter
from ddtrace.internal.settings import env
from ddtrace.internal.settings._agentless import config as agentless_config
from ddtrace.internal.settings._opentelemetry import otel_config


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
    exporter_class = get_metrics_exporter(protocol)
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
def _build_resource() -> Optional[Any]:
    """Build an OpenTelemetry Resource using DD_TAGS and OTEL_RESOURCE_ATTRIBUTES."""
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.util.types import AttributeValue

        resource_attributes: dict[str, AttributeValue | None] = {
            **config.tags,
            "service.name": config.service,
            "service.version": config.version,
            "deployment.environment": config.env,
        }

        if config._report_hostname and "host.name" not in resource_attributes:
            resource_attributes["host.name"] = get_hostname()

        return Resource.create({key: value for key, value in resource_attributes.items() if value is not None})
    except ImportError:
        log.warning(
            "OpenTelemetry SDK is not installed, opentelemetry metrics will not be enabled. "
            "Install ddtrace[opentelemetry] before enabling OpenTelemetry Metrics support."
        )
        return None


def _prepare_agentless_export(endpoint_env_var: str, headers_env_var: str, protocol: str, signal: str) -> None:
    """Set up a direct-to-intake OTLP export, or warn when it cannot work."""
    if not agentless_config.enabled:
        return
    if env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or env.get(endpoint_env_var):
        return

    if protocol.lower() not in ("http/json", "http/protobuf"):
        log.warning(
            "Agentless mode exports OpenTelemetry %s to the Datadog OTLP intake over HTTP, but the "
            "%r protocol is configured. Set OTEL_EXPORTER_OTLP_PROTOCOL to http/protobuf, or point "
            "OTEL_EXPORTER_OTLP_ENDPOINT at a collector that speaks %r.",
            signal,
            protocol,
            protocol,
        )

    if not agentless_config.api_key:
        return
    # Signal-specific headers win over the global ones, so carry those over rather than drop them.
    configured = env.get(headers_env_var) or env.get("OTEL_EXPORTER_OTLP_HEADERS") or ""
    if "dd-api-key" in configured.lower():
        return
    api_key_header = f"dd-api-key={agentless_config.api_key}"
    env[headers_env_var] = f"{configured},{api_key_header}" if configured else api_key_header


def _initialize_metrics(exporter_class, protocol, resource):
    """Configures and sets up the OpenTelemetry Metrics exporter."""
    try:
        from opentelemetry.sdk._configuration import _init_metrics

        # Ensure metrics exporter is configured to send payloads to a Datadog Agent.
        _prepare_agentless_export(
            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "OTEL_EXPORTER_OTLP_METRICS_HEADERS", protocol, "metrics"
        )
        if "OTEL_EXPORTER_OTLP_ENDPOINT" not in env and "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT" not in env:
            env["OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"] = otel_config.exporter.METRICS_ENDPOINT
        env["OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"] = otel_config.exporter.METRICS_TEMPORALITY_PREFERENCE
        env["OTEL_METRIC_EXPORT_INTERVAL"] = str(otel_config.exporter.METRICS_METRIC_READER_EXPORT_INTERVAL)
        env["OTEL_METRIC_EXPORT_TIMEOUT"] = str(otel_config.exporter.METRICS_METRIC_READER_EXPORT_TIMEOUT)
        _init_metrics({protocol: exporter_class}, resource=resource)
        return True
    except ImportError as e:
        log.warning(
            "The installed OpenTelemetry SDK is missing required components: %s. "
            "Metrics exporter not initialized. Please file an issue at github.com/Datadog/dd-trace-py.",
            str(e),
        )
        return False
