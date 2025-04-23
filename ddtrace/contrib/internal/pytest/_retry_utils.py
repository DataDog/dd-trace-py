from contextlib import contextmanager
from dataclasses import dataclass
import typing as t

import _pytest
from _pytest.logging import caplog_handler_key
from _pytest.logging import caplog_records_key
from _pytest.runner import CallInfo
import pytest

from ddtrace.contrib.internal.pytest._types import pytest_TestReport
from ddtrace.contrib.internal.pytest._types import tmppath_result_key
from ddtrace.internal.ci_visibility.api._retry import RetryManager
from ddtrace.contrib.internal.pytest._utils import _TestOutcome
from ddtrace.ext.test_visibility.api import TestExcInfo
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal import core


RetryOutcomes = "OBSOLETE"
RetryTestReport = "OBSOLETE"

RETRY_OUTCOME = "dd_retry"

def get_retry_num(nodeid: str) -> t.Optional[int]:
    with core.context_with_data(f"dd-pytest-retry-{nodeid}") as ctx:
        return ctx.get_item("retry_num")


@contextmanager
def set_retry_num(nodeid: str, retry_num: int):
    with core.context_with_data(f"dd-pytest-retry-{nodeid}") as ctx:
        ctx.set_item("retry_num", retry_num)
        yield


def _get_retry_attempt_string(retry_number) -> str:
    return "ATTEMPT {} ".format(retry_number) if retry_number else "INITIAL ATTEMPT "


def _get_outcome_from_retry(
    item: pytest.Item,
    retry_manager: RetryManager,
    retry_number: int,
) -> _TestOutcome:
    class outcomes:
        PASSED = "passed"
        FAILED = "failed"
        SKIPPED = "skipped"
        XFAIL = "xfail"
        XPASS = "xpass"

    _outcome_status: t.Optional[TestStatus] = None
    _outcome_skip_reason: t.Optional[str] = None
    _outcome_exc_info: t.Optional[TestExcInfo] = None

    # _initrequest() needs to be called first because the test has already executed once
    item._initrequest()

    # Reset output capture across retries.
    item._report_sections = []

    # Setup
    setup_call, setup_report, setup_outcome = _retry_run_when(retry_manager, item, "setup", retry_number)
    if setup_outcome == outcomes.FAILED:
        _outcome_status = TestStatus.FAIL
        if setup_call.excinfo is not None:
            _outcome_exc_info = TestExcInfo(setup_call.excinfo.type, setup_call.excinfo.value, setup_call.excinfo.tb)
            item.stash[caplog_records_key] = {}
            item.stash[caplog_handler_key] = {}
            if tmppath_result_key is not None:
                item.stash[tmppath_result_key] = {}
    if setup_outcome == outcomes.SKIPPED:
        _outcome_status = TestStatus.SKIP

    # Call
    if setup_outcome == outcomes.PASSED:
        call_call, call_report, call_outcome = _retry_run_when(retry_manager, item, "call", retry_number)
        if call_outcome == outcomes.FAILED:
            _outcome_status = TestStatus.FAIL
            if call_call.excinfo is not None:
                _outcome_exc_info = TestExcInfo(call_call.excinfo.type, call_call.excinfo.value, call_call.excinfo.tb)
                item.stash[caplog_records_key] = {}
                item.stash[caplog_handler_key] = {}
                if tmppath_result_key is not None:
                    item.stash[tmppath_result_key] = {}
        elif call_outcome == outcomes.SKIPPED:
            _outcome_status = TestStatus.SKIP
        elif call_outcome == outcomes.PASSED:
            _outcome_status = TestStatus.PASS

    # Teardown does not happen if setup skipped
    if not setup_outcome == outcomes.SKIPPED:
        teardown_call, teardown_report, teardown_outcome = _retry_run_when(retry_manager, item, "teardown", retry_number)
        # Only override the outcome if the teardown failed, otherwise defer to either setup or call outcome
        if teardown_outcome == outcomes.FAILED:
            _outcome_status = TestStatus.FAIL
            if teardown_call.excinfo is not None:
                _outcome_exc_info = TestExcInfo(
                    teardown_call.excinfo.type, teardown_call.excinfo.value, teardown_call.excinfo.tb
                )
                item.stash[caplog_records_key] = {}
                item.stash[caplog_handler_key] = {}
                if tmppath_result_key is not None:
                    item.stash[tmppath_result_key] = {}

    item._initrequest()

    return _TestOutcome(status=_outcome_status, skip_reason=_outcome_skip_reason, exc_info=_outcome_exc_info)


