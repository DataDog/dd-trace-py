from contextlib import contextmanager
from dataclasses import dataclass
import typing as t

import _pytest
from _pytest.runner import CallInfo
import pytest

from ddtrace.internal import core


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


def _efd_get_attempt_string(nodeid) -> str:
    retry_number = get_retry_num(nodeid)
    return "ATTEMPT {} ".format(retry_number) if retry_number is not None else "INITIAL ATTEMPT "


def _retry_run_when(item, when, outcomes: RetryOutcomes) -> t.Tuple[CallInfo, _pytest.reports.TestReport]:
    hooks = {
        "setup": item.ihook.pytest_runtest_setup,
        "call": item.ihook.pytest_runtest_call,
        "teardown": item.ihook.pytest_runtest_teardown,
    }
    hook = hooks[when]
    # NOTE: we use nextitem=item here to make sure that logs don't generate a new line
    if when == "teardown":
        call = CallInfo.from_call(
            lambda: hook(item=item, nextitem=pytest.Class.from_parent(item.session, name="forced_teardown")), when=when
        )
    else:
        call = CallInfo.from_call(lambda: hook(item=item), when=when)
    report = pytest.TestReport.from_item_and_call(item=item, call=call)
    if report.outcome == "passed":
        report.outcome = outcomes.PASSED
    elif report.outcome == "failed" or report.outcome == "error":
        report.outcome = outcomes.FAILED
    elif report.outcome == "skipped":
        report.outcome = outcomes.SKIPPED
    # Only log for actual test calls, or failures
    if when == "call" or "passed" not in report.outcome:
        item.ihook.pytest_runtest_logreport(report=report)
    return call, report
