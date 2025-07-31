"""Unit tests for helper functions in pytest plugin v2."""

from unittest import mock

from ddtrace.contrib.internal.pytest._plugin_v2 import _handle_retry_logic
from ddtrace.contrib.internal.pytest._plugin_v2 import _has_setup_or_teardown_failure
from ddtrace.contrib.internal.pytest._plugin_v2 import _log_test_reports
from ddtrace.contrib.internal.pytest._plugin_v2 import _should_collect_coverage_for_item
from ddtrace.contrib.internal.pytest._utils import TestPhase


class MockTestItem:
    """Mock pytest test item for testing."""

    def __init__(self, nodeid="test_file.py::test_name"):
        self.nodeid = nodeid
        self.ihook = mock.Mock()

    @property
    def location(self):
        return ("test_file.py", 10, "test_name")


class MockTestReport:
    """Mock pytest test report for testing."""

    def __init__(self, when=TestPhase.CALL, outcome="passed", failed=False):
        self.when = when
        self.outcome = outcome
        self.failed = failed
        self.skipped = False
        self.longrepr = None


class TestShouldCollectCoverageForItem:
    """Test _should_collect_coverage_for_item function."""

    def test_should_collect_coverage_when_enabled_and_not_skipped(self):
        """Test coverage collection is enabled when coverage is on and item won't be skipped."""
        item = MockTestItem()

        # Mock all the dependencies to avoid pytest infrastructure complexity
        with mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTestSession.should_collect_coverage", return_value=True
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2._pytest_marked_to_skip", return_value=False
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTest.was_itr_skipped", return_value=False
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2._get_test_id_from_item", return_value=mock.Mock()
        ):
            result = _should_collect_coverage_for_item(item)
            assert result is True

    def test_should_not_collect_coverage_when_disabled(self):
        """Test coverage collection is disabled when coverage collection is off."""
        item = MockTestItem()

        # When coverage collection is disabled, we don't need to check other conditions
        with mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTestSession.should_collect_coverage", return_value=False
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2._pytest_marked_to_skip", return_value=False
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTest.was_itr_skipped", return_value=False
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2._get_test_id_from_item", return_value=mock.Mock()
        ):
            result = _should_collect_coverage_for_item(item)
            assert result is False

    def test_should_not_collect_coverage_when_item_marked_to_skip(self):
        """Test coverage collection is disabled when item is marked to skip."""
        item = MockTestItem()

        with mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTestSession.should_collect_coverage", return_value=True
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2._pytest_marked_to_skip", return_value=True
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTest.was_itr_skipped", return_value=False
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2._get_test_id_from_item", return_value=mock.Mock()
        ):
            result = _should_collect_coverage_for_item(item)
            assert result is False

    def test_should_not_collect_coverage_when_item_itr_skipped(self):
        """Test coverage collection is disabled when item was ITR skipped."""
        item = MockTestItem()

        with mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTestSession.should_collect_coverage", return_value=True
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2._pytest_marked_to_skip", return_value=False
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTest.was_itr_skipped", return_value=True
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2._get_test_id_from_item", return_value=mock.Mock()
        ):
            result = _should_collect_coverage_for_item(item)
            assert result is False


class TestLogTestReports:
    """Test _log_test_reports function."""

    def test_log_reports_from_dict(self):
        """Test logging reports when passed as a dictionary."""
        item = MockTestItem()
        reports_dict = {
            TestPhase.SETUP: MockTestReport(when=TestPhase.SETUP),
            TestPhase.CALL: MockTestReport(when=TestPhase.CALL),
            TestPhase.TEARDOWN: MockTestReport(when=TestPhase.TEARDOWN),
        }

        _log_test_reports(item, reports_dict)

        assert item.ihook.pytest_runtest_logreport.call_count == 3
        # Verify all reports were logged
        logged_reports = [call.kwargs["report"] for call in item.ihook.pytest_runtest_logreport.call_args_list]
        assert len(logged_reports) == 3

    def test_log_reports_from_list(self):
        """Test logging reports when passed as a list."""
        item = MockTestItem()
        reports_list = [
            MockTestReport(when=TestPhase.SETUP),
            MockTestReport(when=TestPhase.CALL),
        ]

        _log_test_reports(item, reports_list)

        assert item.ihook.pytest_runtest_logreport.call_count == 2


