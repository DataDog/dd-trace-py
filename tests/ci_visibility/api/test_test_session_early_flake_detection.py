from pathlib import Path

import mock

from ddtrace.ext.test_visibility._item_ids import TestModuleId
from ddtrace.ext.test_visibility._item_ids import TestSuiteId
from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api._efd_settings import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility.api._module import TestVisibilityModule
from ddtrace.internal.ci_visibility.api._session import TestVisibilitySession
from ddtrace.internal.ci_visibility.api._suite import TestVisibilitySuite
from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
from ddtrace.internal.ci_visibility.api._test_early_flake_detection import EarlyFlakeDetectionSessionHandler
from ddtrace.internal.ci_visibility.api._test_early_flake_detection import SessionMarkedFaulty
from ddtrace.internal.ci_visibility.constants import TEST_EFD_ABORT_REASON
from ddtrace.internal.ci_visibility.constants import TEST_EFD_ENABLED
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS
from ddtrace.internal.test_visibility._efd_mixins import EFDTestStatus
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
from tests.utils import DummyTracer


class TestEarlyFlakeDetectionSessionHandler:
    """Tests for the EarlyFlakeDetectionSessionHandler class."""

    def _get_session_settings(self, efd_settings: EarlyFlakeDetectionSettings):
        """Helper to create session settings with the provided EFD settings."""
        return TestVisibilitySessionSettings(
            tracer=DummyTracer(),
            test_service="efd_session_test_service",
            test_command="efd_session_test_command",
            test_framework="efd_session_test_framework",
            test_framework_metric_name=TEST_FRAMEWORKS.MANUAL,
            test_framework_version="0.0",
            session_operation_name="efd_session",
            module_operation_name="efd_module",
            suite_operation_name="efd_suite",
            test_operation_name="efd_test",
            workspace_path=Path().absolute(),
            efd_settings=efd_settings,
        )

    def test_init(self):
        """Test that the handler initializes correctly."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        session = mock.MagicMock()
        handler = EarlyFlakeDetectionSessionHandler(session, session_settings)

        assert handler._session == session
        assert handler._session_settings == session_settings
        assert handler._abort_reason is None
        assert handler._is_faulty_session is None

    def test_is_enabled(self):
        """Test that is_enabled returns the correct value."""
        # Test enabled
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        session = mock.MagicMock()
        handler = EarlyFlakeDetectionSessionHandler(session, session_settings)
        assert handler.is_enabled() is True

        # Test disabled
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(False))
        handler = EarlyFlakeDetectionSessionHandler(session, session_settings)
        assert handler.is_enabled() is False

    def test_set_abort_reason(self):
        """Test that set_abort_reason correctly sets the abort reason."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(True))
        session = mock.MagicMock()
        handler = EarlyFlakeDetectionSessionHandler(session, session_settings)

        handler.abort_reason = "test_reason"
        assert handler._abort_reason == "test_reason"

    def test_is_faulty_session(self):
        """Test that is_faulty_session correctly determines if a session is faulty."""
        session_settings = self._get_session_settings(
            EarlyFlakeDetectionSettings(enabled=True, faulty_session_threshold=50)
        )

        # Create session with tests
        session = TestVisibilitySession(session_settings)

        # Create test hierarchy with modules, suites, and tests
        m1_id = TestModuleId("module1")
        m1 = TestVisibilityModule(m1_id.name, session_settings=session_settings)
        session.add_child(m1_id, m1)

        m1_s1_id = TestSuiteId(m1_id, "suite1")
        m1_s1 = TestVisibilitySuite(m1_s1_id.name, session_settings=session_settings)
        m1.add_child(m1_s1_id, m1_s1)

        # Create 10 tests, 6 of them are new (60% > threshold of 50%)
        for i in range(10):
            # Mark 60% of tests as new
            is_new = i < 6
            test_name = f"test_{i}"
            m1_s1.add_child(
                InternalTestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=session_settings, is_new=is_new),
            )

        handler = EarlyFlakeDetectionSessionHandler(session, session_settings)

        # The implementation looks for a minimum number of new tests equal to the threshold
        # Our threshold is 50, so we need at least 50 new tests to be considered faulty
        # Since we only have 6 new tests, the session should not be considered faulty
        assert handler.is_faulty_session() is False

        # Create a new session with higher threshold count
        session_settings = self._get_session_settings(
            EarlyFlakeDetectionSettings(enabled=True, faulty_session_threshold=5)
        )
        session = TestVisibilitySession(session_settings)

        m1_id = TestModuleId("module1")
        m1 = TestVisibilityModule(m1_id.name, session_settings=session_settings)
        session.add_child(m1_id, m1)

        m1_s1_id = TestSuiteId(m1_id, "suite1")
        m1_s1 = TestVisibilitySuite(m1_s1_id.name, session_settings=session_settings)
        m1.add_child(m1_s1_id, m1_s1)

        # Create 10 tests, 6 of them are new (60% > threshold of 5%)
        for i in range(10):
            # Mark 60% of tests as new
            is_new = i < 6
            test_name = f"test_{i}"
            m1_s1.add_child(
                InternalTestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=session_settings, is_new=is_new),
            )

        handler = EarlyFlakeDetectionSessionHandler(session, session_settings)

        # Now we have 6 new tests and threshold is 5, so it should be considered faulty
        # When debugging, you can see the event being logged:
        with mock.patch("ddtrace.internal.ci_visibility.api._test_early_flake_detection.log.debug") as mock_debug:
            assert handler.is_faulty_session() is True

            # Check that the correct event was logged
            mock_debug.assert_called()
            call_args = mock_debug.call_args_list[-1]
            assert "Session marked as faulty" in call_args[0][0]
            assert isinstance(call_args[1]["extra"]["event"], SessionMarkedFaulty)
            event = call_args[1]["extra"]["event"]
            assert event.new_tests_count == 6
            assert event.total_tests_count == 10
            assert event.new_tests_percentage == 60.0
            assert event.threshold == 5.0

    def test_is_faulty_session_disabled(self):
        """Test that is_faulty_session returns False when EFD is disabled."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(enabled=False))
        session = mock.MagicMock()
        handler = EarlyFlakeDetectionSessionHandler(session, session_settings)

        assert handler.is_faulty_session() is False

    def test_has_failed_tests(self):
        """Test that has_failed_tests correctly checks for failed tests."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(enabled=True))

        # Create session with tests
        session = TestVisibilitySession(session_settings)

        # Create test hierarchy
        m1_id = TestModuleId("module1")
        m1 = TestVisibilityModule(m1_id.name, session_settings=session_settings)
        session.add_child(m1_id, m1)

        m1_s1_id = TestSuiteId(m1_id, "suite1")
        m1_s1 = TestVisibilitySuite(m1_s1_id.name, session_settings=session_settings)
        m1.add_child(m1_s1_id, m1_s1)

        # Create a test
        test_name = "test_1"
        test1 = TestVisibilityTest(test_name, session_settings=session_settings)
        m1_s1.add_child(InternalTestId(m1_s1_id, name=test_name), test1)

        # Mock the test methods
        test1.efd_has_retries = mock.MagicMock(return_value=True)
        test1.efd_get_final_status = mock.MagicMock(return_value=EFDTestStatus.ALL_FAIL)

        # Test with failed tests
        handler = EarlyFlakeDetectionSessionHandler(session, session_settings)
        assert handler.has_failed_tests() is True

        # Mock test with a different status
        test1.efd_get_final_status = mock.MagicMock(return_value=EFDTestStatus.FLAKY)
        assert handler.has_failed_tests() is False

    def test_has_failed_tests_disabled(self):
        """Test that has_failed_tests returns False when EFD is disabled."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(enabled=False))
        session = mock.MagicMock()
        handler = EarlyFlakeDetectionSessionHandler(session, session_settings)

        assert handler.has_failed_tests() is False

    def test_has_failed_tests_faulty_session(self):
        """Test that has_failed_tests returns False for a faulty session."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(enabled=True))
        session = mock.MagicMock()
        handler = EarlyFlakeDetectionSessionHandler(session, session_settings)

        # Mock is_faulty_session to return True
        handler.is_faulty_session = mock.MagicMock(return_value=True)

        assert handler.has_failed_tests() is False

    def test_set_tags(self):
        """Test that set_tags correctly sets EFD-related tags on the session."""
        session_settings = self._get_session_settings(EarlyFlakeDetectionSettings(enabled=True))
        session = mock.MagicMock()
        handler = EarlyFlakeDetectionSessionHandler(session, session_settings)

        # Test with EFD enabled
        handler.set_tags()
        session.set_tag.assert_called_with(TEST_EFD_ENABLED, True)

        # Test with abort reason
        session.reset_mock()
        handler.abort_reason = "test_reason"
        handler.set_tags()
        calls = [mock.call(TEST_EFD_ENABLED, True), mock.call(TEST_EFD_ABORT_REASON, "test_reason")]
        session.set_tag.assert_has_calls(calls, any_order=True)

        # Test with faulty session
        session.reset_mock()
        handler._abort_reason = None
        handler.is_faulty_session = mock.MagicMock(return_value=True)
        handler.set_tags()
        calls = [mock.call(TEST_EFD_ENABLED, True), mock.call(TEST_EFD_ABORT_REASON, "faulty")]
        session.set_tag.assert_has_calls(calls, any_order=True)
