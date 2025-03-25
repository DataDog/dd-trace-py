import typing as t

import _pytest
import pytest

from ddtrace.contrib.internal.pytest._retry_utils import RetryOutcomes
from ddtrace.contrib.internal.pytest._retry_utils import RetryTestReport
from ddtrace.contrib.internal.pytest._retry_utils import _get_outcome_from_retry
from ddtrace.contrib.internal.pytest._retry_utils import _get_retry_attempt_string
from ddtrace.contrib.internal.pytest._retry_utils import set_retry_num
from ddtrace.contrib.internal.pytest._types import _pytest_report_teststatus_return_type
from ddtrace.contrib.internal.pytest._types import pytest_TestReport
from ddtrace.contrib.internal.pytest._utils import _get_test_id_from_item
from ddtrace.contrib.internal.pytest._utils import _TestOutcome
from ddtrace.contrib.internal.pytest.constants import USER_PROPERTY_QUARANTINED
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
from ddtrace.internal.test_visibility.api import InternalTest


log = get_logger(__name__)


class _RETRY_OUTCOMES:
    ATTEMPT_PASSED = "dd_fix_attempt_passed"
    ATTEMPT_FAILED = "dd_fix_attempt_failed"
    ATTEMPT_SKIPPED = "dd_fix_attempt_skipped"
    FINAL_PASSED = "dd_fix_final_passed"
    FINAL_FAILED = "dd_fix_final_failed"
    FINAL_SKIPPED = "dd_fix_final_skipped"


_FINAL_OUTCOMES: t.Dict[TestStatus, str] = {
    TestStatus.PASS: _RETRY_OUTCOMES.FINAL_PASSED,
    TestStatus.FAIL: _RETRY_OUTCOMES.FINAL_FAILED,
    TestStatus.SKIP: _RETRY_OUTCOMES.FINAL_SKIPPED,
}


def attempt_to_fix_handle_retries(
    test_id: InternalTestId,
    item: pytest.Item,
    when: str,
    original_result: pytest_TestReport,
    test_outcome: _TestOutcome,
):
    retry_outcomes = _RETRY_OUTCOMES
    final_outcomes = _FINAL_OUTCOMES

    outcomes = RetryOutcomes(
        PASSED=retry_outcomes.ATTEMPT_PASSED,
        FAILED=retry_outcomes.ATTEMPT_FAILED,
        SKIPPED=retry_outcomes.ATTEMPT_SKIPPED,
        XFAIL=retry_outcomes.ATTEMPT_PASSED,
        XPASS=retry_outcomes.ATTEMPT_FAILED,
    )

    # Overwrite the original result to avoid double-counting when displaying totals in final summary
    if when == "call":
        if test_outcome.status == TestStatus.FAIL:
            original_result.outcome = outcomes.FAILED
        elif test_outcome.status == TestStatus.SKIP:
            original_result.outcome = outcomes.SKIPPED
        return

    retries_outcome = _do_retries(item, outcomes)
    longrepr = InternalTest.stash_get(test_id, "failure_longrepr")

    final_report = RetryTestReport(
        nodeid=item.nodeid,
        location=item.location,
        keywords=item.keywords,
        when="call",
        longrepr=longrepr,
        outcome=final_outcomes[retries_outcome],
        user_properties=item.user_properties,
    )
    item.ihook.pytest_runtest_logreport(report=final_report)


def _do_retries(item: pytest.Item, outcomes: RetryOutcomes) -> TestStatus:
    test_id = _get_test_id_from_item(item)

    while InternalTest.attempt_to_fix_should_retry(test_id):
        retry_num = InternalTest.attempt_to_fix_add_retry(test_id, start_immediately=True)

        with set_retry_num(item.nodeid, retry_num):
            retry_outcome = _get_outcome_from_retry(item, outcomes)

        InternalTest.attempt_to_fix_finish_retry(
            test_id, retry_num, retry_outcome.status, retry_outcome.skip_reason, retry_outcome.exc_info
        )

    return InternalTest.attempt_to_fix_get_final_status(test_id)


def attempt_to_fix_get_teststatus(report: pytest_TestReport) -> _pytest_report_teststatus_return_type:
    if report.outcome == _RETRY_OUTCOMES.ATTEMPT_PASSED:
        return (
            _RETRY_OUTCOMES.ATTEMPT_PASSED,
            "r",
            (f"ATTEMPT TO FIX RETRY {_get_retry_attempt_string(report.nodeid)}PASSED", {"green": True}),
        )
    if report.outcome == _RETRY_OUTCOMES.ATTEMPT_FAILED:
        return (
            _RETRY_OUTCOMES.ATTEMPT_FAILED,
            "R",
            (f"ATTEMPT TO FIX RETRY {_get_retry_attempt_string(report.nodeid)}FAILED", {"yellow": True}),
        )
    if report.outcome == _RETRY_OUTCOMES.ATTEMPT_SKIPPED:
        return (
            _RETRY_OUTCOMES.ATTEMPT_SKIPPED,
            "s",
            (f"ATTEMPT TO FIX RETRY {_get_retry_attempt_string(report.nodeid)}SKIPPED", {"yellow": True}),
        )
    if report.outcome == _RETRY_OUTCOMES.FINAL_PASSED:
        return (_RETRY_OUTCOMES.FINAL_PASSED, ".", ("ATTEMPT TO FIX FINAL STATUS: PASSED", {"green": True}))
    if report.outcome == _RETRY_OUTCOMES.FINAL_FAILED:
        return (_RETRY_OUTCOMES.FINAL_FAILED, "F", ("ATTEMPT TO FIX FINAL STATUS: FAILED", {"red": True}))
    if report.outcome == _RETRY_OUTCOMES.FINAL_SKIPPED:
        return (_RETRY_OUTCOMES.FINAL_SKIPPED, "s", ("ATTEMPT TO FIX FINAL STATUS: SKIPPED", {"yellow": True}))
    return None


def attempt_to_fix_pytest_terminal_summary_post_yield(terminalreporter: _pytest.terminal.TerminalReporter):
    # Flaky tests could have passed their initial attempt, so they need to be removed from the passed stats to avoid
    # overcounting:
    flaky_node_ids = {report.nodeid for report in terminalreporter.stats.get(_RETRY_OUTCOMES.FINAL_FAILED, [])}
    passed_reports = terminalreporter.stats.get("passed")
    if passed_reports:
        terminalreporter.stats["passed"] = [report for report in passed_reports if report.nodeid not in flaky_node_ids]

    terminalreporter.stats.pop(_RETRY_OUTCOMES.ATTEMPT_PASSED, None)
    terminalreporter.stats.pop(_RETRY_OUTCOMES.ATTEMPT_FAILED, None)
    terminalreporter.stats.pop(_RETRY_OUTCOMES.ATTEMPT_SKIPPED, None)
    terminalreporter.stats.pop(_RETRY_OUTCOMES.FINAL_PASSED, None)

    failed_tests = terminalreporter.stats.pop(_RETRY_OUTCOMES.FINAL_FAILED, [])
    for report in failed_tests:
        if USER_PROPERTY_QUARANTINED not in report.user_properties:
            terminalreporter.stats.setdefault("failed", []).append(report)

    skipped_tests = terminalreporter.stats.pop(_RETRY_OUTCOMES.FINAL_SKIPPED, [])
    for report in skipped_tests:
        if USER_PROPERTY_QUARANTINED not in report.user_properties:
            terminalreporter.stats.setdefault("skipped", []).append(report)

    # TODO: report list of attempt-to-fix results.
