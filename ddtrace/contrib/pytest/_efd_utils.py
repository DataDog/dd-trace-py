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
from ddtrace.internal.test_visibility.api import InternalTestSession


log = get_logger(__name__)


class _EFD_RETRY_OUTCOMES:
    EFD_ATTEMPT_PASSED = "dd_efd_attempt_passed"
    EFD_ATTEMPT_FAILED = "dd_efd_attempt_failed"
    EFD_ATTEMPT_SKIPPED = "dd_efd_attempt_skipped"
    EFD_FINAL_PASSED = "dd_efd_final_passed"
    EFD_FINAL_FAILED = "dd_efd_final_failed"
    EFD_FINAL_SKIPPED = "dd_efd_final_skipped"
    EFD_FINAL_FLAKY = "dd_efd_final_flaky"


_EFD_FLAKY_OUTCOME = "flaky"

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

    # If the test skipped (can happen either in setup or call depending on mark vs calling .skip()), we set the original
    # status as skipped and then continue handling retries because we may not return
    if test_outcome.status == TestStatus.SKIP and when in ["setup", "call"]:
        original_result.outcome = _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED
        # We don't return for when == call when skip happens during setup, so we need to log it and make sure the status
        # of the test is set
        if when == "setup":
            item.ihook.pytest_runtest_logreport(
                nodeid=item.nodeid,
                locationm=item.location,
                keywords=item.keywords,
                when="setup",
                longrepr=None,
                outcome=_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED,
            )
            InternalTest.mark_skip(test_id)

    efd_outcome = _efd_handle_retries(item)

    final_report = pytest.TestReport(
        nodeid=item.nodeid,
        location=item.location,
        keywords=item.keywords,
        when="call",
        longrepr=None,
        outcome=_FINAL_OUTCOMES[efd_outcome],
    )
    item.ihook.pytest_runtest_logreport(report=final_report)


def efd_get_failed_reports(terminalreporter: _pytest.terminal.TerminalReporter) -> t.List[_pytest.reports.TestReport]:
    return terminalreporter.getreports(_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED)


def _efd_handle_retries(item: pytest.Item) -> EFDTestStatus:
    test_id = _get_test_id_from_item(item)

    while InternalTest.efd_should_retry(test_id):
        retry_num = InternalTest.efd_add_retry(test_id, start_immediately=True)

        with set_retry_num(item.nodeid, retry_num):
            retry_outcome = _efd_get_outcome_from_item(item)

        InternalTest.efd_finish_retry(
            test_id, retry_num, retry_outcome.status, retry_outcome.skip_reason, retry_outcome.exc_info
        )

    return InternalTest.efd_get_final_status(test_id)


def _efd_get_outcome_from_item(
    item: pytest.Item,
) -> _TestOutcome:
    _outcome_status: t.Optional[TestStatus] = None
    _outcome_skip_reason: t.Optional[str] = None
    _outcome_exc_info: t.Optional[TestExcInfo] = None

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
    if setup_report.outcome == _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED:
        _outcome_status = TestStatus.SKIP

    # Call
    if setup_report.outcome == _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED:
        call_call, call_report = _retry_run_when(item, "call", outcomes)
        if call_report.outcome == _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED:
            _outcome_status = TestStatus.FAIL
            if call_call.excinfo is not None:
                _outcome_exc_info = TestExcInfo(call_call.excinfo.type, call_call.excinfo.value, call_call.excinfo.tb)
                item.stash[caplog_records_key] = {}
                item.stash[caplog_handler_key] = {}
        elif call_report.outcome == _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED:
            _outcome_status = TestStatus.SKIP
        elif call_report.outcome == _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED:
            _outcome_status = TestStatus.PASS

    # Teardown does not happen if setup skipped
    if not setup_report.skipped:
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


def _efd_write_report_for_status(
    terminalreporter: _pytest.terminal.TerminalReporter,
    status_key: str,
    status_text: str,
    report_outcome: str,
    raw_strings: t.List[str],
    markedup_strings: t.List[str],
    color: str,
    delete_reports: bool = True,
):
    reports = terminalreporter.getreports(status_key)
    markup_kwargs = {color: True}
    if reports:
        text = f"{len(reports)} {status_text}"
        raw_strings.append(text)
        markedup_strings.append(terminalreporter._tw.markup(text, **markup_kwargs, bold=True))
        terminalreporter.write_sep("_", status_text.upper(), **markup_kwargs, bold=True)
        for report in reports:
            line = f"{terminalreporter._tw.markup(status_text.upper(), **markup_kwargs)} {report.nodeid}"
            terminalreporter.write_line(line)
            report.outcome = report_outcome
            # Do not re-append a report if a report already exists for the item in the reports
            for existing_reports in terminalreporter.stats.get(report_outcome, []):
                if existing_reports.nodeid == report.nodeid:
                    break
            else:
                terminalreporter.stats.setdefault(report_outcome, []).append(report)
        if delete_reports:
            del terminalreporter.stats[status_key]


