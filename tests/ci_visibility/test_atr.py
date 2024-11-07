from pathlib import Path
import typing as t
from unittest import mock

import pytest

from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api._session import TestVisibilitySession
from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS
from ddtrace.internal.test_visibility._atr_mixins import AutoTestRetriesSettings
from tests.utils import DummyTracer


class TestCIVisibilityTestATR:
    """Tests that the classes in the CIVisibility API correctly handle ATR

    Tests are done with EFD enabled and disabled to ensure that the classes handle both cases correctly.
    """

    def _get_session_settings(
        self, atr_settings: AutoTestRetriesSettings, efd_enabled: bool = False
    ) -> TestVisibilitySessionSettings:
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
            efd_settings=EarlyFlakeDetectionSettings(enabled=efd_enabled),
            atr_settings=atr_settings,
        )

    @pytest.mark.parametrize(
        "num_tests,atr_settings,atr_expected_retries",
        (
            # Test defaults
            # 8 tests should retry 5 times each for 40 total retries
            (8, AutoTestRetriesSettings(enabled=True), [5] * 8),
            # 200 tests should retry 5 times each for 1000 total retries
            (200, AutoTestRetriesSettings(enabled=True), [5] * 200),
            # 201 tests should retry 5 times each except for the last one
            (201, AutoTestRetriesSettings(enabled=True), [5] * 200),
            # Test custom settings
            # Only the first test should retry, 10 times total
            (3, AutoTestRetriesSettings(enabled=True, max_retries=100, max_session_total_retries=10), [10]),
            # The first 8 tests should retry 2 times each, and the 9th should retry once
            (20, AutoTestRetriesSettings(enabled=True, max_retries=2, max_session_total_retries=17), [2] * 8 + [1]),
            # The first 5 tests should retry 7 times each, and the remaining 9 should retry 0 times
            (13, AutoTestRetriesSettings(enabled=True, max_retries=7, max_session_total_retries=35), [7] * 5),
            # Not-enabled should result in no retries
            (8, AutoTestRetriesSettings(enabled=False), []),
        ),
    )
    @pytest.mark.parametrize("efd_enabled", (True, False))
    def test_atr_max_retries(self, num_tests, atr_settings, atr_expected_retries, efd_enabled):
        """Tests that the Test class retries the expected number of times"""

        expected_total_retries_count = sum(atr_expected_retries)
        expected_retried_tests_count = len(atr_expected_retries)

        total_retries = 0
        retried_tests = set()
        session = TestVisibilitySession(
            session_settings=self._get_session_settings(atr_settings, efd_enabled=efd_enabled)
        )
        session.efd_is_faulty_session = lambda: False

        test_names = [f"atr_test_{i}" for i in range(num_tests)]

        for test_number, test_name in enumerate(test_names):
            atr_test = TestVisibilityTest(
                name=test_name,
                session_settings=self._get_session_settings(atr_settings, efd_enabled=efd_enabled),
            )

            with mock.patch.object(atr_test, "get_session", return_value=session):
                atr_test.start()
                atr_test.finish_test(TestStatus.FAIL)

                retry_count = 0
                while atr_test.atr_should_retry():  # avoid infinite loops
                    retry_count += 1
                    total_retries += 1
                    retried_tests.add(atr_test)

                    added_retry_num = atr_test.atr_add_retry(start_immediately=True)
                    assert added_retry_num == retry_count
                    atr_test.atr_finish_retry(added_retry_num, TestStatus.FAIL)

                assert retry_count == (
                    atr_expected_retries[test_number] if test_number < len(atr_expected_retries) else 0
                )
                assert atr_test.atr_get_final_status() == TestStatus.FAIL

        assert total_retries == expected_total_retries_count
        assert len(retried_tests) == expected_retried_tests_count

    @pytest.mark.parametrize(
        "test_result,retry_results,expected_status",
        (
            # Initial pass
            (
                TestStatus.PASS,
                (),
                TestStatus.PASS,
            ),
            # Passes first retry
            (
                TestStatus.FAIL,
                (TestStatus.PASS,),
                TestStatus.PASS,
            ),
            # Passes 5th retry
            (
                TestStatus.FAIL,
                (TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL, TestStatus.PASS),
                TestStatus.PASS,
            ),
            # Never passes:
            (
                TestStatus.FAIL,
                (TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL),
                TestStatus.FAIL,
            ),
            # Passes 6th retry (so fails because we only do 5):
            (
                TestStatus.FAIL,
                (TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL, TestStatus.PASS),
                TestStatus.FAIL,
            ),
            # Skips initial attempt
            (
                TestStatus.SKIP,
                (),
                TestStatus.SKIP,
            ),
            # Skips some retries and passes
            (
                TestStatus.FAIL,
                (TestStatus.FAIL, TestStatus.SKIP, TestStatus.PASS),
                TestStatus.PASS,
            ),
            # Skips all retries
            (
                TestStatus.FAIL,
                (TestStatus.SKIP, TestStatus.SKIP, TestStatus.SKIP, TestStatus.SKIP, TestStatus.SKIP),
                TestStatus.FAIL,
            ),
            # Skips some retries and fails
            (
                TestStatus.FAIL,
                (TestStatus.SKIP, TestStatus.SKIP, TestStatus.FAIL, TestStatus.FAIL, TestStatus.SKIP),
                TestStatus.FAIL,
            ),
        ),
    )
    def test_atr_final_status(self, test_result, retry_results: t.Iterable[TestStatus], expected_status):
        """Tests that the EFD API correctly reports the final statuses of a test"""
        atr_settings = AutoTestRetriesSettings(enabled=True)
        session = TestVisibilitySession(session_settings=self._get_session_settings(atr_settings))
        session.efd_is_faulty_session = lambda: False

        atr_test = TestVisibilityTest(
            name="atr_test", session_settings=self._get_session_settings(atr_settings, efd_enabled=True)
        )

        atr_test.get_session = lambda: session

        atr_test.start()
        atr_test.finish_test(test_result)
        expected_retry_number = 0
        for test_result in retry_results:
            if not atr_test.atr_should_retry():
                break
            expected_retry_number += 1
            added_retry_number = atr_test.atr_add_retry(start_immediately=True)
            assert added_retry_number == expected_retry_number
            atr_test.atr_finish_retry(added_retry_number, test_result)
        assert atr_test.atr_get_final_status() == expected_status

    def test_atr_does_not_retry_if_disabled(self):
        atr_test = TestVisibilityTest(
            name="atr_test",
            session_settings=self._get_session_settings(atr_settings=AutoTestRetriesSettings(enabled=False)),
        )
        atr_test.start()
        atr_test.finish_test(TestStatus.FAIL)
        assert atr_test.atr_should_retry() is False
