from __future__ import annotations

import dataclasses
from enum import Enum
import logging
import typing as t

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
        pass

    def request_metrics(
        self, count: t.Optional[str], duration: t.Optional[str], response_bytes: t.Optional[str], error: t.Optional[str]
    ) -> TelemetryAPIRequestMetrics:
        return TelemetryAPIRequestMetrics(
            telemetry_api=self, count=count, duration=duration, response_bytes=response_bytes, error=error
        )


@dataclasses.dataclass
class TelemetryAPIRequestMetrics:
    telemetry_api: TelemetryAPI
    count: t.Optional[str]
    duration: t.Optional[str]
    response_bytes: t.Optional[str]
    error: t.Optional[str]

    def record_request(
        self, seconds: float, response_bytes: t.Optional[int], compressed_response: bool, error: t.Optional[ErrorType]
    ) -> None:
        log.debug("Recording request: %s %s %s %s %s", self.count, seconds, response_bytes, compressed_response, error)

    def record_request_error(
    )