def _retry_run_when(retry_manager, item, when, retry_number: int) -> t.Tuple[CallInfo, _pytest.reports.TestReport]:
    hooks = {
        "setup": item.ihook.pytest_runtest_setup,
        "call": item.ihook.pytest_runtest_call,
        "teardown": item.ihook.pytest_runtest_teardown,
    }
    hook = hooks[when]
    # NOTE: we use nextitem=item here to make sure that logs don't generate a new line
    #  ^ ꙮ I don't understand what this means. nextitem is not item here.
    if when == "teardown":
        call = CallInfo.from_call(
            lambda: hook(item=item, nextitem=pytest.Class.from_parent(item.session, name="forced_teardown")), when=when
        )
    else:
        call = CallInfo.from_call(lambda: hook(item=item), when=when)
    report = item.ihook.pytest_runtest_makereport(item=item, call=call)
    report.user_properties += [
        ("dd_retry_reason", retry_manager.retry_reason),
        ("dd_retry_outcome", report.outcome),
        ("dd_retry_number", retry_number),
    ]
    original_outcome = report.outcome
    report.outcome = RETRY_OUTCOME

    # Only log for actual test calls, or failures
    if when == "call" or "passed" not in original_outcome:
        item.ihook.pytest_runtest_logreport(report=report)
    return call, report, original_outcome


#######

import functools
import typing as t

import _pytest
import pytest

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
        #if test_outcome.status == TestStatus.FAIL:
        original_result.user_properties += [
            ("dd_retry_reason", retry_manager.retry_reason),
            ("dd_retry_outcome", original_result.outcome),
            ("dd_retry_number", 0),
        ]
        original_result.outcome = RETRY_OUTCOME
        return

    outcome, retry_outcome = _do_retries(retry_manager, item)
    longrepr = InternalTest.stash_get(retry_manager.test_id, "failure_longrepr")
    final_user_properties = [
        ("dd_retry_reason", retry_manager.retry_reason),
        ("dd_retry_outcome", retry_outcome or _FINAL_OUTCOMES[outcome]),
    ]
    final_report = pytest_TestReport(
        nodeid=item.nodeid,
        location=item.location,
        keywords={k: 1 for k in item.keywords},  # <https://github.com/pytest-dev/pytest/blob/8.3.x/src/_pytest/reports.py#L345>
        when="call",
        longrepr=longrepr,
        outcome=_FINAL_OUTCOMES[outcome],
        user_properties=item.user_properties + final_user_properties,
    )
    item.ihook.pytest_runtest_logreport(report=final_report)


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
        color="purple",
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


def retry_get_teststatus(report: pytest_TestReport) -> _pytest_report_teststatus_return_type:
    if report.outcome != RETRY_OUTCOME:
        return None

    retry_outcome = get_user_property(report, "dd_retry_outcome")
    retry_reason = get_user_property(report, "dd_retry_reason")
    retry_number = get_user_property(report, "dd_retry_number")
    if retry_outcome == "passed":
        return (
            RETRY_OUTCOME,
            "r",
            (f"{retry_reason} RETRY {_get_retry_attempt_string(retry_number)}PASSED", {"green": True}),
        )
    if retry_outcome == "failed":
        return (
            RETRY_OUTCOME,
            "R",
            (f"{retry_reason} RETRY {_get_retry_attempt_string(retry_number)}FAILED", {"yellow": True}),
        )
    if retry_outcome == "skipped":
        return (
            RETRY_OUTCOME,
            "s",
            (f"{retry_reason} RETRY {_get_retry_attempt_string(retry_number)}SKIPPED", {"yellow": True}),
        )
    return None
