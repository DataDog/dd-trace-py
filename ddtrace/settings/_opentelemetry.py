import typing as t

from ddtrace.internal.telemetry import get_config
from ddtrace.internal.telemetry import report_configuration
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.settings._agent import get_agent_hostname
from ddtrace.settings._core import DDConfig


def _derive_endpoint(config: "ExporterConfig"):
    endpoint = get_config("OTEL_EXPORTER_OTLP_ENDPOINT", config.DEFAULT_ENDPOINT)
    if endpoint != config.DEFAULT_ENDPOINT:
        return endpoint
    elif config.PROTOCOL.lower() in ("http/json", "http/protobuf"):
        return f"http://{get_agent_hostname()}:{ExporterConfig.HTTP_PORT}{ExporterConfig.HTTP_LOGS_ENDPOINT}"
    return f"http://{get_agent_hostname()}:{ExporterConfig.GRPC_PORT}"


def _derive_logs_endpoint(config: "ExporterConfig"):
    endpoint = get_config("OTEL_EXPORTER_OTLP_ENDPOINT", config.DEFAULT_ENDPOINT)
    if endpoint != config.DEFAULT_ENDPOINT:
        return endpoint
    elif config.LOGS_PROTOCOL.lower() in ("http/json", "http/protobuf"):
        return f"http://{get_agent_hostname()}:{ExporterConfig.HTTP_PORT}{ExporterConfig.HTTP_LOGS_ENDPOINT}"
    return f"http://{get_agent_hostname()}:{ExporterConfig.GRPC_PORT}"


def _derive_logs_protocol(config: "ExporterConfig"):
    logs_protocol = get_config("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", "grpc")
    if logs_protocol != "grpc":
        return logs_protocol
    return config.PROTOCOL


def _derive_logs_headers(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_LOGS_HEADERS", config.HEADERS, parse_tags_str)


def _derive_logs_timeout(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_LOGS_TIMEOUT", config.DEFAULT_TIMEOUT, int)


class OpenTelemetryConfig(DDConfig):
    __prefix__ = "otel"


class ExporterConfig(DDConfig):
    __prefix__ = "exporter"

    GRPC_PORT: int = 4317
    HTTP_PORT: int = 4318
    HTTP_LOGS_ENDPOINT: str = "/v1/logs"
    DEFAULT_ENDPOINT: str = "http://localhost:4318"
    DEFAULT_HEADERS: t.Dict[str, str] = {}
    DEFAULT_TIMEOUT: int = 10000

    PROTOCOL = DDConfig.v(t.Optional[str], "otlp.protocol", default="grpc")
    ENDPOINT = DDConfig.d(str, _derive_endpoint)
    HEADERS = DDConfig.v(dict, "otlp.headers", default=DEFAULT_HEADERS, parser=parse_tags_str)
    TIMEOUT = DDConfig.v(int, "otlp.timeout", default=DEFAULT_TIMEOUT)

    LOGS_PROTOCOL = DDConfig.d(str, _derive_logs_protocol)
    LOGS_ENDPOINT = DDConfig.d(str, _derive_logs_endpoint)
    LOGS_HEADERS = DDConfig.d(dict, _derive_logs_headers)
    LOGS_TIMEOUT = DDConfig.d(int, _derive_logs_timeout)


OpenTelemetryConfig.include(ExporterConfig, namespace="exporter")

otel_config = OpenTelemetryConfig()

report_configuration(otel_config)
