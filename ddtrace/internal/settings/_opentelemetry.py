import typing as t

from ddtrace.internal.settings import env
from ddtrace.internal.settings._agent import get_agent_hostname
from ddtrace.internal.settings._agentless import config as agentless_config
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.telemetry import get_config
from ddtrace.internal.telemetry import report_configuration
from ddtrace.internal.utils.formats import asbool


def _agentless_endpoint(signal_path: str = "") -> str:
    return f"https://otlp.{agentless_config.site}{signal_path}"


def _targets_agentless_intake(signal_endpoint_env_var: str = "") -> bool:
    if not agentless_config.enabled:
        return False
    if env.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    return not (signal_endpoint_env_var and env.get(signal_endpoint_env_var))


def _with_intake_auth(configured: str, signal_endpoint_env_var: str) -> str:
    if not agentless_config.api_key or not _targets_agentless_intake(signal_endpoint_env_var):
        return configured
    if "dd-api-key" in configured.lower():
        return configured
    api_key_header = f"dd-api-key={agentless_config.api_key}"
    return f"{configured},{api_key_header}" if configured else api_key_header


def _default_protocol(config: "ExporterConfig", signal_endpoint_env_var: str = "") -> str:
    """The protocol to fall back on when no signal-specific one is set.

    Agentless to our intake overrides the gRPC default because the intake speaks https only.
    """
    if _targets_agentless_intake(signal_endpoint_env_var) and "OTEL_EXPORTER_OTLP_PROTOCOL" not in env:
        return "http/protobuf"
    return config.PROTOCOL


def _default_endpoint(config: "ExporterConfig", protocol: str, signal_path: str = "") -> str:
    if agentless_config.enabled and "OTEL_EXPORTER_OTLP_ENDPOINT" not in env:
        return _agentless_endpoint(signal_path)
    return ExporterConfig._get_default_endpoint(protocol, signal_path)


def _derive_endpoint(config: "ExporterConfig"):
    default_endpoint = ExporterConfig._get_default_endpoint(config.PROTOCOL)
    return get_config("OTEL_EXPORTER_OTLP_ENDPOINT", default_endpoint)


def _derive_logs_endpoint(config: "ExporterConfig"):
    default_endpoint = _default_endpoint(config, config.LOGS_PROTOCOL, config.LOGS_PATH)
    return get_config("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", default_endpoint)


