import typing as t

from _pytest.logging import caplog_handler_key
from _pytest.logging import caplog_records_key
import pytest

from ddtrace.contrib.pytest._retry_utils import RetryOutcomes
from ddtrace.contrib.pytest._retry_utils import _efd_get_attempt_string
from ddtrace.contrib.pytest._retry_utils import _retry_run_when
from ddtrace.contrib.pytest._retry_utils import set_retry_num
from ddtrace.contrib.pytest._utils import _get_test_id_from_item
from ddtrace.contrib.pytest._utils import _TestOutcome
from ddtrace.ext.test_visibility.api import TestExcInfo
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._efd_mixins import EFDTestStatus
from ddtrace.internal.test_visibility.api import InternalTest


log = get_logger(__name__)


class _EFD_RETRY_OUTCOMES:
    EFD_ATTEMPT_PASSED = "dd_efd_attempt_passed"
    EFD_ATTEMPT_FAILED = "dd_efd_attempt_failed"
    EFD_ATTEMPT_SKIPPED = "dd_efd_attempt_skipped"
    EFD_FINAL_PASSED = "dd_efd_final_passed"
    EFD_FINAL_FAILED = "dd_efd_final_failed"
    EFD_FINAL_SKIPPED = "dd_efd_final_skipped"
    EFD_FINAL_FLAKY = "dd_efd_final_flaky"


_EFD_FLAKY_OUTCOME = "Flaky"

_FINAL_OUTCOMES: t.Dict[EFDTestStatus, str] = {
    EFDTestStatus.ALL_PASS: _EFD_RETRY_OUTCOMES.EFD_FINAL_PASSED,
    EFDTestStatus.ALL_FAIL: _EFD_RETRY_OUTCOMES.EFD_FINAL_FAILED,
    EFDTestStatus.ALL_SKIP: _EFD_RETRY_OUTCOMES.EFD_FINAL_SKIPPED,
    EFDTestStatus.FLAKY: _EFD_RETRY_OUTCOMES.EFD_FINAL_FLAKY,
}


def efd_handle_retries(test_id, item, when, original_result, test_outcome):
    # Don't mark the original test's call as failed if it will be EFD-retried to avoid failing the session
    # pytest_terminal_summary will then update the counts according to the test's final status
    if when == "call" and test_outcome.status == TestStatus.FAIL:
        original_result.outcome = _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED
        return
    if InternalTest.get_tag(test_id, "_dd.ci.efd_setup_failed"):
        log.debug("Test item %s failed during setup, will not be retried for Early Flake Detection")
        return
    if InternalTest.get_tag(test_id, "_dd.ci.efd_teardown_failed"):
        # NOTE: tests that passed their call but failed during teardown are not retried
        log.debug("Test item %s failed during teardown, will not be retried for Early Flake Detection")
        return

    original_result.outcome = _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED
    efd_outcome = _efd_handle_retries(item, test_outcome)

    final_report = pytest.TestReport(
        nodeid=item.nodeid,
        location=item.location,
        keywords=item.keywords,
        when="call",
        longrepr=None,
        outcome=_FINAL_OUTCOMES[efd_outcome],
    )
    item.ihook.pytest_runtest_logreport(report=final_report)


def efd_get_failed_reports(terminalreporter):
    return terminalreporter.getreports(_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED)


def _efd_handle_retries(item, original_outcome: _TestOutcome, force_teardown: bool = False) -> EFDTestStatus:
    test_id = _get_test_id_from_item(item)

    while InternalTest.efd_should_retry(test_id):
        retry_num = InternalTest.efd_add_retry(test_id, start_immediately=True)

        with set_retry_num(item.nodeid, retry_num):
            retry_outcome = _efd_get_outcome_from_item(item, None, force_teardown)

        InternalTest.efd_finish_retry(
            test_id, retry_num, retry_outcome.status, retry_outcome.skip_reason, retry_outcome.exc_info
        )

    return InternalTest.efd_get_final_status(test_id)