def _efd_prepare_attempts_strings(
    terminalreporter: _pytest.terminal.TerminalReporter,
    reports_key: str,
    reports_text: str,
    raw_strings: t.List[str],
    markedup_strings: t.List[str],
    color: str,
    bold: bool = False,
):
    reports = terminalreporter.getreports(reports_key)
    markup_kwargs = {color: True}
    if bold:
        markup_kwargs["bold"] = True
    if reports:
        failed_attempts_text = f"{len(reports)} {reports_text}"
        raw_strings.append(failed_attempts_text)
        markedup_strings.append(terminalreporter._tw.markup(failed_attempts_text, **markup_kwargs))
        del terminalreporter.stats[reports_key]


def efd_pytest_terminal_summary_post_yield(terminalreporter: _pytest.terminal.TerminalReporter):
    terminalreporter.write_sep("=", "Datadog Early Flake Detection", purple=True, bold=True)
    # Print summary info
    raw_summary_strings = []
    markedup_summary_strings = []

    _efd_write_report_for_status(
        terminalreporter,
        _EFD_RETRY_OUTCOMES.EFD_FINAL_FAILED,
        "failed",
        PYTEST_STATUS.FAILED,
        raw_summary_strings,
        markedup_summary_strings,
        color="red",
    )

    _efd_write_report_for_status(
        terminalreporter,
        _EFD_RETRY_OUTCOMES.EFD_FINAL_PASSED,
        "passed",
        PYTEST_STATUS.PASSED,
        raw_summary_strings,
        markedup_summary_strings,
        color="green",
    )

    _efd_write_report_for_status(
        terminalreporter,
        _EFD_RETRY_OUTCOMES.EFD_FINAL_SKIPPED,
        "skipped",
        PYTEST_STATUS.SKIPPED,
        raw_summary_strings,
        markedup_summary_strings,
        color="yellow",
    )

    _efd_write_report_for_status(
        terminalreporter,
        _EFD_FLAKY_OUTCOME,
        _EFD_FLAKY_OUTCOME,
        _EFD_FLAKY_OUTCOME,
        raw_summary_strings,
        markedup_summary_strings,
        color="yellow",
        delete_reports=False,
    )

    # Flaky tests could have passed their initial attempt, so they need to be removed from the passed stats to avoid
    # overcounting:
    flaky_node_ids = {report.nodeid for report in terminalreporter.stats.get(_EFD_FLAKY_OUTCOME, [])}
    passed_reports = terminalreporter.stats.get("passed", [])
    if passed_reports:
        terminalreporter.stats["passed"] = [report for report in passed_reports if report.nodeid not in flaky_node_ids]

    raw_attempt_strings = []
    markedup_attempts_strings = []

    _efd_prepare_attempts_strings(
        terminalreporter,
        _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED,
        "failed",
        raw_attempt_strings,
        markedup_attempts_strings,
        "red",
        bold=True,
    )
    _efd_prepare_attempts_strings(
        terminalreporter,
        _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED,
        "passed",
        raw_attempt_strings,
        markedup_attempts_strings,
        "green",
    )
    _efd_prepare_attempts_strings(
        terminalreporter,
        _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED,
        "skipped",
        raw_attempt_strings,
        markedup_attempts_strings,
        "yellow",
    )

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

    if raw_summary_string:
        terminalreporter.write_sep(
            " ",
            markedup_summary_string,
            fullwidth=terminalreporter._tw.fullwidth + (len(markedup_summary_string) - len(raw_summary_string)),
            purple=True,
            bold=True,
        )
    else:
        if InternalTestSession.efd_is_faulty_session():
            terminalreporter.write_sep(
                " ",
                "No tests were retried because too many were considered new.",
                red=True,
                bold=True,
            )
        else:
            terminalreporter.write_sep(
                " ",
                "No Early Flake Detection results.",
                purple=True,
                bold=True,
            )
    terminalreporter.write_sep("=", purple=True, bold=True)


def efd_get_teststatus(report: _pytest.reports.TestReport) -> t.Optional[pytest.TestShortLogReport]:
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
