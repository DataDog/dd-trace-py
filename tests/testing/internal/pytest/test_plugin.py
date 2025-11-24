"""Unit tests for pytest plugin functionality.

This file is organized with high-level feature tests first, followed by unit tests.
Integration tests are in tests/test_integration.py.
"""

import os
import typing as t
from unittest.mock import Mock
from unittest.mock import patch

from _pytest.reports import TestReport
import pytest

from ddtestpy.internal.pytest.plugin import DISABLED_BY_TEST_MANAGEMENT_REASON
from ddtestpy.internal.pytest.plugin import SKIPPED_BY_ITR_REASON
from ddtestpy.internal.pytest.plugin import TestOptPlugin
from ddtestpy.internal.pytest.plugin import XdistTestOptPlugin
from ddtestpy.internal.pytest.plugin import _encode_test_parameter
from ddtestpy.internal.pytest.plugin import _get_exception_tags
from ddtestpy.internal.pytest.plugin import _get_module_path_from_item
from ddtestpy.internal.pytest.plugin import _get_test_command
from ddtestpy.internal.pytest.plugin import _get_test_parameters_json
from ddtestpy.internal.pytest.plugin import _get_user_property
from ddtestpy.internal.pytest.plugin import nodeid_to_test_ref
from tests.mocks import TestDataFactory
from tests.mocks import mock_test
from tests.mocks import pytest_item_mock
from tests.mocks import session_manager_mock


# =============================================================================
# HIGH-LEVEL FEATURE TESTS (organized by feature)
# =============================================================================


