import typing as t

from ddtrace.internal.telemetry import report_configuration
from ddtrace.settings._core import DDConfig


class OpenTelemetryExporterConfig(DDConfig):
    __prefix__ = "otel.exporter"

    # OTLP exporter configuration
    OTLP_PROTOCOL = DDConfig.v(t.Optional[str], "otlp.protocol", default=None)
    OTLP_LOGS_PROTOCOL = DDConfig.v(t.Optional[str], "otlp.logs.protocol", default=None)
    OTLP_ENDPOINT = DDConfig.v(t.Optional[str], "otlp.endpoint", default=None)
    OTLP_LOGS_ENDPOINT = DDConfig.v(t.Optional[str], "otlp.logs.endpoint", default=None)
    OTLP_HEADERS = DDConfig.v(t.Optional[str], "otlp.headers", default=None)
    OTLP_LOGS_HEADERS = DDConfig.v(t.Optional[str], "otlp.logs.headers", default=None)


exporter_config = OpenTelemetryExporterConfig()

report_configuration(exporter_config)