class TestHasSetupOrTeardownFailure:
    """Test _has_setup_or_teardown_failure function."""

    def test_detects_setup_failure(self):
        """Test detection of setup phase failure."""
        reports_dict = {
            TestPhase.SETUP: MockTestReport(when=TestPhase.SETUP, failed=True),
            TestPhase.CALL: MockTestReport(when=TestPhase.CALL),
        }

        result = _has_setup_or_teardown_failure(reports_dict)
        assert result is True

    def test_detects_teardown_failure(self):
        """Test detection of teardown phase failure."""
        reports_dict = {
            TestPhase.CALL: MockTestReport(when=TestPhase.CALL),
            TestPhase.TEARDOWN: MockTestReport(when=TestPhase.TEARDOWN, failed=True),
        }

        result = _has_setup_or_teardown_failure(reports_dict)
        assert result is True

    def test_no_failure_when_only_call_fails(self):
        """Test that call phase failures don't count as setup/teardown failures."""
        reports_dict = {
            TestPhase.SETUP: MockTestReport(when=TestPhase.SETUP),
            TestPhase.CALL: MockTestReport(when=TestPhase.CALL, failed=True),
            TestPhase.TEARDOWN: MockTestReport(when=TestPhase.TEARDOWN),
        }

        result = _has_setup_or_teardown_failure(reports_dict)
        assert result is False

    def test_no_failure_when_all_pass(self):
        """Test no failure detected when all phases pass."""
        reports_dict = {
            TestPhase.SETUP: MockTestReport(when=TestPhase.SETUP),
            TestPhase.CALL: MockTestReport(when=TestPhase.CALL),
            TestPhase.TEARDOWN: MockTestReport(when=TestPhase.TEARDOWN),
        }

        result = _has_setup_or_teardown_failure(reports_dict)
        assert result is False


class TestHandleRetryLogic:
    """Test _handle_retry_logic function."""

    def test_skips_retries_on_setup_failure(self):
        """Test that retries are skipped when setup fails."""
        item = MockTestItem()
        test_id = mock.Mock()
        reports_dict = {
            TestPhase.SETUP: MockTestReport(when=TestPhase.SETUP, failed=True),
            TestPhase.CALL: MockTestReport(when=TestPhase.CALL),
        }
        test_outcome = mock.Mock()

        with mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTest.is_quarantined_test", return_value=False
        ), mock.patch("ddtrace.contrib.internal.pytest._plugin_v2.InternalTest.is_attempt_to_fix", return_value=False):
            result = _handle_retry_logic(item, test_id, reports_dict, test_outcome)

            # Should return False (no retry handler called) and should log reports
            assert result is False
            assert item.ihook.pytest_runtest_logreport.call_count == 2

    def test_calls_efd_retry_handler_when_enabled(self):
        """Test that EFD retry handler is called when conditions are met."""
        item = MockTestItem()
        test_id = mock.Mock()
        reports_dict = {
            TestPhase.CALL: MockTestReport(when=TestPhase.CALL, failed=True),
        }
        test_outcome = mock.Mock()
        mock_efd_handler = mock.Mock()

        with mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTest.is_quarantined_test", return_value=False
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTest.is_attempt_to_fix", return_value=False
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2._pytest_version_supports_attempt_to_fix", return_value=False
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTestSession.efd_enabled", return_value=True
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTest.efd_should_retry", return_value=True
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.efd_handle_retries", mock_efd_handler
        ):
            result = _handle_retry_logic(item, test_id, reports_dict, test_outcome)

            # Should return True (retry handler was called)
            assert result is True
            mock_efd_handler.assert_called_once_with(
                test_id=test_id,
                item=item,
                test_reports=reports_dict,
                test_outcome=test_outcome,
                is_quarantined=False,
            )

    def test_logs_reports_when_no_retry_handler(self):
        """Test that reports are logged when no retry handler is applicable."""
        item = MockTestItem()
        test_id = mock.Mock()
        reports_dict = {
            TestPhase.CALL: MockTestReport(when=TestPhase.CALL),
        }
        test_outcome = mock.Mock()

        with mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTest.is_quarantined_test", return_value=False
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTest.is_attempt_to_fix", return_value=False
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTestSession.efd_enabled", return_value=False
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.InternalTestSession.atr_is_enabled", return_value=False
        ):
            result = _handle_retry_logic(item, test_id, reports_dict, test_outcome)

            # Should return False and log the reports
            assert result is False
            assert item.ihook.pytest_runtest_logreport.call_count == 1
