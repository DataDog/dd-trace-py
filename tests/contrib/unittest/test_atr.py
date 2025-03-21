import unittest
from unittest import mock

import pytest

from ddtrace.contrib.internal.unittest._atr_utils import _atr_stats
from ddtrace.contrib.internal.unittest._atr_utils import atr_handle_retries
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
from ddtrace.internal.test_visibility.api import InternalTest


class FlakyTest(unittest.TestCase):
    def test_always_passes(self):
        """This test always passes"""
        self.assertTrue(True)

    def test_always_fails(self):
        """This test always fails"""
        self.assertTrue(False)

    counter = 0

    def test_flaky_recovers(self):
        """This test fails first, then passes on retry"""
        FlakyTest.counter += 1
        self.assertTrue(FlakyTest.counter >= 2)


@pytest.fixture
def reset_atr_stats():
    """Reset ATR stats before and after tests"""
    # Reset before test
    _atr_stats["total_retried"] = 0
    _atr_stats["recovered"] = 0
    _atr_stats["failed"] = 0
    _atr_stats["retried_tests"] = []

    yield

    # Reset after test
    _atr_stats["total_retried"] = 0
    _atr_stats["recovered"] = 0
    _atr_stats["failed"] = 0
    _atr_stats["retried_tests"] = []


class TestATR:
    """Test the Automatic Test Retry functionality"""

    def test_atr_handle_retries_passing_test(self, reset_atr_stats):
        """Test that passing tests are not retried"""
        # Create a mock test case that passed
        test_case = FlakyTest("test_always_passes")
        test_id = InternalTestId("test_id")

        # Call atr_handle_retries with TestStatus.PASS
        atr_handle_retries(test_id, test_case, None, TestStatus.PASS)

        # Check that no retries were performed
        assert _atr_stats["total_retried"] == 0
        assert not hasattr(test_case, "_dd_atr_attempt_outcome")

    def test_atr_handle_retries_failing_test(self, reset_atr_stats):
        """Test that failing tests are retried and statistics are updated"""
        # Create a mock test case that failed
        test_case = FlakyTest("test_always_fails")
        test_id = InternalTestId("test_id")

        # Mock the retry behavior
        with mock.patch.object(InternalTest, "atr_should_retry", side_effect=[True, False]):
            with mock.patch.object(InternalTest, "atr_add_retry", return_value=1):
                with mock.patch.object(InternalTest, "atr_finish_retry"):
                    with mock.patch.object(InternalTest, "atr_get_final_status", return_value=TestStatus.FAIL):
                        # Call atr_handle_retries with TestStatus.FAIL
                        atr_handle_retries(test_id, test_case, None, TestStatus.FAIL)

        # Check that retry was performed and statistics updated
        assert _atr_stats["total_retried"] == 1
        assert _atr_stats["failed"] == 1
        assert _atr_stats["recovered"] == 0
        assert hasattr(test_case, "_dd_atr_attempt_outcome")
        assert hasattr(test_case, "_dd_atr_final_outcome")

    def test_atr_recovers_flaky_test(self, reset_atr_stats):
        """Test that a flaky test is recovered by ATR"""
        # Create a mock test case for the flaky test
        test_case = FlakyTest("test_flaky_recovers")
        test_id = InternalTestId("test_id")

        # Reset the counter to ensure test will fail first time
        FlakyTest.counter = 0

        # Mock parts of the retry behavior but allow actual test execution
        with mock.patch.object(InternalTest, "atr_should_retry", side_effect=[True, False]):
            with mock.patch.object(InternalTest, "atr_add_retry", return_value=1):
                with mock.patch.object(InternalTest, "atr_finish_retry"):
                    with mock.patch.object(InternalTest, "atr_get_final_status", return_value=TestStatus.PASS):
                        # Call atr_handle_retries with TestStatus.FAIL
                        atr_handle_retries(test_id, test_case, None, TestStatus.FAIL)

        # Check that retry was performed and statistics updated correctly
        assert _atr_stats["total_retried"] == 1
        assert _atr_stats["failed"] == 0
        assert _atr_stats["recovered"] == 1
        assert hasattr(test_case, "_dd_atr_attempt_outcome")
        assert hasattr(test_case, "_dd_atr_final_outcome")

        # Check that counter was incremented twice (original + 1 retry)
        assert FlakyTest.counter == 2
