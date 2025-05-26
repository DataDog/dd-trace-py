from contextlib import contextmanager
from dataclasses import dataclass
import typing as t

from _pytest.runner import runtestprotocol
import pytest

from ddtrace.contrib.internal.pytest._types import pytest_TestReport
from ddtrace.contrib.internal.pytest._utils import TestPhase
from ddtrace.contrib.internal.pytest._utils import _TestOutcome
from ddtrace.contrib.internal.pytest._utils import excinfo_by_report
from ddtrace.ext.test_visibility.api import TestExcInfo
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal import core
from ddtrace._trace.span import Span


class UserProperty:
    RETRY_REASON = "dd_retry_reason"
    RETRY_FINAL_OUTCOME = "dd_retry_final_outcome"


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


def get_retry_num(nodeid: str) -> t.Optional[int]:
    with core.context_with_data(f"dd-pytest-retry-{nodeid}") as ctx:
        return ctx.get_item("retry_num")


@contextmanager
def set_retry_num(nodeid: str, retry_num: int):
    with core.context_with_data(f"dd-pytest-retry-{nodeid}") as ctx:
        ctx.set_item("retry_num", retry_num)
        yield


def _get_retry_attempt_string(nodeid) -> str:
    retry_number = get_retry_num(nodeid)
    return "ATTEMPT {} ".format(retry_number) if retry_number is not None else "INITIAL ATTEMPT "


def _get_outcome_from_retry(
    item: pytest.Item,
    outcomes: RetryOutcomes,
) -> _TestOutcome:
    _outcome_status: t.Optional[TestStatus] = None
    _outcome_skip_reason: t.Optional[str] = None
    _outcome_exc_info: t.Optional[TestExcInfo] = None

    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
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
            tb = Span._get_traceback(report_excinfo.type, report_excinfo.value, report_excinfo.tb)
            _outcome_exc_info = TestExcInfo(report_excinfo.type, report_excinfo.value, tb)
        elif report.skipped:
            report.outcome = outcomes.SKIPPED
        else:
            report.outcome = outcomes.PASSED

        if report.when == TestPhase.CALL or "passed" not in report.outcome:
            item.ihook.pytest_runtest_logreport(report=report)

    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)

    return _TestOutcome(status=_outcome_status, skip_reason=_outcome_skip_reason, exc_info=_outcome_exc_info)


class RetryTestReport(pytest_TestReport):
    """
    A RetryTestReport behaves just like a normal pytest TestReport, except that the the failed/passed/skipped
    properties are aware of retry final states (dd_efd_final_*, etc). This affects the test counts in JUnit XML output,
    for instance.

    The object should be initialized with the `longrepr` of the _initial_ test attempt. A `longrepr` set to `None` means
    the initial attempt either succeeded (which means it was already counted by pytest) or was quarantined (which means
    we should not count it at all), so we don't need to count it here.
    """

    @property
    def failed(self):
        if self.longrepr is None:
            return False
        return "final_failed" in self.outcome

    @property
    def passed(self):
        if self.longrepr is None:
            return False
        return "final_passed" in self.outcome or "final_flaky" in self.outcome

    @property
    def skipped(self):
        if self.longrepr is None:
            return False
        return "final_skipped" in self.outcome
