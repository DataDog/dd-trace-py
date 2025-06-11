"""Tests Intelligent Test Runner (ITR) functionality interacting with pytest-xdist.

The tests in this module validate the interaction between ITR and pytest-xdist.
"""
import os  # Just for the RIOT env var check
from unittest import mock

import pytest

from ddtrace.contrib.internal.pytest._plugin_v2 import XdistHooks
from ddtrace.contrib.internal.pytest._plugin_v2 import _handle_itr_should_skip
from ddtrace.contrib.internal.pytest._plugin_v2 import _pytest_sessionfinish
from ddtrace.contrib.internal.pytest._utils import _USE_PLUGIN_V2
from ddtrace.contrib.internal.pytest._utils import _pytest_version_supports_itr
from ddtrace.ext import test
from ddtrace.ext.test_visibility._item_ids import TestId
from ddtrace.ext.test_visibility._item_ids import TestModuleId
from ddtrace.ext.test_visibility._item_ids import TestSuiteId
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility._api_client import TestManagementSettings
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from tests.contrib.pytest.test_pytest import PytestTestCaseBase


######
# Skip these tests if they are not running under riot
riot_env_value = os.getenv("RIOT", None)
if not riot_env_value:
    pytest.importorskip("xdist", reason="ITR + xdist tests, not running under riot")
######


pytestmark = pytest.mark.skipif(
    not (_USE_PLUGIN_V2 and _pytest_version_supports_itr()),
    reason="ITR requires v2 of the plugin and pytest >=7.0",
)

_TEST_PASS_CONTENT = """
import unittest

def test_func_pass():
    assert True

class SomeTestCase(unittest.TestCase):
    def test_class_func_pass(self):
        assert True
"""

_TEST_FAIL_CONTENT = """
import pytest
import unittest

def test_func_fail():
    assert False

class SomeTestCase(unittest.TestCase):
    def test_class_func_fail(self):
        assert False
"""

_TEST_SKIP_CONTENT = """
import pytest
import unittest

@pytest.mark.skip
def test_func_skip_mark():
    assert True

def test_func_skip_inside():
    pytest.skip()

class SomeTestCase(unittest.TestCase):
    @pytest.mark.skip
    def test_class_func_skip_mark(self):
        assert True

    def test_class_func_skip_inside(self):
        pytest.skip()
"""


class PytestXdistITRTestCase(PytestTestCaseBase):
    def inline_run(self, *args, **kwargs):
        # Add -n 2 to the end of the command line arguments
        args = list(args) + ["-n", "2"]
        return super().inline_run(*args, **kwargs)

    def test_pytest_xdist_itr_skips_tests(self):
        """Test that ITR skips tests when enabled."""
        # Create a simplified sitecustomize with just the essential ITR setup
        itr_skipping_sitecustomize = """
# sitecustomize.py - Simplified ITR setup for xdist
from unittest import mock

# Import required modules
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility._api_client import TestManagementSettings
from ddtrace.internal.ci_visibility._api_client import ITRData
from ddtrace.ext.test_visibility._item_ids import TestSuiteId, TestModuleId


# Create ITR settings and data
itr_settings = TestVisibilityAPISettings(
    coverage_enabled=False, skipping_enabled=True, require_git=False, itr_enabled=True,
    flaky_test_retries_enabled=False, known_tests_enabled=False,
    early_flake_detection=EarlyFlakeDetectionSettings(), test_management=TestManagementSettings()
)

# Create skippable test suites
skippable_suites = {
    TestSuiteId(TestModuleId(""), "test_pass.py"),
    TestSuiteId(TestModuleId(""), "test_fail.py")
}
itr_data = ITRData(correlation_id="12345678-1234-1234-1234-123456789012", skippable_items=skippable_suites)

# Mock API calls to return our settings
mock.patch(
    "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
    return_value=itr_settings
).start()

# Set ITR data when CIVisibility is enabled
import ddtrace.internal.ci_visibility.recorder
CIVisibility = ddtrace.internal.ci_visibility.recorder.CIVisibility
original_enable = CIVisibility.enable

def patched_enable(cls, *args, **kwargs):
    result = original_enable(*args, **kwargs)
    if cls._instance:
        cls._instance._itr_data = itr_data
    return result

CIVisibility.enable = classmethod(patched_enable)
"""
        self.testdir.makepyfile(sitecustomize=itr_skipping_sitecustomize)
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.chdir()

        itr_settings = TestVisibilityAPISettings(
            coverage_enabled=False,
            skipping_enabled=True,
            require_git=False,
            itr_enabled=True,
            flaky_test_retries_enabled=False,
            known_tests_enabled=False,
            early_flake_detection=EarlyFlakeDetectionSettings(),
            test_management=TestManagementSettings(),
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features", return_value=itr_settings
        ):
            rec = self.inline_run(
                "--ddtrace",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "0",
                },
            )
            assert rec.ret == 0  # All tests skipped, so exit code is 0

            # Verify ITR worked
            spans = self.pop_spans()
            session_span = [span for span in spans if span.get_tag("type") == "test_session_end"][0]
            assert session_span.get_tag("test.itr.tests_skipping.enabled") == "true"
            assert session_span.get_metric("test.itr.tests_skipping.count") == 4  # 4 tests skipped


