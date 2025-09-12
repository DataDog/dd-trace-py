from contextlib import contextmanager
from dataclasses import dataclass
import typing as t

from _pytest.runner import runtestprotocol
import pytest

from ddtrace.contrib.internal.pytest._types import pytest_TestReport
from ddtrace.contrib.internal.pytest._utils import TestPhase
from ddtrace.contrib.internal.pytest._utils import _TestOutcome
from ddtrace.contrib.internal.pytest._utils import excinfo_by_report
from ddtrace.contrib.internal.pytest._utils import get_user_property
from ddtrace.ext.test_visibility.api import TestExcInfo
from ddtrace.ext.test_visibility.api import TestStatus


class UserProperty:
    RETRY_REASON = "dd_retry_reason"
    RETRY_FINAL_OUTCOME = "dd_retry_final_outcome"
    RETRY_NUMBER = "dd_retry_number"


class RetryReason:
    EARLY_FLAKE_DETECTION = "early_flake_detection"
    AUTO_TEST_RETRY = "auto_test_retry"
    ATTEMPT_TO_FIX = "attempt_to_fix"


@dataclass(frozen=True)
class RetryOutcomes:
    PASSED: str
    FAILED: str
    SKIPPED: str
    XFAIL: str
    XPASS: str


def get_retry_num(report: pytest_TestReport) -> t.Optional[int]:
    return get_user_property(report, UserProperty.RETRY_NUMBER)


@contextmanager
def set_retry_num(item: pytest.Item, retry_num: int):
    original_user_properties = item.user_properties
    try:
        item.user_properties = original_user_properties + [(UserProperty.RETRY_NUMBER, retry_num)]
        yield
    finally:
        item.user_properties = original_user_properties


def _get_retry_attempt_string(report: pytest_TestReport) -> str:
    retry_number = get_retry_num(report)
    return "ATTEMPT {} ".format(retry_number) if retry_number else "INITIAL ATTEMPT "


def _get_outcome_from_retry(
    item: pytest.Item,
    outcomes: RetryOutcomes,
    retry_number: int,
) -> _TestOutcome:
    _outcome_status: t.Optional[TestStatus] = None
    _outcome_skip_reason: t.Optional[str] = None
    _outcome_exc_info: t.Optional[TestExcInfo] = None

    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    with set_retry_num(item, retry_number):
        reports = runtestprotocol(item, nextitem=None, log=False)

    if any(report.failed for report in reports):
        _outcome_status = TestStatus.FAIL
    elif any(report.skipped for report in reports):
        _outcome_status = TestStatus.SKIP
    else:
        _outcome_status = TestStatus.PASS

    for report in reports:
        if report.failed:
            report.outcome = outcomes.FAILED
            report_excinfo = excinfo_by_report.get(report)
            _outcome_exc_info = TestExcInfo(report_excinfo.type, report_excinfo.value, report_excinfo.tb)
        elif report.skipped:
            report.outcome = outcomes.SKIPPED
        else:
            report.outcome = outcomes.PASSED

        if report.when == TestPhase.CALL or "passed" not in report.outcome:
            item.ihook.pytest_runtest_logreport(report=report)

    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)

    return _TestOutcome(status=_outcome_status, skip_reason=_outcome_skip_reason, exc_info=_outcome_exc_info)
