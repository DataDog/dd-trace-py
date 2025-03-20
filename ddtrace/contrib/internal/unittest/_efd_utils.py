import typing as t
import unittest

from ddtrace.contrib.internal.unittest.constants import EFD_RETRY_OUTCOMES
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._efd_mixins import EFDTestStatus
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
from ddtrace.internal.test_visibility.api import InternalTest


log = get_logger(__name__)

_FINAL_OUTCOMES: t.Dict[EFDTestStatus, str] = {
    EFDTestStatus.ALL_PASS: EFD_RETRY_OUTCOMES.EFD_FINAL_PASSED,
    EFDTestStatus.ALL_FAIL: EFD_RETRY_OUTCOMES.EFD_FINAL_FAILED,
    EFDTestStatus.ALL_SKIP: EFD_RETRY_OUTCOMES.EFD_FINAL_SKIPPED,
    EFDTestStatus.FLAKY: EFD_RETRY_OUTCOMES.EFD_FINAL_FLAKY,
}


def efd_handle_retries(
    test_id: InternalTestId,
    test_item: unittest.TestCase,
    result: unittest.TestResult,
    test_outcome: TestStatus,
):
    """
    Handles EFD retries for a test item.

    Args:
        test_id: The internal ID of the test
        test_item: The unittest TestCase instance
        result: The unittest TestResult instance
        test_outcome: The outcome of the test (PASS, FAIL, SKIP)
    """
    # Overwrite the original result to avoid double-counting when displaying totals in final summary
    if test_outcome == TestStatus.FAIL:
        setattr(test_item, "_dd_efd_attempt_outcome", EFD_RETRY_OUTCOMES.EFD_ATTEMPT_FAILED)
    elif test_outcome == TestStatus.PASS:
        setattr(test_item, "_dd_efd_attempt_outcome", EFD_RETRY_OUTCOMES.EFD_ATTEMPT_PASSED)
    elif test_outcome == TestStatus.SKIP:
        setattr(test_item, "_dd_efd_attempt_outcome", EFD_RETRY_OUTCOMES.EFD_ATTEMPT_SKIPPED)

    # Skip EFD for tests that failed during setup
    if InternalTest.get_tag(test_id, "_dd.ci.efd_setup_failed"):
        log.debug("Test item %s failed during setup, will not be retried for Early Flake Detection", test_id)
        return

    efd_outcome = _efd_do_retries(test_item, test_id)

    # Store final EFD status on test item
    setattr(test_item, "_dd_efd_final_outcome", _FINAL_OUTCOMES[efd_outcome])


def _efd_do_retries(test_item: unittest.TestCase, test_id: InternalTestId) -> EFDTestStatus:
    """
    Execute EFD retries for a test item.

    Args:
        test_item: The unittest TestCase instance
        test_id: The internal test ID

    Returns:
        The final EFD test status
    """
    while InternalTest.efd_should_retry(test_id):
        retry_num = InternalTest.efd_add_retry(test_id, start_immediately=True)

        # Store retry number on test item
        setattr(test_item, "_dd_efd_retry_num", retry_num)

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

        InternalTest.efd_finish_retry(test_id, retry_num, status, skip_reason, exc_info)

    return InternalTest.efd_get_final_status(test_id)
