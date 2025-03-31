from pathlib import Path
from unittest import mock

import pytest

from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
from ddtrace.internal.ci_visibility.api._test_early_flake_detection import EarlyFlakeDetectionHandler
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS
from ddtrace.internal.test_visibility._efd_mixins import EFDTestStatus
from tests.utils import DummyTracer


class TestEarlyFlakeDetectionHandler:
    """Tests for the EarlyFlakeDetectionHandler class."""

    def _get_session_settings(self, efd_settings: EarlyFlakeDetectionSettings):
        """Helper to create session settings with the provided EFD settings."""
        return TestVisibilitySessionSettings(
            tracer=DummyTracer(),
            test_service="efd_handler_test_service",
            test_command="efd_handler_test_command",
            test_framework="efd_handler_test_framework",
            test_framework_metric_name=TEST_FRAMEWORKS.MANUAL,
            test_framework_version="0.0",
            session_operation_name="efd_handler_session",
            module_operation_name="efd_handler_module",
            suite_operation_name="efd_handler_suite",
            test_operation_name="efd_handler_test",
            workspace_path=Path().absolute(),
            efd_settings=efd_settings,
        )

    def test_init(self):
        """Test that the handler initializes correctly."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_init", session_settings=session_settings)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        assert handler._test == test
        assert handler._session_settings == session_settings
        assert handler._is_retry is False
        assert handler._retries == []
        assert handler._abort_reason is None

    def test_is_retry_property(self):
        """Test the is_retry property getter and setter."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_retry", session_settings=session_settings)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        assert handler.is_retry is False
        handler.is_retry = True
        assert handler.is_retry is True

    def test_abort_reason_property(self):
        """Test the abort_reason property getter and set_abort_reason method."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_abort", session_settings=session_settings)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        assert handler.abort_reason is None
        handler.set_abort_reason("test_reason")
        assert handler.abort_reason == "test_reason"

    def test_make_retry_from_test(self):
        """Test that make_retry_from_test creates a retry test correctly."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_make_retry", session_settings=session_settings, is_new=True)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        # Create a parent mock so we can verify it's set on the retry test
        parent_mock = mock.Mock()
        test.parent = parent_mock

        retry_test = handler.make_retry_from_test()

        assert retry_test.name == test.name
        assert retry_test._session_settings == session_settings
        assert retry_test._is_new is True
        assert retry_test.parent == parent_mock
        assert retry_test._efd_is_retry is True

    def test_make_retry_from_test_with_parameters_raises_error(self):
        """Test that make_retry_from_test raises ValueError for tests with parameters."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(
            name="test_make_retry_params",
            session_settings=session_settings,
            parameters="param1=value1",
        )
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        with pytest.raises(ValueError, match="Cannot create an early flake retry from a test with parameters"):
            handler.make_retry_from_test()

    def test_get_retry_test(self):
        """Test that _get_retry_test returns the correct retry test."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_get_retry", session_settings=session_settings)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        # Create mocks for retry tests
        retry1 = mock.Mock()
        retry2 = mock.Mock()
        handler._retries = [retry1, retry2]

        assert handler._get_retry_test(1) == retry1
        assert handler._get_retry_test(2) == retry2

    @pytest.mark.parametrize(
        "efd_settings,duration_s,expected_retries",
        [
            # Test default settings
            (EarlyFlakeDetectionSettings(True), 3.5, 10),  # ≤ 5 seconds: 10 retries
            (EarlyFlakeDetectionSettings(True), 7.5, 5),  # ≤ 10 seconds: 5 retries
            (EarlyFlakeDetectionSettings(True), 15.0, 3),  # ≤ 30 seconds: 3 retries
            (EarlyFlakeDetectionSettings(True), 100.0, 2),  # ≤ 300 seconds: 2 retries
            (EarlyFlakeDetectionSettings(True), 400.0, 0),  # > 300 seconds: 0 retries
            # Test custom settings
            (EarlyFlakeDetectionSettings(True, 7, 8, 9, 10), 3.5, 7),  # ≤ 5 seconds: 7 retries
            (EarlyFlakeDetectionSettings(True, 7, 8, 9, 10), 7.5, 8),  # ≤ 10 seconds: 8 retries
            (EarlyFlakeDetectionSettings(True, 7, 8, 9, 10), 15.0, 9),  # ≤ 30 seconds: 9 retries
            (EarlyFlakeDetectionSettings(True, 7, 8, 9, 10), 100.0, 10),  # ≤ 300 seconds: 10 retries
        ],
    )
    def test_should_retry_duration_thresholds(self, efd_settings, duration_s, expected_retries):
        """Test that should_retry respects the duration thresholds."""
        session_settings = self._get_session_settings(efd_settings)
        test = TestVisibilityTest(name="test_should_retry", session_settings=session_settings, is_new=True)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        # Setup mock for get_session
        mock_session = mock.Mock()
        mock_session.efd_is_faulty_session.return_value = False
        with mock.patch.object(test, "get_session", return_value=mock_session):
            # Start the test
            test.start()
            # Finish the test (to make is_finished() return True)
            test.finish_test(TestStatus.PASS)

            # Mock the test span duration
            test._span.duration = duration_s

            # Add retries up to one less than the expected number of retries
            for i in range(expected_retries - 1):
                handler._retries.append(mock.Mock())
                assert handler.should_retry() is True

            # Add one more retry to reach the limit
            handler._retries.append(mock.Mock())
            if expected_retries > 0:
                assert handler.should_retry() is False
            else:
                assert handler.should_retry() is False

    def test_should_retry_disabled(self):
        """Test that should_retry returns False if EFD is disabled."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(False))
        test = TestVisibilityTest(name="test_should_retry_disabled", session_settings=session_settings, is_new=True)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        assert handler.should_retry() is False

    def test_should_retry_faulty_session(self):
        """Test that should_retry returns False if the session is faulty."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_should_retry_faulty", session_settings=session_settings, is_new=True)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        # Start and finish the test
        test.start()
        test.finish_test(TestStatus.PASS)

        # Setup mock to return a faulty session
        mock_session = mock.Mock()
        mock_session.efd_is_faulty_session.return_value = True
        with mock.patch.object(test, "get_session", return_value=mock_session):
            assert handler.should_retry() is False

    def test_should_retry_with_abort_reason(self):
        """Test that should_retry returns False if there's an abort reason."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_should_retry_abort", session_settings=session_settings, is_new=True)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        # Start and finish the test
        test.start()
        test.finish_test(TestStatus.PASS)

        # Setup mock session
        mock_session = mock.Mock()
        mock_session.efd_is_faulty_session.return_value = False
        with mock.patch.object(test, "get_session", return_value=mock_session):
            handler.set_abort_reason("test_reason")
            assert handler.should_retry() is False

    def test_should_retry_not_new(self):
        """Test that should_retry returns False if the test is not new."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_should_retry_not_new", session_settings=session_settings, is_new=False)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        # Start and finish the test
        test.start()
        test.finish_test(TestStatus.PASS)

        # Setup mock session
        mock_session = mock.Mock()
        mock_session.efd_is_faulty_session.return_value = False
        with mock.patch.object(test, "get_session", return_value=mock_session):
            assert handler.should_retry() is False

    def test_should_retry_not_finished(self):
        """Test that should_retry returns False if the test is not finished."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_should_retry_not_finished", session_settings=session_settings, is_new=True)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        # Setup mock session
        mock_session = mock.Mock()
        mock_session.efd_is_faulty_session.return_value = False
        with mock.patch.object(test, "get_session", return_value=mock_session):
            # Do not finish the test
            test.start()
            assert handler.should_retry() is False

    def test_has_retries(self):
        """Test that has_retries returns the correct value."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_has_retries", session_settings=session_settings)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        assert handler.has_retries() is False
        handler._retries.append(mock.Mock())
        assert handler.has_retries() is True

    def test_add_retry(self):
        """Test that add_retry creates and starts a retry test correctly."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_add_retry", session_settings=session_settings, is_new=True)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        # Setup mock session
        mock_session = mock.Mock()
        mock_session.efd_is_faulty_session.return_value = False
        with mock.patch.object(test, "get_session", return_value=mock_session), mock.patch.object(
            handler, "should_retry", return_value=True
        ), mock.patch.object(handler, "make_retry_from_test") as mock_make_retry:
            # Create a mock retry test
            mock_retry_test = mock.Mock()
            mock_make_retry.return_value = mock_retry_test

            # Test without start_immediately
            retry_number = handler.add_retry(start_immediately=False)
            assert retry_number == 1
            assert handler._retries == [mock_retry_test]
            mock_retry_test.start.assert_not_called()

            # Test with start_immediately
            mock_retry_test.reset_mock()
            retry_number = handler.add_retry(start_immediately=True)
            assert retry_number == 2
            assert len(handler._retries) == 2
            mock_retry_test.start.assert_called_once()

    def test_add_retry_should_not_retry(self):
        """Test that add_retry returns None if should_retry returns False."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_add_retry_no_retry", session_settings=session_settings)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        with mock.patch.object(handler, "should_retry", return_value=False):
            assert handler.add_retry() is None
            assert handler._retries == []

    def test_start_retry(self):
        """Test that start_retry starts the correct retry test."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_start_retry", session_settings=session_settings)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        # Setup mock retry tests
        mock_retry1 = mock.Mock()
        mock_retry2 = mock.Mock()
        handler._retries = [mock_retry1, mock_retry2]

        handler.start_retry(1)
        mock_retry1.start.assert_called_once()
        mock_retry2.start.assert_not_called()

        mock_retry1.reset_mock()
        handler.start_retry(2)
        mock_retry1.start.assert_not_called()
        mock_retry2.start.assert_called_once()

    def test_finish_retry(self):
        """Test that finish_retry finishes the correct retry test with the correct status."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_finish_retry", session_settings=session_settings)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        # Setup mock retry tests
        mock_retry1 = mock.Mock()
        mock_retry2 = mock.Mock()
        handler._retries = [mock_retry1, mock_retry2]

        # Test with a status
        handler.finish_retry(1, TestStatus.PASS)
        mock_retry1.set_status.assert_called_once_with(TestStatus.PASS)
        mock_retry1.finish_test.assert_called_once_with(TestStatus.PASS, exc_info=None)
        mock_retry2.set_status.assert_not_called()
        mock_retry2.finish_test.assert_not_called()

        # Test with exc_info
        mock_retry1.reset_mock()
        exc_info = mock.Mock()
        handler.finish_retry(2, TestStatus.FAIL, exc_info=exc_info)
        mock_retry1.set_status.assert_not_called()
        mock_retry1.finish_test.assert_not_called()
        mock_retry2.set_status.assert_called_once_with(TestStatus.FAIL)
        mock_retry2.finish_test.assert_called_once_with(TestStatus.FAIL, exc_info=exc_info)

    @pytest.mark.parametrize(
        "test_status,retry_statuses,expected_status",
        [
            # All pass
            (TestStatus.PASS, [TestStatus.PASS, TestStatus.PASS], EFDTestStatus.ALL_PASS),
            # All fail
            (TestStatus.FAIL, [TestStatus.FAIL, TestStatus.FAIL], EFDTestStatus.ALL_FAIL),
            # All skip
            (TestStatus.SKIP, [TestStatus.SKIP, TestStatus.SKIP], EFDTestStatus.ALL_SKIP),
            # Mixed (flaky)
            (TestStatus.PASS, [TestStatus.FAIL, TestStatus.PASS], EFDTestStatus.FLAKY),
            (TestStatus.FAIL, [TestStatus.PASS, TestStatus.FAIL], EFDTestStatus.FLAKY),
            (TestStatus.PASS, [TestStatus.SKIP, TestStatus.PASS], EFDTestStatus.FLAKY),
            (TestStatus.SKIP, [TestStatus.PASS, TestStatus.FAIL], EFDTestStatus.FLAKY),
            # No retries
            (TestStatus.PASS, [], EFDTestStatus.ALL_PASS),
            (TestStatus.FAIL, [], EFDTestStatus.ALL_FAIL),
            (TestStatus.SKIP, [], EFDTestStatus.ALL_SKIP),
        ],
    )
    def test_get_final_status(self, test_status, retry_statuses, expected_status):
        """Test that get_final_status returns the correct status based on test and retry statuses."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_get_final_status", session_settings=session_settings)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        # Set the test status
        test._status = test_status

        # Create mocks for retries with their statuses
        handler._retries = []
        for status in retry_statuses:
            retry = mock.Mock()
            retry._status = status
            handler._retries.append(retry)

        assert handler.get_final_status() == expected_status

    @pytest.mark.parametrize(
        "efd_status,expected_status",
        [
            (EFDTestStatus.ALL_PASS, TestStatus.PASS),
            (EFDTestStatus.FLAKY, TestStatus.PASS),
            (EFDTestStatus.ALL_FAIL, TestStatus.FAIL),
            (EFDTestStatus.ALL_SKIP, TestStatus.SKIP),
        ],
    )
    def test_get_consolidated_status(self, efd_status, expected_status):
        """Test that get_consolidated_status returns the correct TestStatus for each EFDTestStatus."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_get_consolidated_status", session_settings=session_settings)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        with mock.patch.object(handler, "get_final_status", return_value=efd_status):
            assert handler.get_consolidated_status() == expected_status

    def test_set_tags(self):
        """Test that set_tags sets the correct tags on the test."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_set_tags", session_settings=session_settings, is_new=True)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        # Setup mock session
        mock_session = mock.Mock()
        mock_session.efd_is_faulty_session.return_value = False
        with mock.patch.object(test, "get_session", return_value=mock_session):
            # Set is_retry and abort_reason
            handler.is_retry = True
            handler.set_abort_reason("test_reason")

            # Call set_tags
            handler.set_tags()

            # Verify tags were set - note that get_tag returns the actual value (could be bool for boolean tags)
            assert test.get_tag("test.is_retry") is True
            assert test.get_tag("test.retry_reason") == "early_flake_detection"
            assert test.get_tag("test.efd.abort_reason") == "test_reason"
            assert test.get_tag("test.is_new") is True

    def test_set_tags_faulty_session(self):
        """Test that set_tags doesn't set is_new tag if the session is faulty."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_set_tags_faulty", session_settings=session_settings, is_new=True)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        # Setup mock session that returns faulty session
        mock_session = mock.Mock()
        mock_session.efd_is_faulty_session.return_value = True
        with mock.patch.object(test, "get_session", return_value=mock_session):
            handler.set_tags()

            # Verify is_new tag was not set
            assert test.get_tag("test.is_new") is None

    def test_check_and_handle_abort_if_needed(self):
        """Test that check_and_handle_abort_if_needed properly checks and handles abort conditions."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        test = TestVisibilityTest(name="test_check_abort", session_settings=session_settings, is_new=True)
        handler = EarlyFlakeDetectionHandler(test, session_settings)

        # Test case: Conditions for abort not met
        with mock.patch.object(handler, "should_abort", return_value=False):
            assert handler.check_and_handle_abort_if_needed() is False
            assert handler.abort_reason is None

        # Test case: Conditions for abort met
        with mock.patch.object(handler, "should_abort", return_value=True):
            assert handler.check_and_handle_abort_if_needed() is True
            assert handler.abort_reason == "slow"

        # Test case: EFD disabled
        session_settings_disabled = self._get_session_settings(EarlyFlakeDetectionSettings(False))
        test_disabled = TestVisibilityTest(
            name="test_check_abort_disabled", session_settings=session_settings_disabled, is_new=True
        )
        handler_disabled = EarlyFlakeDetectionHandler(test_disabled, session_settings_disabled)

        assert handler_disabled.check_and_handle_abort_if_needed() is False

        # Test case: Test is a retry
        handler.is_retry = True
        with mock.patch.object(handler, "should_abort", return_value=True):
            assert handler.check_and_handle_abort_if_needed() is False

        # Test case: Test is not new
        test_not_new = TestVisibilityTest(
            name="test_check_abort_not_new", session_settings=session_settings, is_new=False
        )
        handler_not_new = EarlyFlakeDetectionHandler(test_not_new, session_settings)

        with mock.patch.object(handler_not_new, "should_abort", return_value=True):
            assert handler_not_new.check_and_handle_abort_if_needed() is False
