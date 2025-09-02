import typing as t

from ddtrace.internal.telemetry import get_config
from ddtrace.internal.telemetry import report_configuration
from ddtrace.settings._agent import get_agent_hostname
from ddtrace.settings._core import DDConfig


def _derive_endpoint(config: "ExporterConfig"):
    if config.PROTOCOL.lower() in ("http/json", "http/protobuf"):
        default_endpoint = (
            f"http://{get_agent_hostname()}:{ExporterConfig.HTTP_PORT}{ExporterConfig.HTTP_LOGS_ENDPOINT}"
        )
    else:
        default_endpoint = f"http://{get_agent_hostname()}:{ExporterConfig.GRPC_PORT}"
    return get_config("OTEL_EXPORTER_OTLP_ENDPOINT", default_endpoint)


def _derive_logs_endpoint(config: "ExporterConfig"):
    if config.LOGS_PROTOCOL.lower() in ("http/json", "http/protobuf"):
        default_endpoint = (
            f"http://{get_agent_hostname()}:{ExporterConfig.HTTP_PORT}{ExporterConfig.HTTP_LOGS_ENDPOINT}"
        )
    else:
        default_endpoint = f"http://{get_agent_hostname()}:{ExporterConfig.GRPC_PORT}"
    return get_config(["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"], default_endpoint)


def _derive_logs_protocol(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", config.PROTOCOL)


def _derive_logs_headers(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_LOGS_HEADERS", config.HEADERS)


def _derive_logs_timeout(config: "ExporterConfig"):
    return get_config("OTEL_EXPORTER_OTLP_LOGS_TIMEOUT", config.DEFAULT_TIMEOUT, int)


class OpenTelemetryConfig(DDConfig):
    __prefix__ = "otel"


class ExporterConfig(DDConfig):
    __prefix__ = "exporter"

    GRPC_PORT: int = 4317
    HTTP_PORT: int = 4318
    HTTP_LOGS_ENDPOINT: str = "/v1/logs"
    DEFAULT_HEADERS: str = ""
    DEFAULT_TIMEOUT: int = 10000

    PROTOCOL = DDConfig.v(t.Optional[str], "otlp.protocol", default="grpc")
    ENDPOINT = DDConfig.d(str, _derive_endpoint)
    HEADERS = DDConfig.v(str, "otlp.headers", default=DEFAULT_HEADERS)
    TIMEOUT = DDConfig.v(int, "otlp.timeout", default=DEFAULT_TIMEOUT)

    LOGS_PROTOCOL = DDConfig.d(str, _derive_logs_protocol)
    LOGS_ENDPOINT = DDConfig.d(str, _derive_logs_endpoint)
    LOGS_HEADERS = DDConfig.d(str, _derive_logs_headers)
    LOGS_TIMEOUT = DDConfig.d(int, _derive_logs_timeout)


OpenTelemetryConfig.include(ExporterConfig, namespace="exporter")

otel_config = OpenTelemetryConfig()

report_configuration(otel_config)
