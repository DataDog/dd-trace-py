import dataclasses
from typing import Optional

from ddtrace.internal.ci_visibility.telemetry.constants import ERROR_TYPES
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


log = get_logger(__name__)


@dataclasses.dataclass(frozen=True)
class APIRequestMetricNames:
    count: str
    duration: str
    response_bytes: Optional[str]
    error: str


def record_api_request(
    metric_names: APIRequestMetricNames,
    duration: float,
    response_bytes: Optional[int] = None,
    error: Optional[ERROR_TYPES] = None,
):
    log.debug(
        "Recording early flake detection telemetry for %s: %s, %s, %s",
        metric_names.count,
        duration,
        response_bytes,
        error,
    )

    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, f"{metric_names.count}", 1)
    telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, f"{metric_names.duration}", duration)
    if response_bytes is not None:
        if metric_names.response_bytes is not None:
            # We don't always want to record response bytes (for settings requests), so assume that no metric name
            # means we don't want to record it.
            telemetry_writer.add_distribution_metric(
                TELEMETRY_NAMESPACE.CIVISIBILITY, f"{metric_names.response_bytes}", response_bytes
            )

    if error is not None:
        record_api_request_error(metric_names.error, error)


def record_api_request_error(error_metric_name: str, error: ERROR_TYPES):
    log.debug("Recording early flake detection request error telemetry: %s", error)
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, error_metric_name, 1, (("error_type", error),))
