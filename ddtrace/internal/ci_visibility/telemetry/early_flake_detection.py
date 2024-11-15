from enum import Enum

from ddtrace.internal.ci_visibility.telemetry.constants import CIVISIBILITY_TELEMETRY_NAMESPACE as _NAMESPACE
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer


log = get_logger(__name__)

EARLY_FLAKE_DETECTION_TELEMETRY_PREFIX = "early_flake_detection."
RESPONSE_TESTS = f"{EARLY_FLAKE_DETECTION_TELEMETRY_PREFIX}response_tests"


class EARLY_FLAKE_DETECTION_TELEMETRY(str, Enum):
    REQUEST = "early_flake_detection.request"
    REQUEST_MS = "early_flake_detection.request_ms"
    REQUEST_ERRORS = "early_flake_detection.request_errors"
    RESPONSE_BYTES = "early_flake_detection.response_bytes"
    RESPONSE_TESTS = "early_flake_detection.response_tests"


def record_early_flake_detection_tests_count(early_flake_detection_count: int):
    log.debug("Recording early flake detection tests count telemetry: %s", early_flake_detection_count)
    telemetry_writer.add_count_metric(_NAMESPACE, RESPONSE_TESTS, early_flake_detection_count)
