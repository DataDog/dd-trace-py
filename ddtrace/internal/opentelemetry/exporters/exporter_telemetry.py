import logging
from typing import Any

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


_ENCODING = "protobuf"
log = logging.getLogger(__name__)


def record_log_records(count: int, protocol: str) -> None:
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.TRACERS,
        "otel.log_records",
        count,
        (("protocol", protocol), ("encoding", _ENCODING)),
    )


def record_metrics_export_attempt(protocol: str) -> None:
    # TODO: Count the number of unique metrics streams in this export.
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.TRACERS,
        "otel.metrics_export_attempts",
        1,
        (("protocol", protocol), ("encoding", _ENCODING)),
    )


def record_metrics_export_result(result: Any, protocol: str) -> None:
    if result.value not in (0, 1):
        return

    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.TRACERS,
        "otel.metrics_export_successes" if result.value == 0 else "otel.metrics_export_failures",
        1,
        (("protocol", protocol), ("encoding", _ENCODING)),
    )


class _ExporterProxy:
    _exporter: Any

    def __getattr__(self, name: str) -> Any:
        return getattr(self._exporter, name)


class _MetricsExporterProxy(_ExporterProxy):
    _protocol: str

    def export(self, metrics_data: Any, timeout_millis: Any = 10_000, *args: Any, **kwargs: Any) -> Any:
        record_metrics_export_attempt(self._protocol)
        log.debug("Exporting OpenTelemetry Metrics with %s protocol and protobuf encoding", self._protocol)
        result = self._exporter.export(metrics_data, timeout_millis, *args, **kwargs)
        record_metrics_export_result(result, self._protocol)
        return result


class GRPCMetricsExporter(_MetricsExporterProxy):
    _protocol = "grpc"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

        self._exporter = OTLPMetricExporter(*args, **kwargs)


class HTTPMetricsExporter(_MetricsExporterProxy):
    _protocol = "http"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        self._exporter = OTLPMetricExporter(*args, **kwargs)


class _LogsExporterProxy(_ExporterProxy):
    _protocol: str

    def export(self, batch: Any, *args: Any, **kwargs: Any) -> Any:
        record_log_records(len(batch), self._protocol)
        log.debug("Exporting %d OpenTelemetry Logs with %s protocol and protobuf encoding", len(batch), self._protocol)
        return self._exporter.export(batch, *args, **kwargs)


class GRPCLogsExporter(_LogsExporterProxy):
    _protocol = "grpc"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter

        self._exporter = OTLPLogExporter(*args, **kwargs)


class HTTPLogsExporter(_LogsExporterProxy):
    _protocol = "http"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

        self._exporter = OTLPLogExporter(*args, **kwargs)
