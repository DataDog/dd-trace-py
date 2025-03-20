import typing as t
import unittest

from ddtrace.contrib.internal.unittest.constants import ATR_RETRY_OUTCOMES
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
from ddtrace.internal.test_visibility.api import InternalTest


log = get_logger(__name__)

_FINAL_OUTCOMES: t.Dict[TestStatus, str] = {
    TestStatus.PASS: ATR_RETRY_OUTCOMES.ATR_FINAL_PASSED,
    TestStatus.FAIL: ATR_RETRY_OUTCOMES.ATR_FINAL_FAILED,
}


def atr_handle_retries(
    test_id: InternalTestId,
    test_item: unittest.TestCase,
    result: unittest.TestResult,
    test_outcome: TestStatus,
    is_quarantined: bool = False,
):
    """
    Handles ATR retries for a test item.

    Args:
        test_id: The internal ID of the test
        test_item: The unittest TestCase instance
        result: The unittest TestResult instance
        test_outcome: The outcome of the test (PASS, FAIL, SKIP)
        is_quarantined: Whether the test is in quarantine mode
    """
    # Overwrite the original result to avoid double-counting when displaying totals in final summary
    if test_outcome == TestStatus.FAIL:
        setattr(test_item, "_dd_atr_attempt_outcome", ATR_RETRY_OUTCOMES.ATR_ATTEMPT_FAILED)
    elif test_outcome == TestStatus.PASS:
        # For ATR, we don't retry passing tests, so just return
        return
    elif test_outcome == TestStatus.SKIP:
        setattr(test_item, "_dd_atr_attempt_outcome", ATR_RETRY_OUTCOMES.ATR_ATTEMPT_SKIPPED)
        # For ATR, we don't retry skipped tests
        return

    atr_outcome = _atr_do_retries(test_item, test_id)

    # Store final ATR status on test item
    setattr(test_item, "_dd_atr_final_outcome", _FINAL_OUTCOMES[atr_outcome])


def _atr_do_retries(test_item: unittest.TestCase, test_id: InternalTestId) -> TestStatus:
    """
    Execute ATR retries for a test item.

    Args:
        test_item: The unittest TestCase instance
        test_id: The internal test ID

    Returns:
        The final test status
    """
    while InternalTest.atr_should_retry(test_id):
        retry_num = InternalTest.atr_add_retry(test_id, start_immediately=True)

        # Store retry number on test item
        setattr(test_item, "_dd_atr_retry_num", retry_num)

        # Run the test again
        test_method = getattr(test_item, test_item._testMethodName)
        setup = getattr(test_item, "setUp", lambda: None)
        teardown = getattr(test_item, "tearDown", lambda: None)

        try:
            setup()
            test_method()
            status = TestStatus.PASS
            skip_reason = None
            exc_info = None
        except unittest.SkipTest as e:
            status = TestStatus.SKIP
            skip_reason = str(e)
            exc_info = None
        except Exception as e:
            status = TestStatus.FAIL
            skip_reason = None
            exc_info = (type(e), e, None)  # Simplified exc_info
        finally:
            try:
                teardown()
            except Exception:
                # If teardown fails, the test fails
                status = TestStatus.FAIL

        InternalTest.atr_finish_retry(test_id, retry_num, status, skip_reason, exc_info)

    return InternalTest.atr_get_final_status(test_id)
