from functools import cache
import logging
from typing import Any

from ddtrace.internal.opentelemetry.utils import set_http_exporter_marker
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


class _MetricsExporterTelemetry:
    _protocol: str

    def export(self, metrics_data: Any, timeout_millis: Any = 10_000, *args: Any, **kwargs: Any) -> Any:
        record_metrics_export_attempt(self._protocol)
        log.debug("Exporting OpenTelemetry Metrics with %s protocol and protobuf encoding", self._protocol)
        result = super().export(metrics_data, timeout_millis, *args, **kwargs)  # type: ignore[misc]
        record_metrics_export_result(result, self._protocol)
        return result


@cache
def metrics_exporter_with_telemetry(exporter_class: type[Any], protocol: str) -> type[Any]:
    class MetricsExporter(_MetricsExporterTelemetry, exporter_class):  # type: ignore[misc]
        _protocol = protocol

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            if protocol == "http":
                set_http_exporter_marker(self)

    MetricsExporter.__name__ = exporter_class.__name__
    MetricsExporter.__qualname__ = exporter_class.__qualname__
    return MetricsExporter


class _LogsExporterTelemetry:
    _protocol: str

    def export(self, batch: Any, *args: Any, **kwargs: Any) -> Any:
        record_log_records(len(batch), self._protocol)
        log.debug("Exporting %d OpenTelemetry Logs with %s protocol and protobuf encoding", len(batch), self._protocol)
        return super().export(batch, *args, **kwargs)  # type: ignore[misc]


@cache
def logs_exporter_with_telemetry(exporter_class: type[Any], protocol: str) -> type[Any]:
    class LogsExporter(_LogsExporterTelemetry, exporter_class):  # type: ignore[misc]
        _protocol = protocol

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            if protocol == "http":
                set_http_exporter_marker(self)

    LogsExporter.__name__ = exporter_class.__name__
    LogsExporter.__qualname__ = exporter_class.__qualname__
    return LogsExporter
