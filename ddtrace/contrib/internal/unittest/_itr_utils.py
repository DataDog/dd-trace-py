import os
import unittest

from ddtrace.ext import test
from ddtrace.internal.ci_visibility.constants import ITR_CORRELATION_ID_TAG_NAME
from ddtrace.internal.ci_visibility.constants import ITR_UNSKIPPABLE_REASON
from ddtrace.internal.ci_visibility.constants import SKIPPED_BY_ITR_REASON
from ddtrace.internal.ci_visibility.utils import _generate_fully_qualified_test_name
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _mark_test_as_unskippable(test_object: unittest.TestCase) -> bool:
    """
    Mark a test as unskippable for ITR (Intelligent Test Runner).
    This function is used to decorate test methods with @dd_unskippable and handle tagging.

    Args:
        test_object: The unittest TestCase instance

    Returns:
        True if the test was successfully marked as unskippable, False otherwise
    """

    from ddtrace.contrib.internal.unittest.patch import _extract_module_file_path
    from ddtrace.contrib.internal.unittest.patch import _extract_suite_name_from_test_method
    from ddtrace.internal.ci_visibility import CIVisibility

    if not hasattr(CIVisibility, "_unittest_data"):
        return False

    # Get test identification
    test_module_path = _extract_module_file_path(test_object)
    test_suite_name = _extract_suite_name_from_test_method(test_object)
    test_name = test_object._testMethodName

    # Generate fully qualified test name for marking as unskippable
    test_module_suite_name = _generate_fully_qualified_test_name(test_module_path, test_suite_name, test_name)

    # Mark the test as unskippable
    CIVisibility._unittest_data["unskippable_tests"].add(test_module_suite_name)

    # Get the test method
    test_method = getattr(test_object, test_name, None)
    if test_method:
        # Store the unskippable reason
        setattr(test_method, "_dd_unskippable_reason", ITR_UNSKIPPABLE_REASON.FORCE_RUN)

    return True


def _should_skip_with_itr(test_object: unittest.TestCase) -> bool:
    """
    Determine if a test should be skipped based on ITR (Intelligent Test Runner) data.

    Args:
        test_object: The unittest TestCase instance

    Returns:
        True if the test should be skipped, False otherwise
    """
    from ddtrace.contrib.internal.unittest.patch import _extract_module_file_path
    from ddtrace.contrib.internal.unittest.patch import _extract_suite_name_from_test_method
    from ddtrace.internal.ci_visibility import CIVisibility

    # Get test identification
    test_module_path = _extract_module_file_path(test_object)
    test_suite_name = _extract_suite_name_from_test_method(test_object)
    test_name = test_object._testMethodName

    # Generate fully qualified test name
    test_module_suite_name = _generate_fully_qualified_test_name(test_module_path, test_suite_name, test_name)

    # Check if the test is marked as unskippable
    if (
        hasattr(CIVisibility, "_unittest_data")
        and test_module_suite_name in CIVisibility._unittest_data["unskippable_tests"]
    ):
        return False

    # Check if the test should be skipped based on ITR data
    should_skip = CIVisibility._instance._should_skip_path(test_module_path, test_name)

    if should_skip:
        # Mark the test object as skipped by ITR
        setattr(test_object, "_dd_itr_skip", True)

        # If there's a test span, update its tags
        test_span = getattr(test_object, "_datadog_span", None)
        if test_span:
            test_span.set_tag_str(test.STATUS, test.Status.SKIP.value)
            test_span.set_tag_str(test.SKIP_REASON, SKIPPED_BY_ITR_REASON)

            # Get ITR correlation ID if available
            itr_correlation_id = os.environ.get("DD_CIVISIBILITY_ITR_CORRELATION_ID")
            if itr_correlation_id:
                test_span.set_tag_str(ITR_CORRELATION_ID_TAG_NAME, itr_correlation_id)

    return should_skip


def add_itr_skip_decorator():
    """
    Add the unittest.skip decorator to tests that should be skipped by ITR.
    This function is called during patching to wrap the TestCase class to check
    for ITR skipping before each test runs.
    """
    original_run = unittest.TestCase.run

    def patched_run(self, result=None):
        # Check if this test should be skipped by ITR
        if _should_skip_with_itr(self):
            # Skip the test
            result.addSkip(self, SKIPPED_BY_ITR_REASON)
            return

        # Run the original test
        return original_run(self, result)

    unittest.TestCase.run = patched_run
