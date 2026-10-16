from enum import Enum

from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


log = get_logger(__name__)


class EARLY_FLAKE_DETECTION_TELEMETRY(str, Enum):
    REQUEST = "early_flake_detection.request"
    REQUEST_MS = "early_flake_detection.request_ms"
    REQUEST_ERRORS = "early_flake_detection.request_errors"
    RESPONSE_BYTES = "early_flake_detection.response_bytes"
    RESPONSE_TESTS = "early_flake_detection.response_tests"


def record_early_flake_detection_tests_count(early_flake_detection_count: int):
    log.debug("Recording early flake detection tests count telemetry: %s", early_flake_detection_count)
    telemetry_writer.add_distribution_metric(
        TELEMETRY_NAMESPACE.CIVISIBILITY,
        EARLY_FLAKE_DETECTION_TELEMETRY.RESPONSE_TESTS.value,
        early_flake_detection_count,
    )


def record_early_flake_detection_pages_fetched(pages: int):
    log.debug("Recording early flake detection pages fetched telemetry: %s", pages)
    telemetry_writer.add_distribution_metric(
        TELEMETRY_NAMESPACE.CIVISIBILITY,
        "early_flake_detection.pages_fetched",
        pages,
    )


def record_early_flake_detection_total_fetch_ms(total_ms: float):
    log.debug("Recording early flake detection total fetch ms telemetry: %s", total_ms)
    telemetry_writer.add_distribution_metric(
        TELEMETRY_NAMESPACE.CIVISIBILITY,
        "early_flake_detection.total_fetch_ms",
        total_ms,
    )


def record_early_flake_detection_total_request_ms(total_ms: float):
    log.debug("Recording early flake detection total request ms telemetry: %s", total_ms)
    telemetry_writer.add_distribution_metric(
        TELEMETRY_NAMESPACE.CIVISIBILITY,
        "early_flake_detection.total_request_ms",
        total_ms,
    )
