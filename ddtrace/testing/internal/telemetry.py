from __future__ import annotations

import dataclasses
from enum import Enum
import logging
import typing as t

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


if t.TYPE_CHECKING:
    from ddtrace.testing.internal.http import BackendConnectorSetup


log = logging.getLogger(__name__)


class ErrorType(str, Enum):
    TIMEOUT = "timeout"
    NETWORK = "network"
    CODE_4XX = "status_code_4xx_response"
    CODE_5XX = "status_code_5xx_response"
    BAD_JSON = "bad_json"
    UNKNOWN = "unknown"


class TelemetryAPI:
    def __init__(self, connector_setup: BackendConnectorSetup) -> None:
        # DEV: In a beautiful world, this would set up a backend connector to the telemetry endpoint.
        # Currently we rely on ddtrace's telemetry infrastructure, so we don't have to do anything here.
        self.writer = telemetry_writer

    def request_metrics(
        self, count: str, duration: str, response_bytes: t.Optional[str], error: str
    ) -> TelemetryAPIRequestMetrics:
        return TelemetryAPIRequestMetrics(
            telemetry_api=self, count=count, duration=duration, response_bytes=response_bytes, error=error
        )


@dataclasses.dataclass
class TelemetryAPIRequestMetrics:
    telemetry_api: TelemetryAPI
    count: str
    duration: str
    response_bytes: t.Optional[str]
    error: str

    def record_request(
        self, seconds: float, response_bytes: t.Optional[int], compressed_response: bool, error: t.Optional[ErrorType]
    ) -> None:
        log.debug(
            "Recording Test Optimization telemetry for %s: %s %s %s %s",
            self.count,
            seconds,
            response_bytes,
            compressed_response,
            error,
        )
        self.telemetry_api.writer.add_count_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, self.count, 1)
        self.telemetry_api.writer.add_distribution_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, self.duration, seconds)
        if response_bytes is not None and self.response_bytes is not None:
            # We don't always want to record response bytes (for settings requests), so assume that no metric name
            # means we don't want to record it.
            self.telemetry_api.writer.add_distribution_metric(
                TELEMETRY_NAMESPACE.CIVISIBILITY, self.response_bytes, response_bytes
            )

        if error is not None:
            self.record_error(error)

    def record_error(self, error: ErrorType) -> None:
        log.debug("Recording Test Optimization request error telemetry: %s", error)
        self.telemetry_api.writer.add_count_metric(
            TELEMETRY_NAMESPACE.CIVISIBILITY, self.error, 1, (("error_type", error),)
        )
