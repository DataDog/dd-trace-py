import typing as t

import _pytest
import pytest

from ddtrace.contrib.internal.pytest._retry_utils import RetryOutcomes
from ddtrace.contrib.internal.pytest._retry_utils import RetryReason
from ddtrace.contrib.internal.pytest._retry_utils import UserProperty
from ddtrace.contrib.internal.pytest._retry_utils import _get_outcome_from_retry
from ddtrace.contrib.internal.pytest._retry_utils import _get_retry_attempt_string
from ddtrace.contrib.internal.pytest._retry_utils import set_retry_num
from ddtrace.contrib.internal.pytest._types import _pytest_report_teststatus_return_type
from ddtrace.contrib.internal.pytest._types import pytest_TestReport
from ddtrace.contrib.internal.pytest._utils import PYTEST_STATUS
from ddtrace.contrib.internal.pytest._utils import _get_test_id_from_item
from ddtrace.contrib.internal.pytest._utils import _TestOutcome
from ddtrace.contrib.internal.pytest._utils import get_user_property
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
    EFDTestStatus.ALL_PASS: PYTEST_STATUS.PASSED,
    EFDTestStatus.ALL_FAIL: PYTEST_STATUS.FAILED,
    EFDTestStatus.ALL_SKIP: PYTEST_STATUS.SKIPPED,
    EFDTestStatus.FLAKY: PYTEST_STATUS.PASSED,
}


def efd_handle_retries(
    test_id: InternalTestId,
    item: pytest.Item,
    test_reports: t.Dict[str, pytest_TestReport],
    test_outcome: _TestOutcome,
    is_quarantined: bool = False,
):
    setup_report = test_reports.get("setup")
    call_report = test_reports.get("call")
    teardown_report = test_reports.get("teardown")

    # Overwrite the original result to avoid double-counting when displaying totals in final summary
    if call_report:
        if test_outcome.status == TestStatus.FAIL:
            call_report.outcome = _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED
        elif test_outcome.status == TestStatus.PASS:
            call_report.outcome = _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED
        elif test_outcome.status == TestStatus.SKIP:
            call_report.outcome = _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED

    if InternalTest.get_tag(test_id, "_dd.ci.efd_setup_failed"):
        log.debug("Test item %s failed during setup, will not be retried for Early Flake Detection")
        return

    if InternalTest.get_tag(test_id, "_dd.ci.efd_teardown_failed"):
        # NOTE: tests that passed their call but failed during teardown are not retried
        log.debug("Test item %s failed during teardown, will not be retried for Early Flake Detection")
        return

    # If the test skipped (can happen either in setup or call depending on mark vs calling .skip()), we set the original
    # status as skipped and then continue handling retries because we may not return
    if test_outcome.status == TestStatus.SKIP:
        if call_report:
            call_report.outcome = _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED
        else:
            # When skip happens during setup, we don't have a call report.
            setup_report.outcome = _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED

    item.ihook.pytest_runtest_logreport(report=setup_report)

    if call_report:
        item.ihook.pytest_runtest_logreport(report=call_report)

    efd_outcome = _efd_do_retries(item)
    longrepr = InternalTest.stash_get(test_id, "failure_longrepr")

    final_report = pytest_TestReport(
        nodeid=item.nodeid,
        location=item.location,
        keywords={k: 1 for k in item.keywords},
        when="call",
        longrepr=longrepr,
        outcome=_FINAL_OUTCOMES[efd_outcome],
        user_properties=item.user_properties
        + [
            (UserProperty.RETRY_REASON, RetryReason.EARLY_FLAKE_DETECTION),
            (UserProperty.RETRY_FINAL_OUTCOME, efd_outcome.value),
        ],
    )
    item.ihook.pytest_runtest_logreport(report=final_report)

    item.ihook.pytest_runtest_logreport(report=teardown_report)


def efd_get_failed_reports(terminalreporter: _pytest.terminal.TerminalReporter) -> t.List[pytest_TestReport]:
    return terminalreporter.getreports(_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED)


def _efd_do_retries(item: pytest.Item) -> EFDTestStatus:
    test_id = _get_test_id_from_item(item)

    outcomes = RetryOutcomes(
        PASSED=_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED,
        FAILED=_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED,
        SKIPPED=_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED,
        XFAIL=_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED,
        XPASS=_EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED,
    )

    while InternalTest.efd_should_retry(test_id):
        retry_num = InternalTest.efd_add_retry(test_id, start_immediately=True)

        with set_retry_num(item.nodeid, retry_num):
            retry_outcome = _get_outcome_from_retry(item, outcomes)

        InternalTest.efd_finish_retry(
            test_id, retry_num, retry_outcome.status, retry_outcome.skip_reason, retry_outcome.exc_info
        )

    return InternalTest.efd_get_final_status(test_id)


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


def efd_get_teststatus(report: pytest_TestReport) -> _pytest_report_teststatus_return_type:
    if report.outcome == _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED:
        return (
            _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED,
            "r",
            (f"EFD RETRY {_get_retry_attempt_string(report.nodeid)}PASSED", {"green": True}),
        )
    if report.outcome == _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED:
        return (
            _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED,
            "R",
            (f"EFD RETRY {_get_retry_attempt_string(report.nodeid)}FAILED", {"yellow": True}),
        )
    if report.outcome == _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED:
        return (
            _EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED,
            "s",
            (f"EFD RETRY {_get_retry_attempt_string(report.nodeid)}SKIPPED", {"yellow": True}),
        )

    if get_user_property(report, UserProperty.RETRY_REASON) == RetryReason.EARLY_FLAKE_DETECTION:
        efd_outcome = get_user_property(report, UserProperty.RETRY_FINAL_OUTCOME)
        if efd_outcome == "passed":
            return (_EFD_RETRY_OUTCOMES.EFD_FINAL_PASSED, ".", ("EFD FINAL STATUS: PASSED", {"green": True}))
        if efd_outcome == "failed":
            return (_EFD_RETRY_OUTCOMES.EFD_FINAL_FAILED, "F", ("EFD FINAL STATUS: FAILED", {"red": True}))
        if efd_outcome == "skipped":
            return (_EFD_RETRY_OUTCOMES.EFD_FINAL_SKIPPED, "S", ("EFD FINAL STATUS: SKIPPED", {"yellow": True}))
        if efd_outcome == "flaky":
            # Flaky tests are the only one that have a pretty string because they are intended to be displayed in the
            # final count of terminal summary
            return (_EFD_FLAKY_OUTCOME, "K", ("EFD FINAL STATUS: FLAKY", {"yellow": True}))
    return None
