from enum import Enum

from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


log = get_logger(__name__)


class TEST_MANAGEMENT_TELEMETRY(str, Enum):
    REQUEST = "test_management.request"
    REQUEST_MS = "test_management.request_ms"
    REQUEST_ERRORS = "test_management.request_errors"
    RESPONSE_BYTES = "test_management.response_bytes"
    RESPONSE_TESTS = "test_management.response_tests"


def record_test_management_tests_count(test_management_count: int):
    log.debug("Recording Test Management tests count telemetry: %s", test_management_count)
    telemetry_writer.add_distribution_metric(
        TELEMETRY_NAMESPACE.CIVISIBILITY,
        TEST_MANAGEMENT_TELEMETRY.RESPONSE_TESTS.value,
        test_management_count,
    )