class TestSkippingAndITRFeatures:
    """Test intelligent test running and skipping functionality."""

    def test_skippable_test_without_attempt_to_fix_gets_skipped(self) -> None:
        """Test that a skippable test that is NOT attempt_to_fix gets skipped."""
        # Create test references using TestDataFactory
        test_ref = TestDataFactory.create_test_ref("test_module", "test_suite.py", "test_function")

        # Create mock session manager with test in skippable_items
        mock_manager = session_manager_mock().with_skipping_enabled(True).with_skippable_items({test_ref}).build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Create mock test that is NOT attempt_to_fix
        test = mock_test(test_ref)
        mock_manager.discover_test.return_value = (test.module, test.suite, test)

        # Store test in plugin's dictionary
        plugin.tests_by_nodeid = {"test_module/test_suite.py::test_function": test}

        # Create mock pytest item
        mock_item = pytest_item_mock("test_module/test_suite.py::test_function").build()

        # Mock the trace_context and coverage_collection context managers
        with patch("ddtestpy.internal.pytest.plugin.trace_context"), patch(
            "ddtestpy.internal.pytest.plugin.coverage_collection"
        ):

            # Call the method that applies skipping logic
            list(plugin.pytest_runtest_protocol_wrapper(mock_item, None))

        # Verify that the test was marked as skipped
        mock_item.add_marker.assert_called()
        call_args = mock_item.add_marker.call_args
        assert call_args[0][0].mark.name == "skip"
        assert call_args[0][0].mark.kwargs["reason"] == SKIPPED_BY_ITR_REASON

    def test_skippable_test_with_attempt_to_fix_not_skipped(self) -> None:
        """Test that a skippable test that IS attempt_to_fix does NOT get skipped."""
        # Create test references using TestDataFactory
        test_ref = TestDataFactory.create_test_ref("test_module", "test_suite.py", "test_function")

        # Create mock session manager with test in skippable_items
        mock_manager = session_manager_mock().with_skipping_enabled(True).with_skippable_items({test_ref}).build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Create mock test that IS attempt_to_fix
        test = mock_test(test_ref)
        test.set_attributes(is_attempt_to_fix=True)
        mock_manager.discover_test.return_value = (test.module, test.suite, test)

        # Store test in plugin's dictionary
        plugin.tests_by_nodeid = {"test_module/test_suite.py::test_function": test}

        # Create mock pytest item
        mock_item = pytest_item_mock("test_module/test_suite.py::test_function").build()

        # Mock the trace_context and coverage_collection context managers
        with patch("ddtestpy.internal.pytest.plugin.trace_context"), patch(
            "ddtestpy.internal.pytest.plugin.coverage_collection"
        ):

            # Call the method that applies skipping logic
            list(plugin.pytest_runtest_protocol_wrapper(mock_item, None))

        # Verify that the test was NOT marked as skipped with ITR reason
        skip_calls = [
            call
            for call in mock_item.add_marker.call_args_list
            if len(call[0]) > 0 and hasattr(call[0][0], "mark") and call[0][0].mark.name == "skip"
        ]

        itr_skip_calls = [call for call in skip_calls if call[0][0].mark.kwargs.get("reason") == SKIPPED_BY_ITR_REASON]

        assert len(itr_skip_calls) == 0, "Test should not be skipped with ITR reason when is_attempt_to_fix=True"

    def test_suite_level_skipping_works(self) -> None:
        """Test that tests from a skippable suite get skipped."""
        # Create test references using TestDataFactory
        test_ref = TestDataFactory.create_test_ref("test_module", "test_suite.py", "test_function")
        suite_ref = test_ref.suite

        # Create mock session manager with SUITE in skippable_items (not individual test)
        mock_manager = (
            session_manager_mock()
            .with_skipping_enabled(True)
            .with_skippable_items({suite_ref})  # Suite is skippable, not individual test
            .build_mock()
        )
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Create mock test that is NOT attempt_to_fix
        test = mock_test(test_ref)
        mock_manager.discover_test.return_value = (test.module, test.suite, test)

        # Store test in plugin's dictionary
        plugin.tests_by_nodeid = {"test_module/test_suite.py::test_function": test}

        # Create mock pytest item
        mock_item = pytest_item_mock("test_module/test_suite.py::test_function").build()

        # Mock the trace_context and coverage_collection context managers
        with patch("ddtestpy.internal.pytest.plugin.trace_context"), patch(
            "ddtestpy.internal.pytest.plugin.coverage_collection"
        ):

            # Call the method that applies skipping logic
            list(plugin.pytest_runtest_protocol_wrapper(mock_item, None))

        # Verify that the test was marked as skipped
        mock_item.add_marker.assert_called()
        call_args = mock_item.add_marker.call_args
        assert call_args[0][0].mark.name == "skip"
        assert call_args[0][0].mark.kwargs["reason"] == SKIPPED_BY_ITR_REASON

    def test_disabled_test_management_features(self) -> None:
        """Test test management features like disabled and quarantined tests."""
        # Create test references using TestDataFactory
        test_ref = TestDataFactory.create_test_ref("test_module", "test_suite.py", "test_function")

        # Create plugin and mock dependencies
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Create mock test that is disabled but NOT attempt_to_fix
        test = mock_test(test_ref)
        test.set_attributes(is_disabled=True, is_attempt_to_fix=False)
        mock_manager.discover_test.return_value = (test.module, test.suite, test)

        # Store test in plugin's dictionary
        plugin.tests_by_nodeid = {"test_module/test_suite.py::test_function": test}

        # Create mock pytest item
        mock_item = pytest_item_mock("test_module/test_suite.py::test_function").build()

        # Mock the trace_context and coverage_collection context managers
        with patch("ddtestpy.internal.pytest.plugin.trace_context"), patch(
            "ddtestpy.internal.pytest.plugin.coverage_collection"
        ):

            # Call the method that applies skipping logic
            list(plugin.pytest_runtest_protocol_wrapper(mock_item, None))

        # Verify that the test was marked as skipped for test management reason
        skip_calls = [
            call
            for call in mock_item.add_marker.call_args_list
            if len(call[0]) > 0 and hasattr(call[0][0], "mark") and call[0][0].mark.name == "skip"
        ]

        tm_skip_calls = [
            call for call in skip_calls if call[0][0].mark.kwargs.get("reason") == DISABLED_BY_TEST_MANAGEMENT_REASON
        ]

        assert len(tm_skip_calls) == 1, "Disabled test should be skipped with test management reason"


