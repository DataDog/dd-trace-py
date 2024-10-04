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
        with mock.patch.multiple(efd_test, get_session=lambda *args: mock_session):
            efd_test.start()

            efd_test.efd_record_initial(TestStatus.PASS)

            # Overwrite the test duration
            efd_test._efd_initial_finish_time_ns = efd_test._span.start_ns + (efd_test_duration_s * 1e9)

            retry_count = 0
            while efd_test.efd_should_retry():
                retry_count += 1
                added_retry_num = efd_test.efd_add_retry()
                assert added_retry_num == retry_count
                efd_test.efd_start_retry(added_retry_num)
                efd_test.efd_finish_retry(added_retry_num, TestStatus.PASS)

            assert retry_count == expected_max_retries

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
            is_new=True,
        )
        mock_session = mock.Mock()
        mock_session.efd_is_faulty_session.return_value = False
        with mock.patch.multiple(efd_test, get_session=lambda *args: mock_session):
            efd_test.start()
            efd_test.efd_record_initial(test_result)
            expected_num_retry = 0
            for test_result in retry_results:
                expected_num_retry += 1
                added_retry_number = efd_test.efd_add_retry(start_immediately=True)
                assert added_retry_number
                efd_test.efd_finish_retry(added_retry_number, test_result)
            assert efd_test.efd_get_final_status() == expected_status

    def test_efd_does_not_retry_if_disabled(self):
        efd_test = TestVisibilityTest(
            name="efd_test",
            session_settings=self._get_session_settings(EarlyFlakeDetectionSettings(False)),
        )
        assert efd_test.efd_should_retry() is False

    @pytest.mark.parametrize("faulty_session_threshold,expected_faulty", ((None, False), (10, True), (40, False)))
    def test_efd_session_faulty(self, faulty_session_threshold, expected_faulty):
        """Tests that the number of new tests in a session is correctly used to determine if a session is faulty

        For the purpose of this test, the test structure is hardcoded. Whether or not tests are properly marked as new,
        etc., should be tested elsewhere.
        """
        if faulty_session_threshold is not None:
            efd_settings = EarlyFlakeDetectionSettings(True, faulty_session_threshold=faulty_session_threshold)
        else:
            efd_settings = EarlyFlakeDetectionSettings(True)

        ssettings = self._get_session_settings(efd_settings=efd_settings)
        test_session = TestVisibilitySession(session_settings=ssettings)

        # Module
        m1_id = TestModuleId("module_1")
        m1 = TestVisibilityModule(m1_id.name, session_settings=ssettings)
        test_session.add_child(m1_id, m1)
        m1_s1_id = TestSuiteId(m1_id, "m1_s1")
        m1_s1 = TestVisibilitySuite(m1_s1_id.name, session_settings=ssettings)
        m1.add_child(m1_s1_id, m1_s1)
        m1_s1_t1_id = InternalTestId(m1_s1_id, name="m1_s1_t1")
        m1_s1.add_child(m1_s1_t1_id, TestVisibilityTest(m1_s1_t1_id.name, session_settings=ssettings, is_new=True))
        m1_s1_t2_id = InternalTestId(m1_s1_id, name="m1_s1_t2")
        m1_s1.add_child(m1_s1_t2_id, TestVisibilityTest(m1_s1_t2_id.name, session_settings=ssettings, is_new=False))
        m1_s1_t3_id = InternalTestId(m1_s1_id, name="m1_s1_t3")
        m1_s1.add_child(m1_s1_t3_id, TestVisibilityTest(m1_s1_t3_id.name, session_settings=ssettings, is_new=False))

        m1_s2_id = TestSuiteId(m1_id, "suite_2")
        m1_s2 = TestVisibilitySuite(m1_s2_id.name, session_settings=ssettings)
        m1.add_child(m1_s2_id, m1_s2)
        m1_s2_t1_id = InternalTestId(m1_s2_id, name="m1_s2_t1")
        m1_s2.add_child(m1_s2_t1_id, TestVisibilityTest(m1_s2_t1_id.name, session_settings=ssettings, is_new=True))

        m2_id = TestModuleId("module_2")
        m2 = TestVisibilityModule(m2_id.name, session_settings=ssettings)
        test_session.add_child(m2_id, m2)
        m2_s1_id = TestSuiteId(m2_id, "suite_1")
        m2_s1 = TestVisibilitySuite(m2_s1_id.name, session_settings=ssettings)
        m2.add_child(m2_s1_id, m2_s1)

        m2_s1_t1_id = InternalTestId(m2_s1_id, name="m2_s1_t1")
        m2_s1.add_child(m2_s1_t1_id, TestVisibilityTest(m2_s1_t1_id.name, session_settings=ssettings, is_new=False))
        m2_s1_t2_id = InternalTestId(m2_s1_id, name="m2_s1_t2")
        m2_s1.add_child(m2_s1_t2_id, TestVisibilityTest(m2_s1_t2_id.name, session_settings=ssettings, is_new=False))
        m2_s1_t3_id = InternalTestId(m2_s1_id, name="m2_s1_t3")
        m2_s1.add_child(m2_s1_t3_id, TestVisibilityTest(m2_s1_t3_id.name, session_settings=ssettings, is_new=False))

        # A test with parameters is never considered new:
        m2_s1_t4_id = InternalTestId(m2_s1_id, name="m2_s1_t4", parameters='{"hello": "world"}')
        m2_s1.add_child(
            m2_s1_t4_id,
            TestVisibilityTest(
                m2_s1_t4_id.name, session_settings=ssettings, is_new=True, parameters=m2_s1_t4_id.parameters
            ),
        )

        assert test_session.efd_is_faulty_session() == expected_faulty
