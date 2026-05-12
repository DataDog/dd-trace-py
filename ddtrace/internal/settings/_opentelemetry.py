import typing as t

from ddtrace.internal.settings import env
from ddtrace.internal.settings._agent import get_agent_hostname
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.telemetry import get_config
from ddtrace.internal.telemetry import report_configuration


def _derive_endpoint(config: "ExporterConfig"):
    default_endpoint = ExporterConfig._get_default_endpoint(config.PROTOCOL)
    return get_config("OTEL_EXPORTER_OTLP_ENDPOINT", default_endpoint)


def _derive_logs_endpoint(config: "ExporterConfig"):
    default_endpoint = ExporterConfig._get_default_endpoint(config.LOGS_PROTOCOL, config.LOGS_PATH)
    return get_config("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", default_endpoint)


def _derive_logs_protocol(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", config.PROTOCOL)


def _derive_logs_headers(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_LOGS_HEADERS", config.HEADERS)


def _derive_logs_timeout(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_LOGS_TIMEOUT", config.DEFAULT_TIMEOUT, int)


def _derive_metrics_endpoint(config: "ExporterConfig"):
    default_endpoint = ExporterConfig._get_default_endpoint(config.METRICS_PROTOCOL, config.METRICS_PATH)
    return get_config("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", default_endpoint)


def _derive_metrics_protocol(config: "ExporterConfig"):
    return get_config(["OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", "OTEL_EXPORTER_OTLP_PROTOCOL"], config.PROTOCOL)


def _derive_metrics_headers(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_METRICS_HEADERS", config.HEADERS)


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
    # Default to HTTP/JSON endpoint since libdatadog currently only supports http/json for OTLP traces.
    return f"{ExporterConfig.DEFAULT_HTTP_ENDPOINT}{ExporterConfig.TRACES_PATH}"


def _is_otlp_traces_exporter_enabled(exporter_config: "ExporterConfig") -> bool:
    if env.get("DD_TRACE_AGENT_PROTOCOL_VERSION"):
        return False
    return env.get("OTEL_TRACES_EXPORTER", "").lower() == "otlp"


class OpenTelemetryConfig(DDConfig):
    __prefix__ = "otel"


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

    # TRACES_PROTOCOL is collected for telemetry but not yet used to switch transport:
    # libdatadog currently only supports HTTP/JSON for OTLP traces. gRPC support will
    # consume this field when added.
    TRACES_PROTOCOL = DDConfig.d(str, _derive_traces_protocol)
    TRACES_ENDPOINT = DDConfig.d(str, _derive_traces_endpoint)
    TRACES_HEADERS = DDConfig.d(str, _derive_traces_headers)
    TRACES_TIMEOUT = DDConfig.d(int, _derive_traces_timeout)

    @staticmethod
    def _get_default_endpoint(protocol: str, endpoint: str = ""):
        if protocol.lower() in ("http/json", "http/protobuf"):
            return f"{ExporterConfig.DEFAULT_HTTP_ENDPOINT}{endpoint}"
        return f"{ExporterConfig.DEFAULT_GRPC_ENDPOINT}"


OpenTelemetryConfig.include(ExporterConfig, namespace="exporter")

otel_config = OpenTelemetryConfig()

report_configuration(otel_config)