def _derive_logs_protocol(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", _default_protocol(config, "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"))


def _derive_logs_headers(config: "ExporterConfig"):
    return _with_intake_auth(
        get_config("OTEL_EXPORTER_OTLP_LOGS_HEADERS", config.HEADERS), "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"
    )


def _derive_logs_timeout(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_LOGS_TIMEOUT", config.DEFAULT_TIMEOUT, int)


def _derive_metrics_endpoint(config: "ExporterConfig"):
    default_endpoint = _default_endpoint(config, config.METRICS_PROTOCOL, config.METRICS_PATH)
    return get_config("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", default_endpoint)


def _derive_metrics_protocol(config: "ExporterConfig"):
    return get_config(
        ["OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", "OTEL_EXPORTER_OTLP_PROTOCOL"],
        _default_protocol(config, "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"),
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


def _derive_traces_protocol(config: "ExporterConfig"):
    return get_config(["OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "OTEL_EXPORTER_OTLP_PROTOCOL"], config.PROTOCOL)


def _derive_traces_timeout(config: "ExporterConfig"):
    return get_config(["OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "OTEL_EXPORTER_OTLP_TIMEOUT"], config.DEFAULT_TIMEOUT, int)


def _derive_traces_endpoint(config: "ExporterConfig"):
    # Signal-specific endpoint takes precedence (full URL, no path appended).
    if traces_endpoint := env.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        return traces_endpoint
    # Global endpoint is a base URL; append the traces signal path.
    global_endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if global_endpoint:
        return global_endpoint.rstrip("/") + ExporterConfig.TRACES_PATH
    # Nothing configured: fall back to the local HTTP OTLP endpoint.
    return f"{ExporterConfig.DEFAULT_HTTP_ENDPOINT}{ExporterConfig.TRACES_PATH}"


def _derive_trace_metrics_endpoint(config: "ExporterConfig"):
    """Endpoint for the native OTLP trace-metrics exporter (client-computed span stats).

    libdatadog only supports HTTP/JSON for this exporter, so — unlike the SDK's protocol-aware
    ``METRICS_ENDPOINT`` (which defaults to the gRPC endpoint with no ``/v1/metrics`` path) — this
    always resolves an HTTP ``/v1/metrics`` endpoint, mirroring ``_derive_traces_endpoint``.
    """
    # Signal-specific endpoint takes precedence (full URL, no path appended).
    if metrics_endpoint := env.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"):
        return metrics_endpoint
    # Global endpoint is a base URL; append the metrics signal path.
    global_endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if global_endpoint:
        return global_endpoint.rstrip("/") + ExporterConfig.METRICS_PATH
    # Agentless has no agent OTLP receiver to fall back on.
    if _targets_agentless_intake():
        return _agentless_endpoint(ExporterConfig.METRICS_PATH)
    # Default to HTTP/JSON endpoint since libdatadog currently only supports http/json here.
    return f"{ExporterConfig.DEFAULT_HTTP_ENDPOINT}{ExporterConfig.METRICS_PATH}"


def _is_otlp_traces_exporter_enabled(exporter_config: "ExporterConfig") -> bool:
    if asbool(env.get("DD_TRACE_OTEL_SEMANTICS_ENABLED", default=False)):
        return True
    if env.get("DD_TRACE_AGENT_PROTOCOL_VERSION"):
        return False
    return env.get("OTEL_TRACES_EXPORTER", "").lower() == "otlp"


def _is_otlp_trace_metrics_enabled(
    exporter_config: "ExporterConfig",
    explicit_enabled: t.Optional[bool],
    otel_metrics_enabled: bool,
) -> bool:
    """Whether client-computed span stats should be exported as OTLP metrics.

    Tri-state ``explicit_enabled`` (``OTEL_TRACES_SPAN_METRICS_ENABLED``) takes precedence; when
    ``None``, the feature auto-enables only if both OTLP trace export and OTel metrics export are
    enabled. The two config flags are passed in by the caller so this module stays free of the
    top-level ``ddtrace.internal.settings._config`` singleton (avoids an import cycle).
    """
    if explicit_enabled is not None:
        return explicit_enabled
    return _is_otlp_traces_exporter_enabled(exporter_config) and otel_metrics_enabled


class OpenTelemetryConfig(DDConfig):
    __prefix__ = "otel"

    HTTP_KNOWN_METHODS = DDConfig.v(t.Optional[str], "instrumentation.http.known_methods", default=None)


class ExporterConfig(DDConfig):
    __prefix__ = "exporter"

    DEFAULT_HEADERS: str = ""
    DEFAULT_TIMEOUT: int = 10000
    LOGS_PATH: str = "/v1/logs"
    METRICS_PATH: str = "/v1/metrics"
    TRACES_PATH: str = "/v1/traces"
    DEFAULT_GRPC_ENDPOINT: str = f"http://{get_agent_hostname()}:4317"
    DEFAULT_HTTP_ENDPOINT: str = f"http://{get_agent_hostname()}:4318"
    DEFAULT_METRICS_TEMPORALITY_PREFERENCE: str = "delta"
    DEFAULT_METRICS_METRIC_READER_EXPORT_INTERVAL: int = 10000
    DEFAULT_METRICS_METRIC_READER_EXPORT_TIMEOUT: int = 7500

    PROTOCOL = DDConfig.v(t.Optional[str], "otlp.protocol", default="grpc")
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

    # OTLP trace encoding: http/json or http/protobuf (see NativeWriter for how it is applied).
    TRACES_PROTOCOL = DDConfig.d(str, _derive_traces_protocol)
    TRACES_ENDPOINT = DDConfig.d(str, _derive_traces_endpoint)
    TRACES_HEADERS = DDConfig.d(str, _derive_traces_headers)
    TRACES_TIMEOUT = DDConfig.d(int, _derive_traces_timeout)

    # Endpoint for the native (libdatadog) OTLP trace-metrics exporter. Distinct from the SDK's
    # protocol-aware METRICS_ENDPOINT because the native exporter is HTTP/JSON only.
    TRACE_METRICS_ENDPOINT = DDConfig.d(str, _derive_trace_metrics_endpoint)

    @staticmethod
    def _get_default_endpoint(protocol: str, endpoint: str = ""):
        if protocol.lower() in ("http/json", "http/protobuf"):
            return f"{ExporterConfig.DEFAULT_HTTP_ENDPOINT}{endpoint}"
        return f"{ExporterConfig.DEFAULT_GRPC_ENDPOINT}"


OpenTelemetryConfig.include(ExporterConfig, namespace="exporter")

otel_config = OpenTelemetryConfig()

report_configuration(otel_config)
