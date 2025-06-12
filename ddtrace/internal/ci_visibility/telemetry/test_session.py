#!/usr/bin/env python3
from enum import Enum

from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


log = get_logger(__name__)


class TEST_SESSION_TELEMETRY(str, Enum):
    COLLECTED = "total_tests_collected"


def record_num_tests_discovered(count_tests: int):
    log.debug("Recording session collected items telemetry: %s", count_tests)
    telemetry_writer.add_distribution_metric(
        TELEMETRY_NAMESPACE.CIVISIBILITY, TEST_SESSION_TELEMETRY.COLLECTED, count_tests
    )
