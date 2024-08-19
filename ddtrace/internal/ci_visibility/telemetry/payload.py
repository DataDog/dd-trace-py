from enum import Enum

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.logger import get_logger
from ddtrace.internal.ci_visibility.telemetry.constants import CIVISIBILITY_TELEMETRY_NAMESPACE as _NAMESPACE


log = get_logger(__name__)


class ENDPOINT(str, Enum):
    TEST_CYCLE = "test_cycle"
    CODE_COVERAGE = "code_coverage"


class ENDPOINT_PAYLOAD_TELEMETRY(str, Enum):
    BYTES = "event_payload.bytes"
    REQUESTS_COUNT = "event_payload.requests"
    REQUESTS_MS = "event_payload.requests_ms"
    REQUESTS_ERRORS = "event_payload.requests_errors"
    EVENTS_COUNT = "event_payload.events_count"
    EVENTS_SERIALIZATION_MS = "event_payload.events_serialization_ms"


class REQUEST_ERROR_TYPE(str, Enum):
    TIMEOUT = "timeout"
    NETWORK = "network"
    STATUS_CODE = "status_code"


def record_endpoint_payload_bytes(endpoint: ENDPOINT, nbytes: int) -> None:
    log.debug("Recording endpoint payload bytes: %s, %s", endpoint, nbytes)
    tags = (("endpoint", ENDPOINT),)
    telemetry_writer.add_distribution_metric(_NAMESPACE, ENDPOINT_PAYLOAD_TELEMETRY.BYTES, nbytes, tags)


def record_endpoint_payload_request(endpoint: ENDPOINT) -> None:
    log.debug("Recording endpoint payload request: %s", endpoint)
    tags = (("endpoint", ENDPOINT),)
    telemetry_writer.add_count_metric(_NAMESPACE, ENDPOINT_PAYLOAD_TELEMETRY.REQUESTS_COUNT, 1, tags)


def record_endpoint_payload_request_time(endpoint: ENDPOINT, seconds: float) -> None:
    log.debug("Recording endpoint payload request time: %s, %s seconds", endpoint, seconds)
    tags = (("endpoint", ENDPOINT),)
    telemetry_writer.add_distribution_metric(_NAMESPACE, ENDPOINT_PAYLOAD_TELEMETRY.REQUESTS_MS, seconds*1000, tags)


def record_endpoint_payload_request_error(endpoint: ENDPOINT, error_type: REQUEST_ERROR_TYPE) -> None:
    log.debug("Recording endpoint payload request error: %s, %s", endpoint, error_type)
    tags = (("endpoint", ENDPOINT), ("error_type", error_type))
    telemetry_writer.add_count_metric(_NAMESPACE, ENDPOINT_PAYLOAD_TELEMETRY.REQUESTS_ERRORS, 1, tags)


def record_endpoint_payload_events_count(endpoint: ENDPOINT, events: int) -> None:
    log.debug("Recording endpoint payload events count: %s, %s", endpoint, events)
    tags = (("endpoint", ENDPOINT),)
    telemetry_writer.add_distribution_metric(_NAMESPACE, ENDPOINT_PAYLOAD_TELEMETRY.EVENTS_COUNT, events, tags)


def record_endpoint_payload_events_serialization_time(endpoint: ENDPOINT, seconds: float) -> None:
    log.debug("Recording endpoint payload serialization time: %s, %s seconds", endpoint, nbytes)
    tags = (("endpoint", ENDPOINT),)
    telemetry_writer.add_distribution_metric(_NAMESPACE, ENDPOINT_PAYLOAD_TELEMETRY.EVENTS_SERIALIZATION_MS, seconds*1000, tags)
