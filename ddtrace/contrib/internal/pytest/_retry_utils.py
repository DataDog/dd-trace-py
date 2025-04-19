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
from ddtrace.contrib.internal.pytest._utils import _TestOutcome
from ddtrace.ext.test_visibility.api import TestExcInfo
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal import core

RetryOutcomes = "OBSOLETE"
RetryTestReport = "OBSOLETE"


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
    setup_call, setup_report, setup_outcome = _retry_run_when(item, "setup", retry_number)
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
        call_call, call_report, call_outcome = _retry_run_when(item, "call", retry_number)
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
        teardown_call, teardown_report, teardown_outcome = _retry_run_when(item, "teardown", retry_number)
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


def _retry_run_when(item, when, retry_number: int) -> t.Tuple[CallInfo, _pytest.reports.TestReport]:
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
        ("dd_retry_reason", "auto_test_retry"),
        ("dd_retry_outcome", report.outcome),
        ("dd_retry_number", retry_number),
    ]
    original_outcome = report.outcome
    report.outcome = "retry"

    # Only log for actual test calls, or failures
    if when == "call" or "passed" not in original_outcome:
        item.ihook.pytest_runtest_logreport(report=report)
    return call, report, original_outcome
