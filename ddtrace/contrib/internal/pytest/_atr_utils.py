import functools
import typing as t

import _pytest
import pytest

from ddtrace.contrib.internal.pytest._retry_utils import _get_outcome_from_retry
from ddtrace.contrib.internal.pytest._retry_utils import _get_retry_attempt_string
from ddtrace.contrib.internal.pytest._retry_utils import set_retry_num
from ddtrace.contrib.internal.pytest._types import _pytest_report_teststatus_return_type
from ddtrace.contrib.internal.pytest._types import pytest_TestReport
from ddtrace.contrib.internal.pytest._utils import PYTEST_STATUS
from ddtrace.contrib.internal.pytest._utils import _get_test_id_from_item
from ddtrace.contrib.internal.pytest._utils import _TestOutcome
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.ci_visibility.api._retry import ATRRetryManager
from ddtrace.internal.ci_visibility.api._retry import RetryManager
from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
from ddtrace.internal.ci_visibility.recorder import CIVisibility
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
from ddtrace.internal.test_visibility.api import InternalTest


log = get_logger(__name__)


def _debugme(f):
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception:
            import traceback

            traceback.print_exc()
            raise

    return wrapped


_FINAL_OUTCOMES: t.Dict[TestStatus, str] = {
    TestStatus.PASS: "passed",
    TestStatus.FAIL: "failed",
    TestStatus.SKIP: "skipped",
}


@_debugme
def handle_retries(
    retry_manager: RetryManager,
    item: pytest.Item,
    when: str,
    original_result: pytest_TestReport,
    test_outcome: _TestOutcome,
    is_quarantined: bool = False,
):
    # Overwrite the original result to avoid double-counting when displaying totals in final summary
    if when == "call":
        if test_outcome.status == TestStatus.FAIL:
            original_result.user_properties += [
                ("dd_retry_reason", retry_manager.retry_reason),
                ("dd_retry_outcome", original_result.outcome),
                ("dd_retry_number", 0),
            ]
            original_result.outcome = "retry"
        return

    atr_outcome = _do_retries(retry_manager, item)
    longrepr = InternalTest.stash_get(retry_manager.test_id, "failure_longrepr")

    final_report = pytest_TestReport(
        nodeid=item.nodeid,
        location=item.location,
        keywords=item.keywords,
        when="call",
        longrepr=longrepr,
        outcome=_FINAL_OUTCOMES[atr_outcome],
        user_properties=item.user_properties + [("dd_retry_reason", retry_manager.retry_reason)],
    )
    item.ihook.pytest_runtest_logreport(report=final_report)


def atr_get_failed_reports(terminalreporter: _pytest.terminal.TerminalReporter) -> t.List[pytest_TestReport]:
    return terminalreporter.getreports(_ATR_RETRY_OUTCOMES.ATR_ATTEMPT_FAILED)


@_debugme
def _do_retries(retry_manager: RetryManager, item: pytest.Item) -> TestStatus:
    test_id = _get_test_id_from_item(item)

    while retry_manager.should_retry():
        retry_num = retry_manager.add_and_start_retry()

        with set_retry_num(item.nodeid, retry_num):
            retry_outcome = _get_outcome_from_retry(item, retry_manager, retry_num)

        retry_manager.finish_retry(
            retry_number=retry_num,
            status=retry_outcome.status,
            skip_reason=retry_outcome.skip_reason,
            exc_info=retry_outcome.exc_info,
        )

    return retry_manager.get_final_status()