class TestSessionManagement:
    """Test session lifecycle and configuration."""

    def test_plugin_initialization(self) -> None:
        """Test that TestOptPlugin initializes correctly."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        assert plugin.is_xdist_worker is False
        assert plugin.enable_ddtrace is False
        assert isinstance(plugin.reports_by_nodeid, dict)
        assert isinstance(plugin.excinfo_by_report, dict)
        assert isinstance(plugin.tests_by_nodeid, dict)

    def test_xdist_plugin_initialization(self) -> None:
        """Test that XdistTestOptPlugin initializes correctly."""
        mock_manager = session_manager_mock().build_mock()
        plugin = XdistTestOptPlugin(session_manager=mock_manager)

        # Should inherit from TestOptPlugin
        assert plugin.is_xdist_worker is False
        assert hasattr(plugin, "pytest_configure_node")

    def test_session_start_with_xdist_worker_input(self) -> None:
        """Test plugin behavior with xdist worker configuration."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Mock session with xdist worker input
        mock_session = Mock()
        mock_config = Mock()
        mock_config.workerinput = {"dd_session_id": "test-session-123"}
        # Mock invocation_params to avoid the join error
        mock_invocation_params = Mock()
        mock_invocation_params.args = ["test_arg1", "test_arg2"]
        mock_config.invocation_params = mock_invocation_params
        mock_session.config = mock_config

        # Mock the session manager and other dependencies
        with patch("ddtestpy.internal.pytest.plugin.SessionManager", return_value=Mock()):
            plugin.pytest_sessionstart(mock_session)

        assert plugin.is_xdist_worker is True

    def test_get_test_command_extraction(self) -> None:
        """Test that the pytest session command is properly extracted."""
        # Mock config with various command parameters
        mock_config = Mock()
        mock_invocation_params = Mock()
        mock_invocation_params.args = ["--tb=short", "-v", "tests/"]
        mock_config.invocation_params = mock_invocation_params

        # Mock environment variable
        with patch.dict(os.environ, {"PYTEST_ADDOPTS": "--maxfail=1"}):
            command = _get_test_command(mock_config)

        expected = "pytest --tb=short -v tests/ --maxfail=1"
        assert command == expected

    def test_get_test_command_no_params(self) -> None:
        """Test command extraction when no invocation params are available."""
        mock_config = Mock()
        mock_config.invocation_params = None

        with patch.dict(os.environ, {}, clear=True):
            command = _get_test_command(mock_config)

        assert command == "pytest"


class TestReportGeneration:
    """Test report generation and status handling."""

    def test_pytest_report_teststatus_retry(self) -> None:
        """Test report status for retry scenarios."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Mock report with retry properties
        mock_report = Mock()
        mock_report.user_properties = [("dd_retry_outcome", "failed"), ("dd_retry_reason", "Auto Test Retries")]

        result = plugin.pytest_report_teststatus(mock_report)

        assert result == ("dd_retry", "R", "RETRY FAILED (Auto Test Retries)")

    def test_pytest_report_teststatus_quarantined(self) -> None:
        """Test report status for quarantined tests in call phase."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Mock report with quarantined property (call phase)
        mock_report = Mock()
        mock_report.user_properties = [("dd_quarantined", True)]
        mock_report.when = "call"

        result = plugin.pytest_report_teststatus(mock_report)

        # In non-teardown phases, quarantined tests return empty strings (no logging)
        assert result == ("", "", "")

    def test_pytest_report_teststatus_quarantined_teardown(self) -> None:
        """Test report status for quarantined tests in teardown phase."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Mock report with quarantined property (teardown phase)
        mock_report = Mock()
        mock_report.user_properties = [("dd_quarantined", True)]
        mock_report.when = "teardown"

        result = plugin.pytest_report_teststatus(mock_report)

        # In teardown phase, quarantined tests show the quarantined status
        assert result == ("quarantined", "Q", ("QUARANTINED", {"blue": True}))

    def test_pytest_report_teststatus_normal(self) -> None:
        """Test report status for normal tests."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Mock normal report
        mock_report = Mock()
        mock_report.user_properties = []

        result = plugin.pytest_report_teststatus(mock_report)

        assert result is None