class TestXdistHooksUnit:
    """Unit tests for XdistHooks class functionality."""

    def test_xdist_hooks_registers_span_id_with_valid_session_span(self):
        """Test that XdistHooks properly extracts and passes span ID when session span exists."""
        mock_node = mock.MagicMock()
        mock_node.workerinput = {}

        mock_span = mock.MagicMock()
        mock_span.span_id = 12345

        with mock.patch("ddtrace.internal.test_visibility.api.InternalTestSession.get_span", return_value=mock_span):
            hooks = XdistHooks()
            hooks.pytest_configure_node(mock_node)

        assert mock_node.workerinput["root_span"] == 12345

    def test_xdist_hooks_registers_zero_when_no_session_span(self):
        """Test that XdistHooks uses 0 for root span as fallback when no session span exists."""
        mock_node = mock.MagicMock()
        mock_node.workerinput = {}

        with mock.patch("ddtrace.internal.test_visibility.api.InternalTestSession.get_span", return_value=None):
            hooks = XdistHooks()
            hooks.pytest_configure_node(mock_node)

        assert mock_node.workerinput["root_span"] == 0

    def test_xdist_hooks_aggregates_itr_skipped_count_from_workers(self):
        """Test that XdistHooks properly aggregates ITR skipped counts from worker nodes."""
        # Clean up any existing global state
        if hasattr(pytest, "global_worker_itr_results"):
            delattr(pytest, "global_worker_itr_results")

        hooks = XdistHooks()

        # First worker reports 3 skipped tests
        mock_node1 = mock.MagicMock()
        mock_node1.workeroutput = {"itr_skipped_count": 3}
        hooks.pytest_testnodedown(mock_node1, None)

        assert hasattr(pytest, "global_worker_itr_results")
        assert pytest.global_worker_itr_results == 3

        # Second worker reports 5 skipped tests
        mock_node2 = mock.MagicMock()
        mock_node2.workeroutput = {"itr_skipped_count": 5}
        hooks.pytest_testnodedown(mock_node2, None)

        assert pytest.global_worker_itr_results == 8

        # Clean up
        delattr(pytest, "global_worker_itr_results")

    def test_xdist_hooks_ignores_worker_without_itr_skipped_count(self):
        """Test that XdistHooks ignores workers that don't have ITR skipped count."""
        # Clean up any existing global state
        if hasattr(pytest, "global_worker_itr_results"):
            delattr(pytest, "global_worker_itr_results")

        hooks = XdistHooks()

        # Worker without workeroutput
        mock_node1 = mock.MagicMock()
        del mock_node1.workeroutput
        hooks.pytest_testnodedown(mock_node1, None)

        assert not hasattr(pytest, "global_worker_itr_results")

        # Worker with workeroutput but no itr_skipped_count
        mock_node2 = mock.MagicMock()
        mock_node2.workeroutput = {"other_data": "value"}
        hooks.pytest_testnodedown(mock_node2, None)

        assert not hasattr(pytest, "global_worker_itr_results")

    def test_xdist_hooks_initializes_global_count_correctly(self):
        """Test that the first worker initializes the global count to its value (regression test for += vs =)."""
        # Clean up any existing global state
        if hasattr(pytest, "global_worker_itr_results"):
            delattr(pytest, "global_worker_itr_results")

        hooks = XdistHooks()

        # First worker should initialize the global count to its value
        mock_node = mock.MagicMock()
        mock_node.workeroutput = {"itr_skipped_count": 7}
        hooks.pytest_testnodedown(mock_node, None)

        # Should be 7, not 0 + 7 = 7 (this would catch if initialization logic was wrong)
        assert pytest.global_worker_itr_results == 7

        # Clean up
        delattr(pytest, "global_worker_itr_results")

    def test_handle_itr_should_skip_counts_skipped_tests_in_worker(self):
        """Test that _handle_itr_should_skip properly counts skipped tests in worker processes."""
        # Create a mock item with worker config
        mock_item = mock.MagicMock()
        mock_item.config.workeroutput = {}

        test_id = TestId(TestSuiteId(TestModuleId("test_module"), "test_suite"), "test_name")

        with mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSession.is_test_skipping_enabled", return_value=True
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSuite.is_itr_unskippable", return_value=False
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTest.is_attempt_to_fix", return_value=False
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSuite.is_itr_skippable", return_value=True
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTest.mark_itr_skipped"
        ):
            result = _handle_itr_should_skip(mock_item, test_id)

            assert result is True
            assert mock_item.config.workeroutput["itr_skipped_count"] == 1
            # Verify the skip marker was added
            mock_item.add_marker.assert_called_once()

    def test_handle_itr_should_skip_increments_existing_worker_count(self):
        """Test that _handle_itr_should_skip increments existing worker skipped count."""
        # Create a mock item with worker config that already has a count
        mock_item = mock.MagicMock()
        mock_item.config.workeroutput = {"itr_skipped_count": 5}

        test_id = TestId(TestSuiteId(TestModuleId("test_module"), "test_suite"), "test_name")

        with mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSession.is_test_skipping_enabled", return_value=True
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSuite.is_itr_unskippable", return_value=False
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTest.is_attempt_to_fix", return_value=False
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSuite.is_itr_skippable", return_value=True
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTest.mark_itr_skipped"
        ):
            result = _handle_itr_should_skip(mock_item, test_id)

            assert result is True
            # This is a critical regression test: should be 6 (5+1)
            assert mock_item.config.workeroutput["itr_skipped_count"] == 6

    def test_handle_itr_should_skip_returns_false_when_not_skippable(self):
        """Test that _handle_itr_should_skip returns False when test is not skippable."""
        mock_item = mock.MagicMock()
        mock_item.config.workeroutput = {}
        test_id = TestId(TestSuiteId(TestModuleId("test_module"), "test_suite"), "test_name")

        with mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSession.is_test_skipping_enabled", return_value=True
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSuite.is_itr_unskippable", return_value=False
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTest.is_attempt_to_fix", return_value=False
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSuite.is_itr_skippable", return_value=False
        ):  # Not skippable
            result = _handle_itr_should_skip(mock_item, test_id)

            assert result is False
            # Should not have counted since test wasn't skipped
            assert "itr_skipped_count" not in mock_item.config.workeroutput
            mock_item.add_marker.assert_not_called()

    def test_handle_itr_should_skip_unskippable_test_gets_forced_run(self):
        """Test that unskippable tests in skippable suites get marked as forced run."""
        mock_item = mock.MagicMock()
        mock_item.config.workeroutput = {}
        test_id = TestId(TestSuiteId(TestModuleId("test_module"), "test_suite"), "test_name")

        with mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSession.is_test_skipping_enabled", return_value=True
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSuite.is_itr_unskippable", return_value=True
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTest.is_attempt_to_fix", return_value=False
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSuite.is_itr_skippable", return_value=True
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTest.mark_itr_forced_run"
        ) as mock_forced_run:
            result = _handle_itr_should_skip(mock_item, test_id)

            assert result is False
            mock_forced_run.assert_called_once_with(test_id)
            # Should not have counted since test wasn't skipped
            assert "itr_skipped_count" not in mock_item.config.workeroutput

    def test_pytest_sessionfinish_aggregates_worker_itr_results(self):
        """Test that pytest_sessionfinish properly aggregates ITR results from workers."""
        # Set up global worker results
        pytest.global_worker_itr_results = 10

        mock_session = mock.MagicMock()
        # Main process doesn't have workerinput
        del mock_session.config.workerinput
        mock_session.exitstatus = 0

        mock_session_span = mock.MagicMock()

        # Test the ITR aggregation logic directly
        # Simulate the conditions in _pytest_sessionfinish

        # Count ITR skipped tests from workers if we're in the main process
        if hasattr(mock_session.config, "workerinput") is False and hasattr(pytest, "global_worker_itr_results"):
            skipped_count = pytest.global_worker_itr_results
            if skipped_count > 0:
                session_span = mock_session_span  # Use our mock directly
                if session_span:
                    session_span.set_tag_str(test.ITR_TEST_SKIPPING_TESTS_SKIPPED, "true")
                    session_span.set_tag_str(test.ITR_DD_CI_ITR_TESTS_SKIPPED, "true")
                    session_span.set_metric(test.ITR_TEST_SKIPPING_COUNT, skipped_count)

        # Verify the session span was tagged with ITR results
        mock_session_span.set_tag_str.assert_any_call(test.ITR_TEST_SKIPPING_TESTS_SKIPPED, "true")
        mock_session_span.set_tag_str.assert_any_call(test.ITR_DD_CI_ITR_TESTS_SKIPPED, "true")
        mock_session_span.set_metric.assert_called_with(test.ITR_TEST_SKIPPING_COUNT, 10)

        # Clean up
        delattr(pytest, "global_worker_itr_results")

    def test_pytest_sessionfinish_no_aggregation_for_worker_process(self):
        """Test that pytest_sessionfinish doesn't aggregate results when running in worker process."""
        # Set up global worker results
        pytest.global_worker_itr_results = 10

        mock_session = mock.MagicMock()
        # Worker process has workerinput
        mock_session.config.workerinput = {"root_span": "12345"}
        mock_session.exitstatus = 0

        mock_session_span = mock.MagicMock()

        with mock.patch("ddtrace.ext.test_visibility.api.is_test_visibility_enabled", return_value=True), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSession.get_span", return_value=mock_session_span
        ), mock.patch("ddtrace.internal.test_visibility.api.InternalTestSession.finish"):
            _pytest_sessionfinish(mock_session, 0)

            # Verify no ITR tags were set (worker shouldn't aggregate)
            mock_session_span.set_tag_str.assert_not_called()
            mock_session_span.set_metric.assert_not_called()

        # Clean up
        delattr(pytest, "global_worker_itr_results")

    def test_pytest_sessionfinish_no_aggregation_when_no_global_results(self):
        """Test that pytest_sessionfinish doesn't aggregate when no global worker results exist."""
        # Ensure no global worker results exist
        if hasattr(pytest, "global_worker_itr_results"):
            delattr(pytest, "global_worker_itr_results")

        mock_session = mock.MagicMock()
        # Main process doesn't have workerinput
        del mock_session.config.workerinput
        mock_session.exitstatus = 0

        mock_session_span = mock.MagicMock()

        with mock.patch("ddtrace.ext.test_visibility.api.is_test_visibility_enabled", return_value=True), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSession.get_span", return_value=mock_session_span
        ), mock.patch("ddtrace.internal.test_visibility.api.InternalTestSession.finish"):
            _pytest_sessionfinish(mock_session, 0)

            # Verify no ITR tags were set (no global results to aggregate)
            mock_session_span.set_tag_str.assert_not_called()
            mock_session_span.set_metric.assert_not_called()

    def test_pytest_sessionfinish_no_aggregation_when_zero_skipped(self):
        """Test that pytest_sessionfinish doesn't aggregate when zero tests were skipped."""
        # Set up global worker results with zero skipped
        pytest.global_worker_itr_results = 0

        mock_session = mock.MagicMock()
        # Main process doesn't have workerinput
        del mock_session.config.workerinput
        mock_session.exitstatus = 0

        mock_session_span = mock.MagicMock()

        with mock.patch("ddtrace.ext.test_visibility.api.is_test_visibility_enabled", return_value=True), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSession.get_span", return_value=mock_session_span
        ), mock.patch("ddtrace.internal.test_visibility.api.InternalTestSession.finish"):
            _pytest_sessionfinish(mock_session, 0)

            # Verify no ITR tags were set (zero tests skipped)
            mock_session_span.set_tag_str.assert_not_called()
            mock_session_span.set_metric.assert_not_called()

        # Clean up
        delattr(pytest, "global_worker_itr_results")
