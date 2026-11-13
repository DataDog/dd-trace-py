import typing as t

from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.settings import env
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings._agent import get_agent_hostname
from ddtrace.internal.settings._agentless import config as agentless_config
from ddtrace.internal.settings._config import config as tracer_config
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.telemetry import get_config
from ddtrace.internal.telemetry import report_configuration


_HTTP_PROTOCOLS = ("http/json", "http/protobuf")


def _is_http(protocol: str) -> bool:
    return protocol.lower() in _HTTP_PROTOCOLS


def _agent_endpoint(protocol: str, signal_path: str = "") -> str:
    """The OTLP receiver of the Datadog Agent: HTTP on port 4318 under the signal path, or gRPC on port 4317."""
    if _is_http(protocol):
        return f"http://{get_agent_hostname()}:4318{signal_path}"
    return f"http://{get_agent_hostname()}:4317"


def _targets_agentless_intake(signal_endpoint_env_var: str = "") -> bool:
    """Whether a signal is exported to the Datadog OTLP intake: in agentless mode, when no endpoint is set for it."""
    if not agentless_config.enabled or env.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    return not (signal_endpoint_env_var and env.get(signal_endpoint_env_var))


def _with_intake_auth(configured: str, signal_endpoint_env_var: str) -> str:
    if not agentless_config.api_key or not _targets_agentless_intake(signal_endpoint_env_var):
        return configured
    if "dd-api-key" in configured.lower():
        return configured
    api_key_header = f"dd-api-key={agentless_config.api_key}"
    return f"{configured},{api_key_header}" if configured else api_key_header


def _default_endpoint(protocol: str, signal_path: str, agentless_intake: bool = True) -> str:
    """The endpoint of a signal that has no endpoint of its own.

    As in the OpenTelemetry SDK exporters, HTTP appends the signal path to the global endpoint, and gRPC uses it as-is.
    Without a global endpoint, the signal goes to the Datadog OTLP intake in agentless mode, when agentless_intake
    allows it, and to the Datadog Agent otherwise.
    """
    if global_endpoint := env.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        base = global_endpoint.rstrip("/")
        return base + signal_path if _is_http(protocol) else base
    if agentless_intake and agentless_config.enabled:
        return f"https://otlp.{agentless_config.site}{signal_path}"
    return _agent_endpoint(protocol, signal_path)