def _efd_get_outcome_from_item(
    item: pytest.Item, nextitem: t.Optional[pytest.Item], force_teardown: bool = False
) -> _TestOutcome:
    _outcome_status: t.Optional[TestStatus] = None
    _outcome_skip_reason: t.Optional[str] = None
    _outcome_exc_info: t.Optional[TestExcInfo] = None

    if force_teardown:
        # Clear any previous fixtures:
        t_call = pytest.CallInfo.from_call(
            lambda: item.ihook.pytest_runtest_teardown(
                item=item, nextitem=pytest.Class.from_parent(item.session, name="Fakeboi")
            ),
            when="teardown",
        )
        if t_call.excinfo is not None:
            log.debug("Error during initial EFD teardoown", exc_info=True)
            return _TestOutcome(
                status=TestStatus.FAIL,
                exc_info=TestExcInfo(t_call.excinfo.type, t_call.excinfo.value, t_call.excinfo.tb),
            )

    # _initrequest() needs to be called first because the test has already executed once
    item._initrequest()

    outcomes = RetryOutcomes(
        PASSED=_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED,
        FAILED=_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED,
        SKIPPED=_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED,
        XFAIL=_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED,
        XPASS=_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED,
    )

    # Setup
    setup_call, setup_report = _retry_run_when(item, "setup", outcomes)
    if setup_report.failed:
        _outcome_status = TestStatus.FAIL
        if setup_call.excinfo is not None:
            _outcome_exc_info = TestExcInfo(setup_call.excinfo.type, setup_call.excinfo.value, setup_call.excinfo.tb)
            item.stash[caplog_records_key] = {}
            item.stash[caplog_handler_key] = {}
    # Call
    if setup_report.outcome == _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED:
        call_call, call_report = _retry_run_when(item, "call", outcomes)
        if call_report.outcome == _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED:
            _outcome_status = TestStatus.FAIL
            if call_call.excinfo is not None:
                _outcome_exc_info = TestExcInfo(call_call.excinfo.type, call_call.excinfo.value, call_call.excinfo.tb)
                item.stash[caplog_records_key] = {}
                item.stash[caplog_handler_key] = {}
        elif call_report.skipped:
            # A test that skips during retries is considered a failure
            _outcome_status = TestStatus.FAIL
        elif call_report.outcome == _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED:
            _outcome_status = TestStatus.PASS

    # Teardown
    teardown_call, teardown_report = _retry_run_when(item, "teardown", outcomes)
    # Only override the outcome if the teardown failed, otherwise defer to either setup or call outcome
    if teardown_report.outcome == _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED:
        _outcome_status = TestStatus.FAIL
        if teardown_call.excinfo is not None:
            _outcome_exc_info = TestExcInfo(
                teardown_call.excinfo.type, teardown_call.excinfo.value, teardown_call.excinfo.tb
            )
            item.stash[caplog_records_key] = {}
            item.stash[caplog_handler_key] = {}

    item._initrequest()

    return _TestOutcome(status=_outcome_status, skip_reason=_outcome_skip_reason, exc_info=_outcome_exc_info)