# =============================================================================
# UNIT TESTS (individual methods and helper functions)
# =============================================================================


class TestNodeIdToTestRef:
    """Unit tests for nodeid_to_test_ref function."""

    def test_nodeid_with_module_suite_and_name(self) -> None:
        """Test parsing a full nodeid with module, suite and test name."""
        nodeid = "tests/internal/test_example.py::TestClass::test_method"
        result = nodeid_to_test_ref(nodeid)

        assert result.suite.module.name == "tests/internal"
        assert result.suite.name == "test_example.py"
        assert result.name == "TestClass::test_method"

    def test_nodeid_with_suite_and_name_only(self) -> None:
        """Test parsing a nodeid with just suite and test name."""
        nodeid = "test_example.py::test_function"
        result = nodeid_to_test_ref(nodeid)

        assert result.suite.module.name == "."
        assert result.suite.name == "test_example.py"
        assert result.name == "test_function"

    def test_nodeid_fallback_format(self) -> None:
        """Test parsing a nodeid that doesn't match the expected format."""
        nodeid = "some_weird_format"
        result = nodeid_to_test_ref(nodeid)

        assert result.suite.module.name == "."
        assert result.suite.name == "."
        assert result.name == "some_weird_format"


class TestHelperFunctions:
    """Unit tests for helper functions."""

    def test_get_module_path_from_item_with_path(self) -> None:
        """Test _get_module_path_from_item when item has path attribute."""
        mock_item = Mock()
        mock_path = Mock()
        mock_path.absolute.return_value.parent = "/some/path"
        mock_item.path = mock_path

        result = _get_module_path_from_item(mock_item)

        assert str(result) == "/some/path"

    def test_get_module_path_from_item_with_module(self) -> None:
        """Test _get_module_path_from_item when item has module.__file__."""
        mock_item = Mock()
        # Remove path attribute to force fallback
        del mock_item.path
        mock_item.module.__file__ = "/some/path/file.py"

        from pathlib import Path

        result = _get_module_path_from_item(mock_item)

        assert result == Path("/some/path")

    def test_get_module_path_from_item_exception(self) -> None:
        """Test _get_module_path_from_item when exceptions occur."""
        mock_item = Mock()
        # Remove attributes to force exception
        del mock_item.path
        del mock_item.module

        from pathlib import Path

        result = _get_module_path_from_item(mock_item)

        assert result == Path.cwd()

    def test_get_exception_tags_with_excinfo(self) -> None:
        """Test _get_exception_tags with valid exception info."""
        mock_excinfo = Mock()
        mock_excinfo.type = ValueError
        mock_excinfo.value = ValueError("test error")
        mock_excinfo.tb = None

        result = _get_exception_tags(mock_excinfo)

        assert "error.type" in result
        assert "error.message" in result
        assert "error.stack" in result
        assert result["error.type"] == "builtins.ValueError"
        assert result["error.message"] == "test error"

    def test_get_exception_tags_with_none(self) -> None:
        """Test _get_exception_tags with None."""
        result = _get_exception_tags(None)
        assert result == {}

    def test_get_user_property_found(self) -> None:
        """Test _get_user_property when property exists."""
        mock_report = Mock()
        mock_report.user_properties = [("key1", "value1"), ("key2", "value2")]

        result = _get_user_property(mock_report, "key1")
        assert result == "value1"

    def test_get_user_property_not_found(self) -> None:
        """Test _get_user_property when property doesn't exist."""
        mock_report = Mock()
        mock_report.user_properties = [("key1", "value1")]

        result = _get_user_property(mock_report, "missing_key")
        assert result is None

    def test_get_user_property_no_properties(self) -> None:
        """Test _get_user_property when report has no user_properties."""
        mock_report = Mock()
        del mock_report.user_properties

        result = _get_user_property(mock_report, "any_key")
        assert result is None

    def test_get_test_parameters_json_with_callspec(self) -> None:
        """Test _get_test_parameters_json with valid callspec."""
        mock_item = Mock()
        mock_callspec = Mock()
        mock_callspec.params = {"param1": "value1", "param2": 42}
        mock_item.callspec = mock_callspec

        result = _get_test_parameters_json(mock_item)

        # Should return valid JSON
        import json

        parsed = json.loads(result or "{}")
        assert "arguments" in parsed
        assert "metadata" in parsed
        # Values are encoded using repr(), so strings get quotes
        assert parsed["arguments"]["param1"] == "'value1'"
        assert parsed["arguments"]["param2"] == "42"

    def test_get_test_parameters_json_no_callspec(self) -> None:
        """Test _get_test_parameters_json when item has no callspec."""
        mock_item = Mock()
        del mock_item.callspec

        result = _get_test_parameters_json(mock_item)
        assert result is None

    def test_encode_test_parameter_simple(self) -> None:
        """Test _encode_test_parameter with simple values."""
        assert _encode_test_parameter("string") == "'string'"
        assert _encode_test_parameter(42) == "42"
        assert _encode_test_parameter(True) == "True"

    def test_encode_test_parameter_removes_memory_addresses(self) -> None:
        """Test _encode_test_parameter removes memory addresses."""
        # Simulate object representation with memory address
        param_with_address = "MyObject at 0x7f8b1c0d2e40"
        result = _encode_test_parameter(param_with_address)

        # Memory address should be removed
        assert "at 0x" not in result
        assert result == "'MyObject'"


