from importlib.metadata import PackageNotFoundError
from importlib.metadata import version
import logging
from typing import Any
from typing import Optional

import opentelemetry.version

from ddtrace import config
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.settings._agentless import config as agentless_config
from ddtrace.internal.settings._opentelemetry import otel_config


log = get_logger(__name__)

MINIMUM_SUPPORTED_VERSION = (1, 15, 0)
API_VERSION = tuple(int(x) for x in opentelemetry.version.__version__.split(".")[:3])

DEFAULT_PROTOCOL = "grpc"
DD_LOGS_PROVIDER_CONFIGURED = False


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


def _import_exporter(protocol):
    """Import the appropriate OpenTelemetry Logs exporter based on the set protocol"""
    try:
        exporter: type[Any]
        exporter_version = _exporter_version()
        if protocol == "grpc":
            if tuple(int(x) for x in exporter_version.split(".")[:3]) >= (1, 18, 0):
                try:
                    from ddtrace.internal.opentelemetry.grpclib_log_exporter import OTLPLogExporter as GRPCLogExporter

                    exporter = GRPCLogExporter
                except ImportError:
                    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
                        OTLPLogExporter as UpstreamGRPCLogExporter,
                    )

                    exporter = UpstreamGRPCLogExporter
            else:
                from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
                    OTLPLogExporter as LegacyGRPCLogExporter,
                )

                exporter = LegacyGRPCLogExporter
        elif protocol == "http/protobuf":
            if tuple(int(x) for x in exporter_version.split(".")[:3]) >= (1, 18, 0):
                from ddtrace.internal.opentelemetry.http_log_exporter import (
                    OTLPLogExporter as LightweightHTTPLogExporter,
                )

                exporter = LightweightHTTPLogExporter
            else:
                from opentelemetry.exporter.otlp.proto.http._log_exporter import (
                    OTLPLogExporter as UpstreamHTTPLogExporter,
                )

                exporter = UpstreamHTTPLogExporter
        else:
            log.warning(
                "OpenTelemetry Logs exporter protocol '%s' is not supported. Use 'grpc' or 'http/protobuf'.",
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

        return exporter

    except ImportError as e:
        log.warning(
            "OpenTelemetry Logs exporter for %s is not available. "
            "Install ddtrace[opentelemetry] before enabling OpenTelemetry Logs support: %s",
            protocol,
            str(e),
        )
        return None


def _exporter_version() -> str:
    try:
        return version("opentelemetry-exporter-otlp-proto-common")
    except PackageNotFoundError:
        from opentelemetry.exporter.otlp.proto.http.version import __version__

        return str(__version__)


class _SelfTelemetryLogFilter(logging.Filter):
    """Reject log records emitted by ddtrace's own loggers and by the OpenTelemetry SDK/exporter loggers.

    ``_init_logging`` attaches a ``LoggingHandler`` to the root logger that captures every log record
    that propagates to root. Without this filter, the tracer's own telemetry-pipeline logs (``ddtrace.*``,
    e.g. the per-batch export debug line) and the OpenTelemetry exporter's logs (``opentelemetry.*``,
    e.g. export-failure warnings/errors) would be captured, exported, and -- because exporting emits
    further log records -- captured again, producing a self-amplifying export loop. A tracer must never
    feed its own telemetry-pipeline logs back into its own telemetry export.
    """

    _EXCLUDED_NAMESPACES = ("ddtrace", "opentelemetry")

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        return not any(name == namespace or name.startswith(namespace + ".") for namespace in self._EXCLUDED_NAMESPACES)


def _exclude_self_telemetry_from_otlp_handler(preexisting_handler_ids: set[int]) -> None:
    """Attach the self-telemetry filter to the OTLP logs handler that ``_init_logging`` just added.

    Only the newly added handler is filtered (found by diffing against ``preexisting_handler_ids``),
    leaving application-configured handlers untouched. The filter binds to the handler instance, so it
    persists when the OpenTelemetry SDK re-adds that same instance after ``logging.basicConfig`` (the
    one reconfiguration API the SDK patches; ``dictConfig``/``fileConfig`` are not).
    """
    try:
        from opentelemetry.sdk._logs import LoggingHandler
    except ImportError:
        return

    telemetry_filter = _SelfTelemetryLogFilter()
    for handler in logging.getLogger().handlers:
        if isinstance(handler, LoggingHandler) and id(handler) not in preexisting_handler_ids:
            handler.addFilter(telemetry_filter)


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


def _initialize_logging(exporter_class, protocol, resource):
    """Configures and sets up the OpenTelemetry Logs exporter."""
    try:
        from opentelemetry.sdk._configuration import _init_logging

        # Ensure logs exporter is configured to send payloads to a Datadog Agent.
        _prepare_agentless_export(
            "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "OTEL_EXPORTER_OTLP_LOGS_HEADERS", protocol, "logs"
        )
        if "OTEL_EXPORTER_OTLP_ENDPOINT" not in env and "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT" not in env:
            env["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] = otel_config.exporter.LOGS_ENDPOINT
        preexisting_handler_ids = {id(handler) for handler in logging.getLogger().handlers}
        _init_logging({protocol: exporter_class}, resource=resource)
        # Stop the OTLP logs handler ddtrace just installed from capturing and re-exporting ddtrace's
        # own log records (and the OpenTelemetry exporter's), which would create a self-amplifying loop.
        _exclude_self_telemetry_from_otlp_handler(preexisting_handler_ids)
        return True
    except ImportError as e:
        log.warning(
            "The installed OpenTelemetry SDK is missing required components: %s. "
            "Logging exporter not initialized. Please file an issue at github.com/Datadog/dd-trace-py.",
            str(e),
        )
        return False