def _write_report_for_status(
    terminalreporter: _pytest.terminal.TerminalReporter,
    retry_reason: str,
    status_text: str,
    report_outcome: str,
    raw_strings: t.List[str],
    markedup_strings: t.List[str],
    color: str,
    delete_reports: bool = True,
):
    reports = [
        report
        for report in terminalreporter.getreports(report_outcome)
        if get_user_property(report, "dd_retry_reason") == retry_reason
        and get_user_property(report, "dd_retry_outcome") == status_text
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


def _prepare_attempts_strings(
    terminalreporter: _pytest.terminal.TerminalReporter,
    number_of_attempts: str,
    reports_text: str,
    raw_strings: t.List[str],
    markedup_strings: t.List[str],
    color: str,
    bold: bool = False,
):
    markup_kwargs = {color: True}
    if bold:
        markup_kwargs["bold"] = True
    if number_of_attempts > 0:
        attempts_text = f"{number_of_attempts} {reports_text}"
        raw_strings.append(attempts_text)
        markedup_strings.append(terminalreporter._tw.markup(attempts_text, **markup_kwargs))


@_debugme
def retry_pytest_terminal_summary_post_yield(retry_class: t.Type[RetryManager], terminalreporter: _pytest.terminal.TerminalReporter):
    # When there were no ATR attempts to retry tests, there is no need to report anything, but just in case, we clear
    # out any potential leftover data:
    #  ^ ꙮ Which data? This just returns.

    session_status = retry_class._get_session_status(CIVisibility.get_session())

    if not session_status.total_retries:
        return

    terminalreporter.write_sep("=", retry_class.report_title, purple=True, bold=True)
    # Print summary info
    raw_summary_strings = []
    markedup_summary_strings = []


    _write_report_for_status(
        terminalreporter,
        retry_reason=retry_class.retry_reason,
        status_text="failed",
        report_outcome=PYTEST_STATUS.FAILED,
        raw_strings=raw_summary_strings,
        markedup_strings=markedup_summary_strings,
        color="red",
    )

    _write_report_for_status(
        terminalreporter,
        retry_reason=retry_class.retry_reason,
        status_text="passed",
        report_outcome=PYTEST_STATUS.PASSED,
        raw_strings=raw_summary_strings,
        markedup_strings=markedup_summary_strings,
        color="green",
    )

    _write_report_for_status(
        terminalreporter,
        retry_reason=retry_class.retry_reason,
        status_text="flaky",
        report_outcome=PYTEST_STATUS.PASSED,
        raw_strings=raw_summary_strings,
        markedup_strings=markedup_summary_strings,
        color="green",
    )

    raw_attempt_strings = []
    markedup_attempts_strings = []

    _prepare_attempts_strings(
        terminalreporter=terminalreporter,
        number_of_attempts=session_status.attempts[TestStatus.FAIL],
        reports_text="failed",
        raw_strings=raw_attempt_strings,
        markedup_strings=markedup_attempts_strings,
        color="red",
        bold=True,
    )
    _prepare_attempts_strings(
        terminalreporter=terminalreporter,
        number_of_attempts=session_status.attempts[TestStatus.PASS],
        reports_text="passed",
        raw_strings=raw_attempt_strings,
        markedup_strings=markedup_attempts_strings,
        color="green",
    )
    _prepare_attempts_strings(
        terminalreporter=terminalreporter,
        number_of_attempts=session_status.attempts[TestStatus.SKIP],
        reports_text="skipped",
        raw_strings=raw_attempt_strings,
        markedup_strings=markedup_attempts_strings,
        color="yellow",
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
    terminalreporter.write_sep("_", f"{retry_class.report_title} summary", purple=True, bold=True)

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


def get_user_property(report, key, default=None):
    for k, v in report.user_properties:
        if k == key:
            return v
    return default


def atr_get_teststatus(report: pytest_TestReport) -> _pytest_report_teststatus_return_type:
    if report.outcome != "retry":
        return None

    retry_outcome = get_user_property(report, "dd_retry_outcome")
    retry_reason = get_user_property(report, "dd_retry_reason")
    if retry_outcome == "passed":
        return (
            "retry",
            "r",
            (f"{retry_reason} RETRY {_get_retry_attempt_string(report.nodeid)}PASSED", {"green": True}),
        )
    if retry_outcome == "failed":
        return (
            "retry",
            "R",
            (f"{retry_reason} RETRY {_get_retry_attempt_string(report.nodeid)}FAILED", {"yellow": True}),
        )
    if retry_outcome == "skipped":
        return (
            "retry",
            "s",
            (f"{retry_reason} RETRY {_get_retry_attempt_string(report.nodeid)}SKIPPED", {"yellow": True}),
        )
    return None
