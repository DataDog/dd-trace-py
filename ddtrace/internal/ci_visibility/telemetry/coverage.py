from enum import Enum
from typing import List
from typing import Tuple

from ddtrace.internal.ci_visibility.telemetry.constants import CIVISIBILITY_TELEMETRY_NAMESPACE as _NAMESPACE
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS
from ddtrace.internal.ci_visibility.telemetry.utils import skip_if_agentless
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer


log = get_logger(__name__)


class COVERAGE_TELEMETRY(str, Enum):
    STARTED = "code_coverage_started"
    FINISHED = "code_coverage_finished"
    IS_EMTPY = "code_coverage.is_empty"
    FILES = "code_coverage.files"
    ERRORS = "code_coverage.errors"


class COVERAGE_LIBRARY(str, Enum):
    COVERAGEPY = "coverage.py"
    DD_COVERAGE = "dd_coverage"


@skip_if_agentless
def record_code_coverage_started(test_framework: TEST_FRAMEWORKS, coverage_library: COVERAGE_LIBRARY):
    log.debug("Recording code coverage started telemetry: %s, %s", test_framework, coverage_library)
    _tags: List[Tuple[str, str]] = [("coverage_library", coverage_library)]
    if test_framework is not None:
        _tags.append(("test_framework", test_framework))
    telemetry_writer.add_count_metric(_NAMESPACE, COVERAGE_TELEMETRY.STARTED.value, 1, tuple(_tags))


@skip_if_agentless
def record_code_coverage_finished(test_framework: TEST_FRAMEWORKS, coverage_library: COVERAGE_LIBRARY):
    log.debug("Recording code coverage finished telemetry: %s, %s", test_framework, coverage_library)
    _tags: List[Tuple[str, str]] = [("coverage_library", coverage_library)]
    if test_framework is not None:
        _tags.append(("test_framework", test_framework))
    telemetry_writer.add_count_metric(_NAMESPACE, COVERAGE_TELEMETRY.FINISHED, 1, tuple(_tags))


@skip_if_agentless
def record_code_coverage_empty():
    log.debug("Recording code coverage empty telemetry")
    telemetry_writer.add_count_metric(_NAMESPACE, COVERAGE_TELEMETRY.IS_EMTPY, 1)


@skip_if_agentless
def record_code_coverage_files(count_files: int):
    log.debug("Recording code coverage files telemetry: %s", count_files)
    try:
        count_files = int(count_files)
    except ValueError:
        log.error("Invalid count_files value %s", count_files)
        return
    telemetry_writer.add_count_metric(_NAMESPACE, COVERAGE_TELEMETRY.FILES, count_files)


@skip_if_agentless
def record_code_coverage_error():
    log.debug("Recording code coverage error telemetry")
    telemetry_writer.add_count_metric(_NAMESPACE, COVERAGE_TELEMETRY.ERRORS, 1)
