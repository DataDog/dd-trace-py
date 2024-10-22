import typing as t

import _pytest
from _pytest.logging import caplog_handler_key
from _pytest.logging import caplog_records_key
import pytest

from ddtrace.contrib.pytest._retry_utils import RetryOutcomes
from ddtrace.contrib.pytest._retry_utils import _efd_get_attempt_string
from ddtrace.contrib.pytest._retry_utils import _retry_run_when
from ddtrace.contrib.pytest._retry_utils import set_retry_num
from ddtrace.contrib.pytest._utils import PYTEST_STATUS
from ddtrace.contrib.pytest._utils import _get_test_id_from_item
from ddtrace.contrib.pytest._utils import _TestOutcome
from ddtrace.ext.test_visibility.api import TestExcInfo
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._efd_mixins import EFDTestStatus
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
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


def efd_handle_retries(
    test_id: InternalTestId,
    item: pytest.Item,
    when: str,
    original_result: _pytest.reports.TestReport,
    test_outcome: _TestOutcome,
):
    # Overwrite the original result to avoid double-counting when displaying totals in final summary
    if when == "call":
        if test_outcome.status == TestStatus.FAIL:
            original_result.outcome = _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED
        elif test_outcome.status == TestStatus.PASS:
            original_result.outcome = _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED
        elif test_outcome.status == TestStatus.SKIP:
            original_result.outcome = _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED
        return
    if InternalTest.get_tag(test_id, "_dd.ci.efd_setup_failed"):
        log.debug("Test item %s failed during setup, will not be retried for Early Flake Detection")
        return
    if InternalTest.get_tag(test_id, "_dd.ci.efd_teardown_failed"):
        # NOTE: tests that passed their call but failed during teardown are not retried
        log.debug("Test item %s failed during teardown, will not be retried for Early Flake Detection")
        return

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


def efd_get_failed_reports(terminalreporter: _pytest.terminal.TerminalReporter) -> t.List[pytest.TestReport]:
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