class TestPrivateMethods:
    """Unit tests for private methods that need more coverage."""

    def test_extract_longrepr_call_phase(self) -> None:
        """Test _extract_longrepr prioritizes call phase."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        reports = {
            "setup": Mock(longrepr="setup error"),
            "call": Mock(longrepr="call error"),
            "teardown": Mock(longrepr="teardown error"),
        }

        result = plugin._extract_longrepr(t.cast(t.Dict[str, TestReport], reports))
        assert result == "call error"

    def test_extract_longrepr_setup_fallback(self) -> None:
        """Test _extract_longrepr falls back to setup when call is missing."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        reports = {
            "setup": Mock(longrepr="setup error"),
            "teardown": Mock(longrepr="teardown error"),
        }

        result = plugin._extract_longrepr(t.cast(t.Dict[str, TestReport], reports))
        assert result == "setup error"

    def test_extract_longrepr_no_errors(self) -> None:
        """Test _extract_longrepr returns None when no errors."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        reports = {
            "setup": Mock(longrepr=None),
            "call": Mock(longrepr=None),
            "teardown": Mock(longrepr=None),
        }

        result = plugin._extract_longrepr(t.cast(t.Dict[str, TestReport], reports))
        assert result is None

    def test_check_applicable_retry_handlers_found(self) -> None:
        """Test _check_applicable_retry_handlers when handler applies."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Mock retry handlers
        handler1 = Mock()
        handler1.should_apply.return_value = False
        handler2 = Mock()
        handler2.should_apply.return_value = True

        mock_manager = Mock()
        mock_manager.retry_handlers = [handler1, handler2]
        plugin = TestOptPlugin(session_manager=mock_manager)

        mock_test = Mock()
        result = plugin._check_applicable_retry_handlers(mock_test)

        assert result == handler2
        handler1.should_apply.assert_called_once_with(mock_test)
        handler2.should_apply.assert_called_once_with(mock_test)

    def test_check_applicable_retry_handlers_none_found(self) -> None:
        """Test _check_applicable_retry_handlers when no handler applies."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Mock retry handlers that don't apply
        handler1 = Mock()
        handler1.should_apply.return_value = False
        handler2 = Mock()
        handler2.should_apply.return_value = False

        mock_manager = Mock()
        mock_manager.retry_handlers = [handler1, handler2]
        plugin = TestOptPlugin(session_manager=mock_manager)

        mock_test = Mock()
        result = plugin._check_applicable_retry_handlers(mock_test)

        assert result is None

    def test_mark_quarantined_test_report_as_skipped_call_phase(self) -> None:
        """Test quarantined test report modification for call phase."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        mock_item = pytest_item_mock("test_file.py::test_name").build()
        mock_report = Mock()
        mock_report.when = "call"

        plugin._mark_quarantined_test_report_as_skipped(mock_item, mock_report)

        assert mock_report.outcome == "skipped"
        assert mock_report.longrepr == (str(mock_item.path), 10, "Quarantined")

    def test_mark_quarantined_test_report_as_skipped_teardown_phase(self) -> None:
        """Test quarantined test report modification for teardown phase."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        mock_item = pytest_item_mock("test_file.py::test_name").build()
        mock_report = Mock()
        mock_report.when = "teardown"

        plugin._mark_quarantined_test_report_as_skipped(mock_item, mock_report)

        assert mock_report.outcome == "passed"

    def test_mark_quarantined_test_report_as_skipped_none_report(self) -> None:
        """Test quarantined test report modification with None report."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        mock_item = pytest_item_mock("test_file.py::test_name").build()

        # Should not raise exception
        plugin._mark_quarantined_test_report_as_skipped(mock_item, None)