def efd_pytest_terminal_summary(terminalreporter):
    terminalreporter.write_sep("=", "Datadog Early Flake Detection", purple=True, bold=True)
    # Print summary info
    passed_reports = terminalreporter.getreports(_EFD_RETRY_OUTCOMES.EFD_FINAL_PASSED)
    if passed_reports:
        terminalreporter.write_sep("_", "PASSED", green=True, bold=True)
        for passed_report in passed_reports:
            line = f"{terminalreporter._tw.markup('PASSED', green=True)} {passed_report.nodeid}"
            terminalreporter.write_line(line)
        del terminalreporter.stats[_EFD_RETRY_OUTCOMES.EFD_FINAL_PASSED]
    failed_reports = terminalreporter.getreports(_EFD_RETRY_OUTCOMES.EFD_FINAL_FAILED)
    if failed_reports:
        terminalreporter.write_sep("_", "FAILED", red=True, bold=True)
        for failed_report in failed_reports:
            line = f"{terminalreporter._tw.markup('FAILED', red=True)} {failed_report.nodeid}"
            terminalreporter.write_line(line)
        del terminalreporter.stats[_EFD_RETRY_OUTCOMES.EFD_FINAL_FAILED]
    skipped_reports = terminalreporter.getreports(_EFD_RETRY_OUTCOMES.EFD_FINAL_SKIPPED)
    if skipped_reports:
        terminalreporter.write_sep("_", "SKIPPED", yellow=True, bold=True)
        for skipped_report in skipped_reports:
            line = f"{terminalreporter._tw.markup('SKIPPED', yellow=True)} {skipped_report.nodeid}"
            terminalreporter.write_line(line)
        del terminalreporter.stats[_EFD_RETRY_OUTCOMES.EFD_FINAL_SKIPPED]
    flaky_reports = terminalreporter.getreports(_EFD_FLAKY_OUTCOME)
    if flaky_reports:
        terminalreporter.write_sep("_", "FLAKY", yellow=True, bold=True)
        for flaky_report in flaky_reports:
            line = f"{terminalreporter._tw.markup('FLAKY', yellow=True)} {flaky_report.nodeid}"
            terminalreporter.write_line(line)
    # Re-categorize failed, passed, and skipped into their final counts so they show up in the correct summary
    for efd_status, pytest_status in [
        (_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED, "failed"),
        (_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED, "passed"),
        (_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED, "skipped"),
    ]:
        reports = terminalreporter.stats.pop(efd_status, [])
        if reports:
            terminalreporter.stats.setdefault(pytest_status, []).extend(reports)


def efd_get_teststatus(report: pytest.TestReport) -> t.Optional[pytest.TestShortLogReport]:
    if report.outcome == _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED:
        return pytest.TestShortLogReport(
            _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED,
            "r",
            (f"EFD RETRY {_efd_get_attempt_string(report.nodeid)}PASSED", {"green": True}),
        )
    if report.outcome == _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED:
        return pytest.TestShortLogReport(
            _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED,
            "R",
            (f"EFD RETRY {_efd_get_attempt_string(report.nodeid)}FAILED", {"yellow": True}),
        )
    if report.outcome == _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED:
        return pytest.TestShortLogReport(
            _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED,
            "s",
            (f"EFD RETRY {_efd_get_attempt_string(report.nodeid)}SKIPPED", {"yellow": True}),
        )
    if report.outcome == _EFD_RETRY_OUTCOMES.EFD_FINAL_PASSED:
        return pytest.TestShortLogReport(
            _EFD_RETRY_OUTCOMES.EFD_FINAL_PASSED, ".", ("EFD FINAL STATUS: PASSED", {"green": True})
        )
    if report.outcome == _EFD_RETRY_OUTCOMES.EFD_FINAL_FAILED:
        return pytest.TestShortLogReport(
            _EFD_RETRY_OUTCOMES.EFD_FINAL_FAILED, "F", ("EFD FINAL STATUS: FAILED", {"red": True})
        )
    if report.outcome == _EFD_RETRY_OUTCOMES.EFD_FINAL_SKIPPED:
        return pytest.TestShortLogReport(
            _EFD_RETRY_OUTCOMES.EFD_FINAL_SKIPPED, "S", ("EFD FINAL STATUS: SKIPPED", {"yellow": True})
        )
    if report.outcome == _EFD_RETRY_OUTCOMES.EFD_FINAL_FLAKY:
        # Flaky tests are the only one that have a pretty string because they are intended to be displayed in the final
        # count of terminal summary
        return pytest.TestShortLogReport(_EFD_FLAKY_OUTCOME, "K", ("EFD FINAL STATUS: FLAKY", {"yellow": True}))
    return None
