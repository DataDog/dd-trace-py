from typing import Any

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


_ENCODING = "protobuf"


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
