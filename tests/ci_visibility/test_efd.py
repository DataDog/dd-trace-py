from pathlib import Path
import typing as t
from unittest import mock

import pytest

from ddtrace.ext.test_visibility._item_ids import TestModuleId
from ddtrace.ext.test_visibility._item_ids import TestSuiteId
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api._module import TestVisibilityModule
from ddtrace.internal.ci_visibility.api._session import TestVisibilitySession
from ddtrace.internal.ci_visibility.api._suite import TestVisibilitySuite
from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS
from ddtrace.internal.test_visibility._efd_mixins import EFDTestStatus
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
from tests.utils import DummyTracer


class TestCIVisibilityTestEFD:
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
            (EarlyFlakeDetectionSettings(True), 298.765, 2),
            (EarlyFlakeDetectionSettings(True), 321.123, 0),
            # Test custom settings
            (EarlyFlakeDetectionSettings(True, 7, 8, 1, 12), 3.124512, 7),
            (EarlyFlakeDetectionSettings(True, 7, 8, 1, 12), 9.951125, 8),
            (EarlyFlakeDetectionSettings(True, 7, 8, 1, 12), 27.1984, 1),
            (EarlyFlakeDetectionSettings(True, 7, 8, 1, 12), 298.765, 12),
            (EarlyFlakeDetectionSettings(True, 7, 8, 1, 12), 321.123, 0),
        ),
    )
    def test_efd_max_retries(self, efd_settings, efd_test_duration_s, expected_max_retries):
        """Tests that the Test class handles EFD API retry events correctly"""

        efd_test = TestVisibilityTest(
            name="efd_test",
            is_new=True,
            session_settings=self._get_session_settings(efd_settings),
        )

        mock_session = mock.Mock()
        mock_session.efd_is_faulty_session.return_value = False
        with mock.patch.object(TestVisibilityTest, "get_session", lambda *args: mock_session):
            efd_test.start()
            # Overwrite the test duration
            efd_test._span.start_ns -= efd_test_duration_s * 1e9
            efd_test.finish_test(TestStatus.PASS)

            retry_count = 0
            while efd_test.efd_should_retry():
                retry_count += 1
                added_retry_num = efd_test.efd_add_retry()
                assert added_retry_num == retry_count
                efd_test.efd_start_retry(added_retry_num)
                efd_test.efd_finish_retry(added_retry_num, TestStatus.PASS)

            assert retry_count == expected_max_retries

    @pytest.mark.parametrize(
        "test_result,retry_results,expected_statuses",
        (
            # All pass
            (
                TestStatus.PASS,
                (TestStatus.PASS, TestStatus.PASS, TestStatus.PASS),
                (EFDTestStatus.ALL_PASS, TestStatus.PASS),
            ),
            # Only original test passed
            (
                TestStatus.PASS,
                (TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL),
                (EFDTestStatus.FLAKY, TestStatus.PASS),
            ),
            # Only one retry passed
            (
                TestStatus.FAIL,
                (TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL, TestStatus.PASS),
                (EFDTestStatus.FLAKY, TestStatus.PASS),
            ),
            # Kitchen sink scenarios:
            (
                TestStatus.FAIL,
                (TestStatus.PASS, TestStatus.PASS, TestStatus.PASS),
                (EFDTestStatus.FLAKY, TestStatus.PASS),
            ),
            (
                TestStatus.PASS,
                (TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL, TestStatus.PASS),
                (EFDTestStatus.FLAKY, TestStatus.PASS),
            ),
            (
                TestStatus.PASS,
                (TestStatus.PASS, TestStatus.SKIP, TestStatus.PASS, TestStatus.FAIL),
                (EFDTestStatus.FLAKY, TestStatus.PASS),
            ),
            (
                TestStatus.FAIL,
                (TestStatus.PASS, TestStatus.SKIP, TestStatus.PASS, TestStatus.FAIL),
                (EFDTestStatus.FLAKY, TestStatus.PASS),
            ),
            (
                TestStatus.FAIL,
                (TestStatus.PASS, TestStatus.SKIP, TestStatus.PASS, TestStatus.SKIP),
                (EFDTestStatus.FLAKY, TestStatus.PASS),
            ),
            (TestStatus.FAIL, (TestStatus.FAIL,), (EFDTestStatus.ALL_FAIL, TestStatus.FAIL)),
            (
                TestStatus.FAIL,
                (TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL, TestStatus.FAIL),
                (EFDTestStatus.ALL_FAIL, TestStatus.FAIL),
            ),
            # All skipped
            (
                TestStatus.SKIP,
                (TestStatus.SKIP, TestStatus.SKIP, TestStatus.SKIP),
                (EFDTestStatus.ALL_SKIP, TestStatus.SKIP),
            ),
            # No retries happened
            (TestStatus.FAIL, tuple(), (EFDTestStatus.ALL_FAIL, TestStatus.FAIL)),
            (TestStatus.PASS, tuple(), (EFDTestStatus.ALL_PASS, TestStatus.PASS)),
            (TestStatus.SKIP, tuple(), (EFDTestStatus.ALL_SKIP, TestStatus.SKIP)),
        ),
    )
    def test_efd_final_status(self, test_result, retry_results: t.Iterable[TestStatus], expected_statuses):
        """Tests that the EFD API correctly reports the final statuses of a test"""
        efd_test = TestVisibilityTest(
            name="efd_test",
            session_settings=self._get_session_settings(EarlyFlakeDetectionSettings(True)),
            is_new=True,
        )
        mock_session = mock.Mock()
        mock_session.efd_is_faulty_session.return_value = False
        with mock.patch.object(TestVisibilityTest, "get_session", lambda *args: mock_session):
            efd_test.start()
            efd_test.finish_test(test_result)
            expected_num_retry = 0
            for test_result in retry_results:
                expected_num_retry += 1
                added_retry_number = efd_test.efd_add_retry(start_immediately=True)
                assert added_retry_number == expected_num_retry
                efd_test.efd_finish_retry(added_retry_number, test_result)
            assert efd_test.efd_get_final_status() == expected_statuses[0]
            assert efd_test.get_status() == expected_statuses[1]

    def test_efd_does_not_retry_if_disabled(self):
        efd_test = TestVisibilityTest(
            name="efd_test",
            session_settings=self._get_session_settings(EarlyFlakeDetectionSettings(False)),
        )
        efd_test.start()
        efd_test.finish_test(TestStatus.FAIL)
        assert efd_test.efd_should_retry() is False

    @pytest.mark.parametrize(
        "faulty_session_threshold,expected_faulty", ((None, True), (10, True), (40, True), (50, False))
    )
    def test_efd_session_faulty_percentage(self, faulty_session_threshold, expected_faulty):
        """Tests that the number of new tests in a session is correctly used to determine if a session is faulty based
        on the percentage of new tests (as opposed to the absolute number).

        In order to test the percentages fully without hitting the absolute number of new tests threshold, we generate
        a large number of both known and new tests.

        There are a total of 100 known and 100 new tests, so 50% are new
        """

        if faulty_session_threshold is not None:
            efd_settings = EarlyFlakeDetectionSettings(True, faulty_session_threshold=faulty_session_threshold)
        else:
            efd_settings = EarlyFlakeDetectionSettings(True)

        ssettings = self._get_session_settings(efd_settings=efd_settings)
        test_session = TestVisibilitySession(session_settings=ssettings)

        # Modules 1 and 2 each have one suite with 30 known tests and 20 new tests.
        m1_id = TestModuleId("module_1")
        m1 = TestVisibilityModule(m1_id.name, session_settings=ssettings)
        test_session.add_child(m1_id, m1)
        m1_s1_id = TestSuiteId(m1_id, "m1_s1")
        m1_s1 = TestVisibilitySuite(m1_s1_id.name, session_settings=ssettings)
        m1.add_child(m1_s1_id, m1_s1)

        # Known tests:
        for i in range(50):
            test_name = f"m1_s1_known_t{i}"
            m1_s1.add_child(
                InternalTestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=ssettings, is_new=False),
            )

        for i in range(50):
            test_name = f"m1_s1_new_t{i}"
            m1_s1.add_child(
                InternalTestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=ssettings, is_new=True),
            )

        m2_id = TestModuleId("module_2")
        m2 = TestVisibilityModule(m2_id.name, session_settings=ssettings)
        test_session.add_child(m2_id, m2)
        m2_s1_id = TestSuiteId(m2_id, "suite_1")
        m2_s1 = TestVisibilitySuite(m2_s1_id.name, session_settings=ssettings)
        m2.add_child(m2_s1_id, m2_s1)

        # Known tests:
        for i in range(50):
            test_name = f"m2_s1_known_t{i}"
            m2_s1.add_child(
                InternalTestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=ssettings, is_new=False),
            )

        for i in range(50):
            test_name = f"m2_s1_new_t{i}"
            m2_s1.add_child(
                InternalTestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=ssettings, is_new=True),
            )

        assert test_session.efd_is_faulty_session() == expected_faulty

    @pytest.mark.parametrize(
        "faulty_session_threshold,expected_faulty", ((None, True), (10, True), (40, False), (50, False))
    )
    def test_efd_session_faulty_absolute(self, faulty_session_threshold, expected_faulty):
        """Tests that the number of new tests in a session is correctly used to determine if a session is faulty based
        on the absolute number of new tests.

        For the purpose of this test, the test structure is hardcoded. Whether or not tests are properly marked as new,
        etc., should be tested elsewhere.

        There are a total of 10 known tests and 40 new tests, so 80% of tests are new.
        """

        if faulty_session_threshold is not None:
            efd_settings = EarlyFlakeDetectionSettings(True, faulty_session_threshold=faulty_session_threshold)
        else:
            efd_settings = EarlyFlakeDetectionSettings(True)

        ssettings = self._get_session_settings(efd_settings=efd_settings)
        test_session = TestVisibilitySession(session_settings=ssettings)

        # Modules 1 and 2 each have one suite with 5 known tests and 20 new tests.
        m1_id = TestModuleId("module_1")
        m1 = TestVisibilityModule(m1_id.name, session_settings=ssettings)
        test_session.add_child(m1_id, m1)
        m1_s1_id = TestSuiteId(m1_id, "m1_s1")
        m1_s1 = TestVisibilitySuite(m1_s1_id.name, session_settings=ssettings)
        m1.add_child(m1_s1_id, m1_s1)

        # Known tests:
        for i in range(5):
            test_name = f"m1_s1_known_t{i}"
            m1_s1.add_child(
                InternalTestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=ssettings, is_new=False),
            )

        for i in range(20):
            test_name = f"m1_s1_new_t{i}"
            m1_s1.add_child(
                InternalTestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=ssettings, is_new=True),
            )

        m2_id = TestModuleId("module_2")
        m2 = TestVisibilityModule(m2_id.name, session_settings=ssettings)
        test_session.add_child(m2_id, m2)
        m2_s1_id = TestSuiteId(m2_id, "suite_1")
        m2_s1 = TestVisibilitySuite(m2_s1_id.name, session_settings=ssettings)
        m2.add_child(m2_s1_id, m2_s1)

        # Known tests:
        for i in range(5):
            test_name = f"m2_s1_known_t{i}"
            m2_s1.add_child(
                InternalTestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=ssettings, is_new=False),
            )

        for i in range(20):
            test_name = f"m2_s1_new_t{i}"
            m2_s1.add_child(
                InternalTestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=ssettings, is_new=True),
            )

        assert test_session.efd_is_faulty_session() == expected_faulty
