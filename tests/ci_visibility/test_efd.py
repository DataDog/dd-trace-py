from pathlib import Path
import typing as t
from unittest import mock

import pytest

from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS
from tests.utils import DummyTracer


class TestEFD:
    """Tests that the EFD API correctly handles retry events"""

    def _mock_civisibility(self, efd_settings: EarlyFlakeDetectionSettings):
        with mock.patch.object(CIVisibility, "enabled", True), mock.patch.object(
            CIVisibility, "_instance", mock.Mock()
        ):
            pass

    def test_max_retries(self):
        """Tests that the EFD API correctly handles retry events"""
        pass

    def test_final_status(self):
        """Tests that the EFD API correctly reports the final status of a test"""
        pass

    def test_session_discovery(self):
        """Tests that EFD settings properly carry to the test session when a session is discovered"""


class TestCIVisibilityAPIEFD:
    """Tests that the classes in the CIVisibility API correctly handle EFD"""

    def _get_session_settings(self, efd_settings: EarlyFlakeDetectionSettings):
        return TestVisibilitySessionSettings(
            tracer=DummyTracer(),
            test_service="efd_test_service",
            test_command="efd_test_command",
            test_framework="efd_test_framework",
            test_framework_metric_name=TEST_FRAMEWORKS.MANUAL,
            test_framework_version="0.0",
            session_operation_name="efd_session",
            module_operation_name="efd_module",
            suite_operation_name="efd_suite",
            test_operation_name="efd_test",
            workspace_path=Path().absolute(),
            efd_settings=efd_settings,
        )

    @pytest.mark.parametrize(
        "efd_settings,efd_test_duration_s,expected_max_retries",
        (
            # Test defaults
            (EarlyFlakeDetectionSettings(True), 3.124512, 10),
            (EarlyFlakeDetectionSettings(True), 9.951125, 5),
            (EarlyFlakeDetectionSettings(True), 27.1984, 3),
            (EarlyFlakeDetectionSettings(True), 299.999, 2),
            (EarlyFlakeDetectionSettings(True), 300.0000001, 0),
            # Test custom settings
            (EarlyFlakeDetectionSettings(True, 7, 8, 1, 12), 3.124512, 7),
            (EarlyFlakeDetectionSettings(True, 7, 8, 1, 12), 9.951125, 8),
            (EarlyFlakeDetectionSettings(True, 7, 8, 1, 12), 27.1984, 1),
            (EarlyFlakeDetectionSettings(True, 7, 8, 1, 12), 299.999, 12),
            (EarlyFlakeDetectionSettings(True, 7, 8, 1, 12), 300.0000001, 0),
        ),
    )
    def test_efd_max_retries(self, efd_settings, efd_test_duration_s, expected_max_retries):
        """Tests that the Test class handles EFD API retry events correctly"""
        efd_test = TestVisibilityTest(
            name="efd_test",
            session_settings=self._get_session_settings(efd_settings),
        )

        efd_test.efd_record_duration(efd_test_duration_s)

        num_retry = 0
        while efd_test.efd_should_retry():
            num_retry += 1
            efd_test.efd_add_retry(num_retry)
            efd_test.efd_start_retry(num_retry)
            efd_test.efd_finish_retry(num_retry, TestStatus.PASS)

        assert num_retry == expected_max_retries

    @pytest.mark.parametrize(
        "test_result,retry_results,expected_status",
        (
            # All pass
            (TestStatus.PASS, (TestStatus.PASS, TestStatus.PASS, TestStatus.PASS), TestStatus.PASS),
            # Only original test passed
            (TestStatus.PASS, (TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL), TestStatus.PASS),
            # Only one retry passed
            (TestStatus.FAIL, (TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL, TestStatus.PASS), TestStatus.PASS),
            # Kitchen sink scenarios:
            (TestStatus.FAIL, (TestStatus.PASS, TestStatus.PASS, TestStatus.PASS), TestStatus.PASS),
            (TestStatus.PASS, (TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL, TestStatus.PASS), TestStatus.PASS),
            (TestStatus.PASS, (TestStatus.PASS, TestStatus.SKIP, TestStatus.PASS, TestStatus.FAIL), TestStatus.PASS),
            (TestStatus.FAIL, (TestStatus.PASS, TestStatus.SKIP, TestStatus.PASS, TestStatus.FAIL), TestStatus.PASS),
            (TestStatus.FAIL, (TestStatus.FAIL,), TestStatus.FAIL),
            (TestStatus.FAIL, (TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL), TestStatus.FAIL),
            # No retries happened
            (TestStatus.FAIL, tuple(), TestStatus.FAIL),
        ),
    )
    def test_efd_final_status(self, test_result, retry_results: t.Iterable[TestStatus], expected_status):
        """Tests that the EFD API correctly reports the final status of a test"""
        efd_test = TestVisibilityTest(
            name="efd_test",
            session_settings=self._get_session_settings(EarlyFlakeDetectionSettings(True)),
        )
        efd_test.set_status(test_result)
        efd_test.efd_record_duration(1.0)
        num_retry = 0
        for test_result in retry_results:
            num_retry += 1
            efd_test.efd_add_retry(num_retry, start_immediately=True)
            efd_test.efd_finish_retry(num_retry, test_result)
        assert efd_test.efd_get_final_status() == expected_status

    def test_efd_does_not_retry_if_disabled(self):
        efd_test = TestVisibilityTest(
            name="efd_test",
            session_settings=self._get_session_settings(EarlyFlakeDetectionSettings(True)),
        )
        assert efd_test.efd_should_retry() is False
