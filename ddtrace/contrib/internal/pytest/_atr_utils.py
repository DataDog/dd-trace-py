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
from ddtrace.contrib.internal.pytest._utils import _PYTEST_STATUS
from ddtrace.contrib.internal.pytest._utils import TestPhase
from ddtrace.contrib.internal.pytest._utils import _get_test_id_from_item
from ddtrace.contrib.internal.pytest._utils import _TestOutcome
from ddtrace.contrib.internal.pytest._utils import get_user_property
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
from ddtrace.internal.test_visibility.api import InternalTest


log = get_logger(__name__)


class _ATR_RETRY_OUTCOMES(_PYTEST_STATUS):
    ATR_ATTEMPT_PASSED = "dd_atr_attempt_passed"
    ATR_ATTEMPT_FAILED = "dd_atr_attempt_failed"
    ATR_ATTEMPT_SKIPPED = "dd_atr_attempt_skipped"


class _QUARANTINE_ATR_RETRY_OUTCOMES(_ATR_RETRY_OUTCOMES):
    ATR_ATTEMPT_PASSED = "dd_quarantine_atr_attempt_passed"
    ATR_ATTEMPT_FAILED = "dd_quarantine_atr_attempt_failed"
    ATR_ATTEMPT_SKIPPED = "dd_quarantine_atr_attempt_skipped"
    ATR_FINAL_PASSED = "dd_quarantine_atr_final_passed"
    ATR_FINAL_FAILED = "dd_quarantine_atr_final_failed"


_FINAL_OUTCOMES: t.Dict[TestStatus, str] = {
    TestStatus.PASS: _ATR_RETRY_OUTCOMES.PASSED,
    TestStatus.FAIL: _ATR_RETRY_OUTCOMES.FAILED,
}


_QUARANTINE_FINAL_OUTCOMES: t.Dict[TestStatus, str] = {
    TestStatus.PASS: _QUARANTINE_ATR_RETRY_OUTCOMES.ATR_FINAL_PASSED,
    TestStatus.FAIL: _QUARANTINE_ATR_RETRY_OUTCOMES.ATR_FINAL_FAILED,
}


def atr_handle_retries(
    test_id: InternalTestId,
    item: pytest.Item,
    test_reports: t.Dict[str, pytest_TestReport],
    test_outcome: _TestOutcome,
    is_quarantined: bool = False,
):
    setup_report = test_reports.get(TestPhase.SETUP)
    call_report = test_reports.get(TestPhase.CALL)
    teardown_report = test_reports.get(TestPhase.TEARDOWN)

    if is_quarantined:
        retry_outcomes = _QUARANTINE_ATR_RETRY_OUTCOMES
        final_outcomes = _QUARANTINE_FINAL_OUTCOMES
    else:
        retry_outcomes = _ATR_RETRY_OUTCOMES
        final_outcomes = _FINAL_OUTCOMES

    outcomes = RetryOutcomes(
        PASSED=retry_outcomes.ATR_ATTEMPT_PASSED,
        FAILED=retry_outcomes.ATR_ATTEMPT_FAILED,
        SKIPPED=retry_outcomes.ATR_ATTEMPT_SKIPPED,
        XFAIL=retry_outcomes.ATR_ATTEMPT_PASSED,
        XPASS=retry_outcomes.ATR_ATTEMPT_FAILED,
    )

    item.ihook.pytest_runtest_logreport(report=setup_report)

    # Overwrite the original result to avoid double-counting when displaying totals in final summary
    if call_report:
        if test_outcome.status == TestStatus.FAIL:
            call_report.outcome = outcomes.FAILED

        item.ihook.pytest_runtest_logreport(report=call_report)

    atr_outcome = _atr_do_retries(item, outcomes)
    longrepr = InternalTest.stash_get(test_id, "failure_longrepr")

    final_report = pytest_TestReport(
        nodeid=item.nodeid,
        location=item.location,
        keywords={k: 1 for k in item.keywords},
        when=TestPhase.CALL,
        longrepr=longrepr,
        outcome=final_outcomes[atr_outcome],
        user_properties=item.user_properties + [(UserProperty.RETRY_REASON, RetryReason.AUTO_TEST_RETRY)],
    )
    item.ihook.pytest_runtest_logreport(report=final_report)

    item.ihook.pytest_runtest_logreport(report=teardown_report)