def efd_pytest_terminal_summary_post_yield(terminalreporter: _pytest.terminal.TerminalReporter):
    terminalreporter.write_sep("=", "Datadog Early Flake Detection", purple=True, bold=True)
    # Print summary info
    raw_summary_strings = []
    markedup_summary_strings = []

    failed_reports = terminalreporter.getreports(_EFD_RETRY_OUTCOMES.EFD_FINAL_FAILED)
    if failed_reports:
        failed_text = f"{len(failed_reports)} failed"
        raw_summary_strings.append(failed_text)
        markedup_summary_strings.append(terminalreporter._tw.markup(failed_text, red=True, bold=True))
        terminalreporter.write_sep("_", "FAILED", red=True, bold=True)
        for failed_report in failed_reports:
            line = f"{terminalreporter._tw.markup('FAILED', red=True)} {failed_report.nodeid}"
            terminalreporter.write_line(line)
            failed_report.outcome = PYTEST_STATUS.FAILED
            terminalreporter.stats.setdefault(PYTEST_STATUS.FAILED, []).append(failed_report)

        del terminalreporter.stats[_EFD_RETRY_OUTCOMES.EFD_FINAL_FAILED]

    passed_reports = terminalreporter.getreports(_EFD_RETRY_OUTCOMES.EFD_FINAL_PASSED)
    if passed_reports:
        passed_text = f"{len(passed_reports)} passed"
        raw_summary_strings.append(passed_text)
        markedup_summary_strings.append(terminalreporter._tw.markup(passed_text, green=True, bold=False))
        terminalreporter.write_sep("_", "PASSED", green=True, bold=True)
        for passed_report in passed_reports:
            line = f"{terminalreporter._tw.markup('PASSED', green=True)} {passed_report.nodeid}"
            terminalreporter.write_line(line)
            passed_report.outcome = PYTEST_STATUS.PASSED
            terminalreporter.stats.setdefault(PYTEST_STATUS.PASSED, []).append(passed_report)
        del terminalreporter.stats[_EFD_RETRY_OUTCOMES.EFD_FINAL_PASSED]

    skipped_reports = terminalreporter.getreports(_EFD_RETRY_OUTCOMES.EFD_FINAL_SKIPPED)
    if skipped_reports:
        skipped_text = f"{len(skipped_reports)} skipped"
        raw_summary_strings.append(skipped_text)
        markedup_summary_strings.append(terminalreporter._tw.markup(skipped_text, yellow=True))
        terminalreporter.write_sep("_", "SKIPPED", yellow=True, bold=True)
        for skipped_report in skipped_reports:
            line = f"{terminalreporter._tw.markup('SKIPPED', yellow=True)} {skipped_report.nodeid}"
            terminalreporter.write_line(line)
            skipped_report.outcome = PYTEST_STATUS.SKIPPED
            terminalreporter.stats.setdefault(PYTEST_STATUS.SKIPPED, []).append(skipped_report)
        del terminalreporter.stats[_EFD_RETRY_OUTCOMES.EFD_FINAL_SKIPPED]

    flaky_reports = terminalreporter.getreports(_EFD_FLAKY_OUTCOME)
    if flaky_reports:
        flaky_text = f"{len(flaky_reports)} flaky"
        raw_summary_strings.append(flaky_text)
        markedup_summary_strings.append(terminalreporter._tw.markup(flaky_text, yellow=True))
        terminalreporter.write_sep("_", "FLAKY", yellow=True, bold=True)
        for flaky_report in flaky_reports:
            line = f"{terminalreporter._tw.markup('FLAKY', yellow=True)} {flaky_report.nodeid}"
            terminalreporter.write_line(line)

    raw_attempt_strings = []
    markedup_attempts_strings = []
    failed_attempts_reports = terminalreporter.getreports(_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED)
    if failed_attempts_reports:
        failed_attempts_text = f"{len(failed_attempts_reports)} failed"
        raw_attempt_strings.append(failed_attempts_text)
        markedup_attempts_strings.append(terminalreporter._tw.markup(failed_attempts_text, red=True, bold=True))
        del terminalreporter.stats[_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED]
    passed_attempts_reports = terminalreporter.getreports(_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED)
    if passed_attempts_reports:
        passed_attempts_text = f"{len(passed_attempts_reports)} passed"
        raw_attempt_strings.append(passed_attempts_text)
        markedup_attempts_strings.append(terminalreporter._tw.markup(passed_attempts_text, green=True, bold=False))
        del terminalreporter.stats[_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED]
    skipped_attempts_reports = terminalreporter.getreports(_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED)
    if skipped_attempts_reports:
        skipped_attempts_text = f"{len(skipped_attempts_reports)} skipped"
        raw_attempt_strings.append(skipped_attempts_text)
        markedup_attempts_strings.append(terminalreporter._tw.markup(skipped_attempts_text, yellow=True))
        del terminalreporter.stats[_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED]

    raw_summary_string = ". ".join(raw_summary_strings)
    # NOTE: find out why bold=False seems to apply to the following string, rather than the current one...
    markedup_summary_string = ", ".join(markedup_summary_strings)

    if markedup_attempts_strings:
        markedup_summary_string += (
            terminalreporter._tw.markup(" (total attempts: ", purple=True)
            + ", ".join(markedup_attempts_strings)
            + terminalreporter._tw.markup(")", purple=True)
        )
        raw_summary_string += f" (total attempts: {', '.join(raw_attempt_strings)})"

    markedup_summary_string += terminalreporter._tw.markup("", purple=True, bold=True)
    if markedup_summary_string.endswith("\x1b[0m"):
        markedup_summary_string = markedup_summary_string[:-4]

    # Print summary counts
    terminalreporter.write_sep("_", "Datadog Early Flake Detection summary", purple=True, bold=True)

    terminalreporter.write_sep(
        " ",
        markedup_summary_string,
        fullwidth=terminalreporter._tw.fullwidth + (len(markedup_summary_string) - len(raw_summary_string)),
        purple=True,
        bold=True,
    )
    terminalreporter.write_sep("=", purple=True, bold=True)


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