def _derive_endpoint(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_ENDPOINT", _agent_endpoint(config.PROTOCOL))


def _derive_logs_endpoint(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", _default_endpoint(config.LOGS_PROTOCOL, config.LOGS_PATH))


def _derive_logs_protocol(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", config.PROTOCOL)


def _derive_logs_headers(config: "ExporterConfig"):
    return _with_intake_auth(
        get_config("OTEL_EXPORTER_OTLP_LOGS_HEADERS", config.HEADERS), "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"
    )


def _derive_logs_timeout(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_LOGS_TIMEOUT", config.DEFAULT_TIMEOUT, int)


def _derive_metrics_endpoint(config: "ExporterConfig"):
    return get_config(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", _default_endpoint(config.METRICS_PROTOCOL, config.METRICS_PATH)
    )


def _derive_metrics_protocol(config: "ExporterConfig"):
    return get_config(
        ["OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", "OTEL_EXPORTER_OTLP_PROTOCOL"],
        config.PROTOCOL,
    )


def _derive_metrics_headers(config: "ExporterConfig"):
    return _with_intake_auth(
        get_config("OTEL_EXPORTER_OTLP_METRICS_HEADERS", config.HEADERS), "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"
    )


def _derive_metrics_timeout(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_METRICS_TIMEOUT", config.DEFAULT_TIMEOUT, int)


def _derive_metrics_temporality_preference(config: "ExporterConfig"):
    return get_config(
        "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE", config.DEFAULT_METRICS_TEMPORALITY_PREFERENCE
    )


def _derive_metrics_metric_reader_export_interval(config: "ExporterConfig"):
    return get_config("OTEL_METRIC_EXPORT_INTERVAL", config.DEFAULT_METRICS_METRIC_READER_EXPORT_INTERVAL, int)


def _derive_metrics_metric_reader_export_timeout(config: "ExporterConfig"):
    return get_config("OTEL_METRIC_EXPORT_TIMEOUT", config.DEFAULT_METRICS_METRIC_READER_EXPORT_TIMEOUT, int)


def _derive_traces_headers(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_TRACES_HEADERS", config.HEADERS)


def _normalize_traces_protocol(protocol: str) -> str:
    protocol = protocol.strip().lower()
    if protocol not in ("grpc", "http/json", "http/protobuf"):
        return "http/protobuf"
    return protocol


def _derive_traces_protocol(config: "ExporterConfig"):
    protocol = get_config(["OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "OTEL_EXPORTER_OTLP_PROTOCOL"], "http/protobuf")
    return _normalize_traces_protocol(protocol)


def _derive_traces_timeout(config: "ExporterConfig"):
    return get_config(["OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "OTEL_EXPORTER_OTLP_TIMEOUT"], config.DEFAULT_TIMEOUT, int)


def _derive_traces_endpoint(config: "ExporterConfig"):
    # libdatadog cannot export OTLP traces in agentless mode, so the intake is never their default.
    return env.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or _default_endpoint(
        config.TRACES_PROTOCOL, config.TRACES_PATH, agentless_intake=False
    )


def _derive_trace_metrics_endpoint(config: "ExporterConfig"):
    """Endpoint for the native OTLP trace-metrics exporter (client-computed span stats).

    Unlike the protocol-aware METRICS_ENDPOINT, this always resolves an HTTP /v1/metrics
    endpoint because libdatadog only supports HTTP/JSON for trace metrics.
    """
    return env.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT") or _default_endpoint("http/json", config.METRICS_PATH)


def _is_otlp_traces_exporter_enabled(exporter_config: "ExporterConfig") -> bool:
    return agent_config.trace_otlp_export_enabled


def otel_resource_attributes() -> dict[str, str]:
    """Return the resource attributes of the OTLP logs and metrics, built from DD_TAGS and OTEL_RESOURCE_ATTRIBUTES.

    The unified service tags are reported under their OpenTelemetry semantic convention names.
    """
    resource_attributes = {k: v for k, v in tracer_config.tags.items() if k not in ("service", "version", "env")}
    resource_attributes.update(
        {
            "service.name": tracer_config.service,
            "service.version": tracer_config.version,
            "deployment.environment.name": tracer_config.env,
        }
    )

    if tracer_config._report_hostname and "host.name" not in resource_attributes:
        resource_attributes["host.name"] = get_hostname()

    return {k: str(v) if v is not None else "" for k, v in resource_attributes.items()}


class OpenTelemetryConfig(DDConfig):
    __prefix__ = "otel"


class ExporterConfig(DDConfig):
    __prefix__ = "exporter"

    DEFAULT_HEADERS: str = ""
    DEFAULT_TIMEOUT: int = 10000
    LOGS_PATH: str = "/v1/logs"
    METRICS_PATH: str = "/v1/metrics"
    TRACES_PATH: str = "/v1/traces"
    DEFAULT_METRICS_TEMPORALITY_PREFERENCE: str = "delta"
    DEFAULT_METRICS_METRIC_READER_EXPORT_INTERVAL: int = 10000
    DEFAULT_METRICS_METRIC_READER_EXPORT_TIMEOUT: int = 7500

    PROTOCOL = DDConfig.v(t.Optional[str], "otlp.protocol", default="http/protobuf")
    ENDPOINT = DDConfig.d(str, _derive_endpoint)
    HEADERS = DDConfig.v(str, "otlp.headers", default=DEFAULT_HEADERS)
    TIMEOUT = DDConfig.v(int, "otlp.timeout", default=DEFAULT_TIMEOUT)

    LOGS_PROTOCOL = DDConfig.d(str, _derive_logs_protocol)
    LOGS_ENDPOINT = DDConfig.d(str, _derive_logs_endpoint)
    LOGS_HEADERS = DDConfig.d(str, _derive_logs_headers)
    LOGS_TIMEOUT = DDConfig.d(int, _derive_logs_timeout)

    METRICS_PROTOCOL = DDConfig.d(str, _derive_metrics_protocol)
    METRICS_ENDPOINT = DDConfig.d(str, _derive_metrics_endpoint)
    METRICS_HEADERS = DDConfig.d(str, _derive_metrics_headers)
    METRICS_TIMEOUT = DDConfig.d(int, _derive_metrics_timeout)
    METRICS_TEMPORALITY_PREFERENCE = DDConfig.d(str, _derive_metrics_temporality_preference)
    METRICS_METRIC_READER_EXPORT_INTERVAL = DDConfig.d(int, _derive_metrics_metric_reader_export_interval)
    METRICS_METRIC_READER_EXPORT_TIMEOUT = DDConfig.d(int, _derive_metrics_metric_reader_export_timeout)

    # OTLP trace transport and encoding: grpc, http/json, or http/protobuf.
    TRACES_PROTOCOL = DDConfig.d(str, _derive_traces_protocol)
    TRACES_ENDPOINT = DDConfig.d(str, _derive_traces_endpoint)
    TRACES_HEADERS = DDConfig.d(str, _derive_traces_headers)
    TRACES_TIMEOUT = DDConfig.d(int, _derive_traces_timeout)

    # Endpoint for the native (libdatadog) OTLP trace-metrics exporter. Distinct from the SDK's
    # protocol-aware METRICS_ENDPOINT because the native exporter is HTTP/JSON only.
    TRACE_METRICS_ENDPOINT = DDConfig.d(str, _derive_trace_metrics_endpoint)


OpenTelemetryConfig.include(ExporterConfig, namespace="exporter")

otel_config = OpenTelemetryConfig()

report_configuration(otel_config)