def atr_get_failed_reports(terminalreporter: _pytest.terminal.TerminalReporter) -> t.List[pytest_TestReport]:
    return terminalreporter.getreports(_ATR_RETRY_OUTCOMES.ATR_ATTEMPT_FAILED)


def _atr_do_retries(item: pytest.Item, outcomes: RetryOutcomes) -> TestStatus:
    test_id = _get_test_id_from_item(item)

    while InternalTest.atr_should_retry(test_id):
        retry_num = InternalTest.atr_add_retry(test_id, start_immediately=True)

        with set_retry_num(item.nodeid, retry_num):
            retry_outcome = _get_outcome_from_retry(item, outcomes)

        InternalTest.atr_finish_retry(
            test_id, retry_num, retry_outcome.status, retry_outcome.skip_reason, retry_outcome.exc_info
        )

    return InternalTest.atr_get_final_status(test_id)


def _atr_write_report_for_status(
    terminalreporter: _pytest.terminal.TerminalReporter,
    status_key: str,
    status_text: str,
    report_outcome: str,
    raw_strings: t.List[str],
    markedup_strings: t.List[str],
    color: str,
    delete_reports: bool = True,
    retry_reason: str = RetryReason.AUTO_TEST_RETRY,
):
    reports = [
        report
        for report in terminalreporter.getreports(report_outcome)
        if get_user_property(report, UserProperty.RETRY_REASON) == retry_reason
    ]
    markup_kwargs = {color: True}
    if reports:
        text = f"{len(reports)} {status_text}"
        raw_strings.append(text)
        markedup_strings.append(terminalreporter._tw.markup(text, **markup_kwargs, bold=True))
        terminalreporter.write_sep("_", status_text.upper(), **markup_kwargs, bold=True)
        for report in reports:
            line = f"{terminalreporter._tw.markup(status_text.upper(), **markup_kwargs)} {report.nodeid}"
            terminalreporter.write_line(line)


