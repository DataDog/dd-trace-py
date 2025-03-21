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

# Store ATR statistics for reporting
_atr_stats = {
    "total_retried": 0,
    "recovered": 0,
    "failed": 0,
    "retried_tests": [],
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

    # Track this test in ATR stats
    _atr_stats["total_retried"] += 1
    _atr_stats["retried_tests"].append(test_item)

    atr_outcome = _atr_do_retries(test_item, test_id)

    # Update ATR stats based on final outcome
    if atr_outcome == TestStatus.PASS:
        _atr_stats["recovered"] += 1
    else:
        _atr_stats["failed"] += 1

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

        # Log the retry attempt
        log.debug(
            "ATR: Retry attempt %d for test %s - Status: %s",
            retry_num,
            f"{test_item.__class__.__name__}.{test_item._testMethodName}",
            status,
        )

        InternalTest.atr_finish_retry(test_id, retry_num, status, skip_reason, exc_info)

    return InternalTest.atr_get_final_status(test_id)


def get_retry_stats() -> dict:
    """
    Get ATR statistics for reporting.

    Returns:
        Dictionary with ATR statistics
    """
    return _atr_stats


def patch_test_result():
    """
    Patch unittest.TestResult to handle ATR retry outcomes.

    This ensures that retried tests which eventually pass are not counted as failures
    in the final test count.
    """
    original_wasSuccessful = unittest.TestResult.wasSuccessful

    def patched_wasSuccessful(self):
        # First check original success status
        original_success = original_wasSuccessful(self)

        if not original_success and hasattr(self, "failures"):
            # Check if any failures were recovered by ATR
            actual_failures = []
            for test, _ in self.failures:
                # Only keep failures that weren't recovered by ATR
                if (
                    not hasattr(test, "_dd_atr_final_outcome")
                    or test._dd_atr_final_outcome != ATR_RETRY_OUTCOMES.ATR_FINAL_PASSED
                ):
                    actual_failures.append((test, _))

            # Update failures list with only the actual failures
            self.failures = actual_failures

            # Check if all failures were recovered
            return len(self.failures) == 0 and len(getattr(self, "errors", [])) == 0

        return original_success

    unittest.TestResult.wasSuccessful = patched_wasSuccessful


def print_atr_report():
    """
    Print a summary report of ATR results.

    This should be called at the end of the test run.
    """
    if _atr_stats["total_retried"] == 0:
        return

    print("\n=== Datadog Auto Test Retries ===")
    print(f"Total tests retried: {_atr_stats['total_retried']}")
    print(f"Tests recovered: {_atr_stats['recovered']}")
    print(f"Tests failed after retries: {_atr_stats['failed']}")

    if _atr_stats["recovered"] > 0:
        print("\nRecovered tests:")
        for test in _atr_stats["retried_tests"]:
            if (
                hasattr(test, "_dd_atr_final_outcome")
                and test._dd_atr_final_outcome == ATR_RETRY_OUTCOMES.ATR_FINAL_PASSED
            ):
                print(f"  - {test.__class__.__name__}.{test._testMethodName}")

    if _atr_stats["failed"] > 0:
        print("\nFailed tests after retries:")
        for test in _atr_stats["retried_tests"]:
            if (
                hasattr(test, "_dd_atr_final_outcome")
                and test._dd_atr_final_outcome == ATR_RETRY_OUTCOMES.ATR_FINAL_FAILED
            ):
                print(f"  - {test.__class__.__name__}.{test._testMethodName}")

    print("================================\n")