# =============================================================================
# COVERAGE GAPS - Additional tests for missing methods
# =============================================================================


class TestSessionLifecycleMethods:
    """Test session lifecycle methods that need coverage."""

    def test_pytest_sessionfinish_normal_completion(self) -> None:
        """Test pytest_sessionfinish with normal exit status."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Set up session and manager
        plugin.session = Mock()
        plugin.manager = Mock()
        plugin.is_xdist_worker = False

        # Mock session with normal exit
        mock_session = Mock()
        mock_session.exitstatus = pytest.ExitCode.OK

        plugin.pytest_sessionfinish(mock_session)

        # Verify session was finished with PASS status
        from ddtestpy.internal.test_data import TestStatus

        plugin.session.set_status.assert_called_once_with(TestStatus.PASS)
        plugin.session.finish.assert_called_once()
        plugin.manager.writer.put_item.assert_called_once_with(plugin.session)
        plugin.manager.finish.assert_called_once()

    def test_pytest_sessionfinish_test_failure(self) -> None:
        """Test pytest_sessionfinish with test failures."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Set up session and manager
        plugin.session = Mock()
        plugin.manager = Mock()
        plugin.is_xdist_worker = False

        # Mock session with test failures
        mock_session = Mock()
        mock_session.exitstatus = pytest.ExitCode.TESTS_FAILED

        plugin.pytest_sessionfinish(mock_session)

        # Verify session was finished with FAIL status
        from ddtestpy.internal.test_data import TestStatus

        plugin.session.set_status.assert_called_once_with(TestStatus.FAIL)

    def test_pytest_sessionfinish_xdist_worker(self) -> None:
        """Test pytest_sessionfinish as xdist worker."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Set up session and manager
        plugin.session = Mock()
        plugin.manager = Mock()
        plugin.is_xdist_worker = True  # Worker mode

        # Mock session
        mock_session = Mock()
        mock_session.exitstatus = pytest.ExitCode.OK
        mock_session.config = Mock(workeroutput={})

        plugin.pytest_sessionfinish(mock_session)

        # Verify session was finished but NOT written (only main process writes)
        plugin.session.finish.assert_called_once()
        plugin.manager.writer.put_item.assert_not_called()
        plugin.manager.finish.assert_called_once()


class TestReportAndLoggingMethods:
    """Test report generation and logging methods."""

    def test_mark_test_report_as_retry_success(self) -> None:
        """Test _mark_test_report_as_retry when report exists."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        mock_handler = Mock()
        mock_handler.get_pretty_name.return_value = "Test Handler"

        mock_report = Mock()
        mock_report.outcome = "failed"
        mock_report.user_properties = []
        reports = {"call": mock_report}

        result = plugin._mark_test_report_as_retry(t.cast(t.Dict[str, TestReport], reports), mock_handler, "call")

        assert result is True
        assert mock_report.outcome == "dd_retry"
        expected_properties = [("dd_retry_outcome", "failed"), ("dd_retry_reason", "Test Handler")]
        assert mock_report.user_properties == expected_properties

    def test_mark_test_report_as_retry_missing(self) -> None:
        """Test _mark_test_report_as_retry when report doesn't exist."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        mock_handler = Mock()
        reports: t.Dict[str, Mock] = {}

        result = plugin._mark_test_report_as_retry(t.cast(t.Dict[str, TestReport], reports), mock_handler, "call")

        assert result is False

    def test_mark_test_reports_as_retry_call_phase(self) -> None:
        """Test _mark_test_reports_as_retry prioritizes call phase."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        mock_handler = Mock()
        mock_call_report = Mock()
        mock_call_report.outcome = "failed"
        mock_call_report.user_properties = []

        reports = {
            "setup": Mock(),
            "call": mock_call_report,
        }

        plugin._mark_test_reports_as_retry(t.cast(t.Dict[str, TestReport], reports), mock_handler)

        # Should only mark call report
        assert mock_call_report.outcome == "dd_retry"

    def test_mark_test_reports_as_retry_setup_fallback(self) -> None:
        """Test _mark_test_reports_as_retry falls back to setup when call missing."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        mock_handler = Mock()
        mock_setup_report = Mock()
        mock_setup_report.outcome = "failed"
        mock_setup_report.user_properties = []

        reports = {
            "setup": mock_setup_report,
            "teardown": Mock(),
        }

        plugin._mark_test_reports_as_retry(t.cast(t.Dict[str, TestReport], reports), mock_handler)

        # Should mark setup report
        assert mock_setup_report.outcome == "dd_retry"


class TestQuarantineHandling:
    """Test quarantine handling methods."""

    def test_mark_quarantined_test_report_group_as_skipped_with_call(self) -> None:
        """Test quarantine group marking when call report exists."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        mock_item = pytest_item_mock("test_file.py::test_name").build()
        mock_call = Mock()
        mock_setup = Mock()
        mock_teardown = Mock()

        reports = {
            "call": mock_call,
            "setup": mock_setup,
            "teardown": mock_teardown,
        }

        plugin._mark_quarantined_test_report_group_as_skipped(mock_item, t.cast(t.Dict[str, TestReport], reports))

        # Call should be marked as skipped, others as passed
        assert mock_call.outcome == "skipped"
        assert mock_setup.outcome == "passed"
        assert mock_teardown.outcome == "passed"

    def test_mark_quarantined_test_report_group_as_skipped_no_call(self) -> None:
        """Test quarantine group marking when call report is missing."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        mock_item = pytest_item_mock("test_file.py::test_name").build()
        mock_setup = Mock()
        mock_teardown = Mock()

        reports = {
            "setup": mock_setup,
            "teardown": mock_teardown,
        }

        plugin._mark_quarantined_test_report_group_as_skipped(mock_item, t.cast(t.Dict[str, TestReport], reports))

        # Setup should be marked as skipped, teardown as passed
        assert mock_setup.outcome == "skipped"
        assert mock_teardown.outcome == "passed"


class TestXdistPlugin:
    """Test XdistTestOptPlugin specific functionality."""

    def test_pytest_configure_node(self) -> None:
        """Test pytest_configure_node method."""
        session_manager = session_manager_mock().build_mock()
        plugin = XdistTestOptPlugin(session_manager=session_manager)

        # Mock session with session_id
        plugin.session = Mock()
        plugin.session.item_id = "test-session-123"

        # Mock node
        mock_node = Mock()
        mock_node.workerinput = {}

        plugin.pytest_configure_node(mock_node)

        # Verify session ID was passed to worker
        assert mock_node.workerinput["dd_session_id"] == "test-session-123"


class TestOutcomeProcessing:
    """Test test outcome processing methods."""

    def test_get_test_outcome_pass(self) -> None:
        """Test _get_test_outcome for passing test."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Set up reports for a passing test
        setup_report = Mock()
        setup_report.failed = False
        setup_report.skipped = False

        call_report = Mock()
        call_report.failed = False
        call_report.skipped = False

        teardown_report = Mock()
        teardown_report.failed = False
        teardown_report.skipped = False

        plugin.reports_by_nodeid["test_id"] = {
            "setup": setup_report,
            "call": call_report,
            "teardown": teardown_report,
        }

        plugin.excinfo_by_report = {
            setup_report: None,
            call_report: None,
            teardown_report: None,
        }

        from ddtestpy.internal.test_data import TestStatus

        status, tags = plugin._get_test_outcome("test_id")

        assert status == TestStatus.PASS
        assert tags == {}

    def test_get_test_outcome_fail(self) -> None:
        """Test _get_test_outcome for failing test."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Set up reports for a failing test
        setup_report = Mock()
        setup_report.failed = False
        setup_report.skipped = False

        call_report = Mock()
        call_report.failed = True
        call_report.skipped = False

        plugin.reports_by_nodeid["test_id"] = {
            "setup": setup_report,
            "call": call_report,
        }

        # Mock exception info
        mock_excinfo = Mock()
        mock_excinfo.type = ValueError
        mock_excinfo.value = ValueError("test failed")
        mock_excinfo.tb = None

        plugin.excinfo_by_report = {
            setup_report: None,
            call_report: mock_excinfo,
        }

        from ddtestpy.internal.test_data import TestStatus

        status, tags = plugin._get_test_outcome("test_id")

        assert status == TestStatus.FAIL
        assert "error.type" in tags
        assert "error.message" in tags

    def test_get_test_outcome_skip_with_reason(self) -> None:
        """Test _get_test_outcome for skipped test with reason."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Set up reports for a skipped test
        setup_report = Mock()
        setup_report.failed = False
        setup_report.skipped = True

        plugin.reports_by_nodeid["test_id"] = {
            "setup": setup_report,
        }

        # Mock exception info with skip reason
        mock_excinfo = Mock()
        mock_excinfo.value = "Test skipped because X"

        plugin.excinfo_by_report = {
            setup_report: mock_excinfo,
        }

        from ddtestpy.internal.test_data import TestStatus
        from ddtestpy.internal.test_data import TestTag

        status, tags = plugin._get_test_outcome("test_id")

        assert status == TestStatus.SKIP
        assert tags[TestTag.SKIP_REASON] == "Test skipped because X"

    def test_get_test_outcome_skip_no_reason(self) -> None:
        """Test _get_test_outcome for skipped test without excinfo."""
        mock_manager = session_manager_mock().build_mock()
        plugin = TestOptPlugin(session_manager=mock_manager)

        # Set up reports for a skipped test
        setup_report = Mock()
        setup_report.failed = False
        setup_report.skipped = True

        plugin.reports_by_nodeid["test_id"] = {
            "setup": setup_report,
        }

        plugin.excinfo_by_report = {
            setup_report: None,
        }

        from ddtestpy.internal.test_data import TestStatus
        from ddtestpy.internal.test_data import TestTag

        status, tags = plugin._get_test_outcome("test_id")

        assert status == TestStatus.SKIP
        assert tags[TestTag.SKIP_REASON] == "Unknown skip reason"