def _atr_prepare_attempts_strings(
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


def atr_pytest_terminal_summary_post_yield(terminalreporter: _pytest.terminal.TerminalReporter):
    # When there were no ATR attempts to retry tests, there is no need to report anything, but just in case, we clear
    # out any potential leftover data:
    if not terminalreporter.stats.get(_ATR_RETRY_OUTCOMES.ATR_ATTEMPT_FAILED):
        return

    terminalreporter.write_sep("=", "Datadog Auto Test Retries", purple=True, bold=True)
    # Print summary info
    raw_summary_strings = []
    markedup_summary_strings = []

    _atr_write_report_for_status(
        terminalreporter,
        status_key=_ATR_RETRY_OUTCOMES.FAILED,
        status_text="failed",
        report_outcome=_PYTEST_STATUS.FAILED,
        raw_strings=raw_summary_strings,
        markedup_strings=markedup_summary_strings,
        color="red",
    )

    _atr_write_report_for_status(
        terminalreporter,
        status_key=_ATR_RETRY_OUTCOMES.PASSED,
        status_text="passed",
        report_outcome=_PYTEST_STATUS.PASSED,
        raw_strings=raw_summary_strings,
        markedup_strings=markedup_summary_strings,
        color="green",
    )

    raw_attempt_strings = []
    markedup_attempts_strings = []

    _atr_prepare_attempts_strings(
        terminalreporter,
        _ATR_RETRY_OUTCOMES.ATR_ATTEMPT_FAILED,
        "failed",
        raw_attempt_strings,
        markedup_attempts_strings,
        "red",
        bold=True,
    )
    _atr_prepare_attempts_strings(
        terminalreporter,
        _ATR_RETRY_OUTCOMES.ATR_ATTEMPT_PASSED,
        "passed",
        raw_attempt_strings,
        markedup_attempts_strings,
        "green",
    )
    _atr_prepare_attempts_strings(
        terminalreporter,
        _ATR_RETRY_OUTCOMES.ATR_ATTEMPT_SKIPPED,
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
    terminalreporter.write_sep("_", "Datadog Auto Test Retries summary", purple=True, bold=True)

    if raw_summary_string:
        terminalreporter.write_sep(
            " ",
            markedup_summary_string,
            fullwidth=terminalreporter._tw.fullwidth + (len(markedup_summary_string) - len(raw_summary_string)),
            purple=True,
            bold=True,
        )
    else:
        terminalreporter.write_sep(
            " ",
            "No tests were retried.",
            purple=True,
            bold=True,
        )
    terminalreporter.write_sep("=", purple=True, bold=True)


def atr_get_teststatus(report: pytest_TestReport) -> _pytest_report_teststatus_return_type:
    if report.outcome == _ATR_RETRY_OUTCOMES.ATR_ATTEMPT_PASSED:
        return (
            _ATR_RETRY_OUTCOMES.ATR_ATTEMPT_PASSED,
            "r",
            (f"ATR RETRY {_get_retry_attempt_string(report.nodeid)}PASSED", {"green": True}),
        )
    if report.outcome == _ATR_RETRY_OUTCOMES.ATR_ATTEMPT_FAILED:
        return (
            _ATR_RETRY_OUTCOMES.ATR_ATTEMPT_FAILED,
            "R",
            (f"ATR RETRY {_get_retry_attempt_string(report.nodeid)}FAILED", {"yellow": True}),
        )
    if report.outcome == _ATR_RETRY_OUTCOMES.ATR_ATTEMPT_SKIPPED:
        return (
            _ATR_RETRY_OUTCOMES.ATR_ATTEMPT_SKIPPED,
            "s",
            (f"ATR RETRY {_get_retry_attempt_string(report.nodeid)}SKIPPED", {"yellow": True}),
        )
    return None


def quarantine_atr_get_teststatus(report: pytest_TestReport) -> _pytest_report_teststatus_return_type:
    if report.outcome == _QUARANTINE_ATR_RETRY_OUTCOMES.ATR_ATTEMPT_PASSED:
        return (
            _QUARANTINE_ATR_RETRY_OUTCOMES.ATR_ATTEMPT_PASSED,
            "q",
            (f"QUARANTINED RETRY {_get_retry_attempt_string(report.nodeid)}PASSED", {"blue": True}),
        )
    if report.outcome == _QUARANTINE_ATR_RETRY_OUTCOMES.ATR_ATTEMPT_FAILED:
        return (
            _QUARANTINE_ATR_RETRY_OUTCOMES.ATR_ATTEMPT_FAILED,
            "Q",
            (f"QUARANTINED RETRY {_get_retry_attempt_string(report.nodeid)}FAILED", {"blue": True}),
        )
    if report.outcome == _QUARANTINE_ATR_RETRY_OUTCOMES.ATR_ATTEMPT_SKIPPED:
        return (
            _QUARANTINE_ATR_RETRY_OUTCOMES.ATR_ATTEMPT_SKIPPED,
            "q",
            (f"QUARANTINED RETRY {_get_retry_attempt_string(report.nodeid)}SKIPPED", {"blue": True}),
        )
    if report.outcome == _QUARANTINE_ATR_RETRY_OUTCOMES.ATR_FINAL_PASSED:
        return (
            _QUARANTINE_ATR_RETRY_OUTCOMES.ATR_FINAL_PASSED,
            ".",
            ("QUARANTINED FINAL STATUS: PASSED", {"blue": True}),
        )
    if report.outcome == _QUARANTINE_ATR_RETRY_OUTCOMES.ATR_FINAL_FAILED:
        return (
            _QUARANTINE_ATR_RETRY_OUTCOMES.ATR_FINAL_FAILED,
            "F",
            ("QUARANTINED FINAL STATUS: FAILED", {"blue": True}),
        )
    return None


def quarantine_pytest_terminal_summary_post_yield(terminalreporter: _pytest.terminal.TerminalReporter):
    terminalreporter.stats.pop(_QUARANTINE_ATR_RETRY_OUTCOMES.ATR_ATTEMPT_PASSED, None)
    terminalreporter.stats.pop(_QUARANTINE_ATR_RETRY_OUTCOMES.ATR_ATTEMPT_FAILED, None)
    terminalreporter.stats.pop(_QUARANTINE_ATR_RETRY_OUTCOMES.ATR_ATTEMPT_SKIPPED, None)
    terminalreporter.stats.pop(_QUARANTINE_ATR_RETRY_OUTCOMES.ATR_FINAL_PASSED, [])
    terminalreporter.stats.pop(_QUARANTINE_ATR_RETRY_OUTCOMES.ATR_FINAL_FAILED, [])

    # TODO: report list of fully failed quarantined tests, possibly inside the ATR report.
