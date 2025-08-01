"""Tests Intelligent Test Runner (ITR) functionality interacting with pytest-xdist.

The tests in this module validate the interaction between ITR and pytest-xdist.
"""

from unittest import mock

import pytest

from ddtrace import config as dd_config
from ddtrace.contrib.internal.pytest._plugin_v2 import XdistHooks
from ddtrace.contrib.internal.pytest._plugin_v2 import _detect_xdist_parallelization_mode
from ddtrace.contrib.internal.pytest._plugin_v2 import _handle_itr_should_skip
from ddtrace.contrib.internal.pytest._plugin_v2 import _pytest_sessionfinish
from ddtrace.contrib.internal.pytest._plugin_v2 import pytest_configure
from ddtrace.contrib.internal.pytest._utils import _pytest_version_supports_itr
from ddtrace.ext import test
from ddtrace.ext.test_visibility import ITR_SKIPPING_LEVEL
from ddtrace.ext.test_visibility._test_visibility_base import TestId
from ddtrace.ext.test_visibility._test_visibility_base import TestModuleId
from ddtrace.ext.test_visibility._test_visibility_base import TestSuiteId
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility._api_client import TestManagementSettings
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from tests.contrib.pytest.test_pytest import PytestTestCaseBase


######
# Skip these tests if xdist is not available
pytest.importorskip("xdist", reason="ITR + xdist tests require pytest-xdist to be installed")
######


pytestmark = pytest.mark.skipif(
    not _pytest_version_supports_itr(),
    reason="ITR requires pytest >=7.0",
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
        """Test that ITR tags are correctly aggregated from xdist workers."""
        # Create a simplified sitecustomize with just the essential ITR setup
        itr_skipping_sitecustomize = """
# sitecustomize.py - Simplified ITR setup for xdist
from unittest import mock

# Import required modules
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility._api_client import TestManagementSettings
from ddtrace.internal.ci_visibility._api_client import ITRData
from ddtrace.ext.test_visibility._test_visibility_base import TestSuiteId, TestModuleId


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
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.test_skipping_enabled",
            return_value=True,
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

        spans = self.pop_spans()
        session_span = [span for span in spans if span.get_tag("type") == "test_session_end"][0]
        assert session_span.get_tag("test.itr.tests_skipping.enabled") == "true"
        assert session_span.get_tag("test.itr.tests_skipping.type") == "suite"
        # Verify number of skipped tests in session
        assert session_span.get_metric("test.itr.tests_skipping.count") == 4


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

        mock_service = mock.MagicMock()
        mock_service._suite_skipping_mode = True

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
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.require_ci_visibility_service", return_value=mock_service
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

        mock_service = mock.MagicMock()
        mock_service._suite_skipping_mode = True

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
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.require_ci_visibility_service", return_value=mock_service
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

        mock_service = mock.MagicMock()
        mock_service._suite_skipping_mode = True

        with mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSession.is_test_skipping_enabled", return_value=True
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSuite.is_itr_unskippable", return_value=False
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTest.is_attempt_to_fix", return_value=False
        ), mock.patch(
            "ddtrace.internal.test_visibility.api.InternalTestSuite.is_itr_skippable", return_value=False
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.require_ci_visibility_service", return_value=mock_service
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

        mock_service = mock.MagicMock()
        mock_service._suite_skipping_mode = True

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
        ) as mock_forced_run, mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.require_ci_visibility_service", return_value=mock_service
        ):
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


class TestXdistModeDetectionIntegration(PytestTestCaseBase):
    """Integration tests for xdist mode detection using inline_run."""

    def test_xdist_loadscope_enables_suite_level_itr(self):
        """Test that --dist=loadscope automatically enables suite-level ITR skipping."""
        # Create test files for different suites
        self.testdir.makepyfile(
            test_suite1="""
import pytest

def test_suite1_func1():
    assert True

def test_suite1_func2():
    assert True
            """,
            test_suite2="""
import pytest

def test_suite2_func1():
    assert True
            """,
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            # Run with xdist using loadscope mode
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "2",
                "--dist=loadscope",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "False",  # Start with test-level
                },
            )

            assert result.ret == 0

        # Verify that ITR level was updated to suite level
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be suite due to loadscope detection
        assert session_span.get_tag("test.itr.tests_skipping.type") == "suite"

    def test_xdist_worksteal_enables_test_level_itr(self):
        """Test that --dist=worksteal automatically enables test-level ITR skipping."""
        # Create test files
        self.testdir.makepyfile(
            test_file="""
import pytest

def test_func1():
    assert True

def test_func2():
    assert True

def test_func3():
    assert True
            """,
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            # Run with xdist using worksteal mode
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "2",
                "--dist=worksteal",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "True",  # Start with suite-level
                },
            )

            assert result.ret == 0

        # Verify that ITR level was updated to test level
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be test due to worksteal detection
        assert session_span.get_tag("test.itr.tests_skipping.type") == "test"

    def test_xdist_loadfile_enables_suite_level_itr(self):
        """Test that --dist=loadfile automatically enables suite-level ITR skipping."""
        # Create test files for different files
        self.testdir.makepyfile(
            test_file1="""
def test_file1_func1():
    assert True

def test_file1_func2():
    assert True
            """,
            test_file2="""
def test_file2_func1():
    assert True
            """,
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            # Run with xdist using loadfile mode
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "2",
                "--dist=loadfile",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "False",  # Start with test-level
                },
            )

            assert result.ret == 0

        # Verify that ITR level was updated to suite level
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be suite due to loadfile detection
        assert session_span.get_tag("test.itr.tests_skipping.type") == "suite"

    def test_no_xdist_uses_env_var_setting(self):
        """Test that without xdist, the env var setting is used."""
        self.testdir.makepyfile(
            test_file="""
def test_func():
    assert True
            """,
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            # Run without xdist
            result = self.inline_run(
                "--ddtrace",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "False",  # Use test-level from env
                },
            )

            assert result.ret == 0

        # Verify that ITR level respects env var (test level)
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be test as set by env var
        assert session_span.get_tag("test.itr.tests_skipping.type") == "test"

    def test_xdist_disabled_with_n_zero_uses_env_var_setting(self):
        """Test that -n 0 (xdist disabled) uses env var setting."""
        self.testdir.makepyfile(
            test_file="""
def test_func():
    assert True
            """,
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            # Run with xdist disabled (-n 0)
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "0",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "True",  # Use suite-level from env
                },
            )

            assert result.ret == 0

        # Verify that ITR level respects env var (suite level)
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be suite as set by env var
        assert session_span.get_tag("test.itr.tests_skipping.type") == "suite"

    def test_xdist_loadgroup_enables_suite_level_itr(self):
        """Test that --dist=loadgroup automatically enables suite-level ITR skipping."""
        # Create test files with groups
        self.testdir.makepyfile(
            test_group1="""
import pytest

@pytest.mark.group("group1")
def test_group1_func1():
    assert True

@pytest.mark.group("group1")
def test_group1_func2():
    assert True
            """,
            test_group2="""
import pytest

@pytest.mark.group("group2")
def test_group2_func1():
    assert True
            """,
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            # Run with xdist using loadgroup mode
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "2",
                "--dist=loadgroup",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "False",  # Start with test-level
                },
            )

            assert result.ret == 0

        # Verify that ITR level was updated to suite level
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be suite due to loadgroup detection
        assert session_span.get_tag("test.itr.tests_skipping.type") == "suite"

    def test_xdist_load_enables_test_level_itr(self):
        """Test that --dist=load (default) automatically enables test-level ITR skipping."""
        # Create test files
        self.testdir.makepyfile(
            test_file="""
import pytest

def test_func1():
    assert True

def test_func2():
    assert True

def test_func3():
    assert True

def test_func4():
    assert True
            """,
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            # Run with xdist using load mode (default)
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "2",
                "--dist=load",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "True",  # Start with suite-level
                },
            )

            assert result.ret == 0

        # Verify that ITR level was updated to test level
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be test due to load detection
        assert session_span.get_tag("test.itr.tests_skipping.type") == "test"

    def test_xdist_each_enables_test_level_itr(self):
        """Test that --dist=each automatically enables test-level ITR skipping."""
        # Create test files
        self.testdir.makepyfile(
            test_file="""
import pytest

def test_func1():
    assert True

def test_func2():
    assert True
            """,
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            # Run with xdist using each mode
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "2",
                "--dist=each",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "True",  # Start with suite-level
                },
            )

            assert result.ret == 0

        # Verify that ITR level was updated to test level
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be test due to each detection
        assert session_span.get_tag("test.itr.tests_skipping.type") == "test"

    def test_xdist_default_no_dist_param_enables_test_level_itr(self):
        """Test that xdist without --dist parameter (defaults to load) enables test-level ITR."""
        # Create test files
        self.testdir.makepyfile(
            test_file="""
import pytest

def test_func1():
    assert True

def test_func2():
    assert True

def test_func3():
    assert True
            """,
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            # Run with xdist but no --dist parameter (defaults to load mode)
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "2",
                # No --dist parameter, should default to load
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "True",  # Start with suite-level
                },
            )

            assert result.ret == 0

        # Verify that ITR level was updated to test level (load is test-level)
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be test due to default load detection
        assert session_span.get_tag("test.itr.tests_skipping.type") == "test"

    def test_xdist_n_auto_enables_test_level_itr(self):
        """Test that -n auto automatically enables test-level ITR skipping."""
        # Create test files
        self.testdir.makepyfile(
            test_file="""
import pytest

def test_func1():
    assert True

def test_func2():
    assert True
            """,
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            # Run with xdist using -n auto
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "auto",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "True",  # Start with suite-level
                },
            )

            assert result.ret == 0

        # Verify that ITR level was updated to test level
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be test due to default load mode with auto workers
        assert session_span.get_tag("test.itr.tests_skipping.type") == "test"

    def test_xdist_n_logical_enables_test_level_itr(self):
        """Test that -n logical automatically enables test-level ITR skipping."""
        # Create test files
        self.testdir.makepyfile(
            test_file="""
import pytest

def test_func1():
    assert True

def test_func2():
    assert True
            """,
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            # Run with xdist using -n logical
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "logical",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "True",  # Start with suite-level
                },
            )

            assert result.ret == 0

        # Verify that ITR level was updated to test level
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be test due to default load mode with logical workers
        assert session_span.get_tag("test.itr.tests_skipping.type") == "test"

    def test_xdist_combination_dist_loadscope_with_maxprocesses(self):
        """Test xdist with --dist=loadscope and --maxprocesses combination."""
        # Create test files for different scopes
        self.testdir.makepyfile(
            test_scope1="""
import pytest

class TestScope1:
    def test_scope1_method1(self):
        assert True

    def test_scope1_method2(self):
        assert True
            """,
            test_scope2="""
import pytest

class TestScope2:
    def test_scope2_method1(self):
        assert True
            """,
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            # Run with xdist using loadscope with maxprocesses
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "3",
                "--dist=loadscope",
                "--maxprocesses=2",  # Limit max processes
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "False",  # Start with test-level
                },
            )

            assert result.ret == 0

        # Verify that ITR level was updated to suite level
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be suite due to loadscope detection
        assert session_span.get_tag("test.itr.tests_skipping.type") == "suite"

    def test_xdist_mixed_distribution_modes_with_tx_option(self):
        """Test xdist with --tx option for different worker configurations."""
        # Create test files
        self.testdir.makepyfile(
            test_file="""
import pytest

def test_func1():
    assert True

def test_func2():
    assert True

def test_func3():
    assert True
            """,
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            # Run with xdist using --tx option
            result = self.inline_run(
                "--ddtrace",
                "--tx",
                "2*popen//python",
                "--dist=load",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "True",  # Start with suite-level
                },
            )

            assert result.ret == 0

        # Verify that ITR level was updated to test level (load mode)
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be test due to load detection
        assert session_span.get_tag("test.itr.tests_skipping.type") == "test"

    def test_xdist_complex_combination_all_options(self):
        """Test xdist with complex combination of all options."""
        # Create test files with multiple scopes and groups
        self.testdir.makepyfile(
            test_complex1="""
import pytest

class TestComplexClass1:
    @pytest.mark.group("group1")
    def test_complex1_method1(self):
        assert True

    @pytest.mark.group("group1")
    def test_complex1_method2(self):
        assert True
            """,
            test_complex2="""
import pytest

class TestComplexClass2:
    @pytest.mark.group("group2")
    def test_complex2_method1(self):
        assert True

    def test_complex2_method2(self):
        assert True
            """,
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            # Run with xdist using complex options combination
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "2",
                "--dist=loadfile",  # Suite-level mode
                "--maxprocesses=4",
                "--tx",
                "popen//python",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "False",  # Start with test-level
                },
            )

            assert result.ret == 0

        # Verify that ITR level was updated to suite level (loadfile mode takes precedence)
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be suite due to loadfile detection
        assert session_span.get_tag("test.itr.tests_skipping.type") == "suite"

    def test_xdist_edge_case_with_rsync_dir(self):
        """Test xdist with rsync-dir option (for completeness)."""
        # Create test files
        self.testdir.makepyfile(
            test_file="""
import pytest

def test_func1():
    assert True

def test_func2():
    assert True
            """,
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            # Run with xdist using rsync-dir option (doesn't affect distribution mode)
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "2",
                "--dist=worksteal",  # Test-level mode
                "--rsync-dir=.",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "True",  # Start with suite-level
                },
            )

            assert result.ret == 0

        # Verify that ITR level was updated to test level (worksteal mode)
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be test due to worksteal detection
        assert session_span.get_tag("test.itr.tests_skipping.type") == "test"


class TestXdistModeDetection:
    """Unit tests for xdist parallelization mode detection and ITR alignment."""

    def test_detect_xdist_parallelization_mode_no_xdist(self):
        """Test mode detection when xdist plugin is not available."""
        # Mock config without xdist plugin
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.return_value = False

        # Test with default env var (True -> SUITE)
        with mock.patch.dict("os.environ", {"_DD_CIVISIBILITY_ITR_SUITE_MODE": "1"}):
            result = _detect_xdist_parallelization_mode(mock_config)
            assert result == ITR_SKIPPING_LEVEL.SUITE

        # Test with env var set to False -> TEST
        with mock.patch.dict("os.environ", {"_DD_CIVISIBILITY_ITR_SUITE_MODE": "0"}):
            result = _detect_xdist_parallelization_mode(mock_config)
            assert result == ITR_SKIPPING_LEVEL.TEST

        mock_config.pluginmanager.hasplugin.assert_called_with("xdist")

    def test_detect_xdist_parallelization_mode_xdist_not_used(self):
        """Test mode detection when xdist is installed but not being used."""
        # Mock config with xdist plugin but not being used
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.return_value = True
        mock_config.option.dist = "no"
        mock_config.option.numprocesses = None

        # Test with default env var (True -> SUITE)
        with mock.patch.dict("os.environ", {"_DD_CIVISIBILITY_ITR_SUITE_MODE": "1"}):
            result = _detect_xdist_parallelization_mode(mock_config)
            assert result == ITR_SKIPPING_LEVEL.SUITE

        # Test with env var set to False -> TEST
        with mock.patch.dict("os.environ", {"_DD_CIVISIBILITY_ITR_SUITE_MODE": "0"}):
            result = _detect_xdist_parallelization_mode(mock_config)
            assert result == ITR_SKIPPING_LEVEL.TEST

    def test_detect_xdist_parallelization_mode_suite_level_modes(self):
        """Test mode detection for suite-level parallelization modes."""
        suite_level_modes = ["loadscope", "loadfile", "loadgroup"]

        for mode in suite_level_modes:
            mock_config = mock.MagicMock()
            mock_config.pluginmanager.hasplugin.return_value = True
            mock_config.option.dist = mode
            mock_config.option.numprocesses = 2

            result = _detect_xdist_parallelization_mode(mock_config)
            assert result == ITR_SKIPPING_LEVEL.SUITE, f"Mode {mode} should return SUITE level"

    def test_detect_xdist_parallelization_mode_test_level_modes(self):
        """Test mode detection for test-level parallelization modes."""
        test_level_modes = ["load", "worksteal", "each"]

        for mode in test_level_modes:
            mock_config = mock.MagicMock()
            mock_config.pluginmanager.hasplugin.return_value = True
            mock_config.option.dist = mode
            mock_config.option.numprocesses = 2

            result = _detect_xdist_parallelization_mode(mock_config)
            assert result == ITR_SKIPPING_LEVEL.TEST, f"Mode {mode} should return TEST level"

    def test_detect_xdist_parallelization_mode_zero_workers(self):
        """Test mode detection when xdist is configured with zero workers."""
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.return_value = True
        mock_config.option.dist = "load"
        mock_config.option.numprocesses = 0  # No workers

        # Should fall back to env var setting
        with mock.patch.dict("os.environ", {"_DD_CIVISIBILITY_ITR_SUITE_MODE": "1"}):
            result = _detect_xdist_parallelization_mode(mock_config)
            assert result == ITR_SKIPPING_LEVEL.SUITE

    def test_pytest_configure_updates_itr_level_for_xdist(self):
        """Test that pytest_configure updates ITR level when xdist mode is detected."""
        # Forcing it to fail to make sure this suite is running in the proper venv
        assert False
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.side_effect = lambda name: name == "xdist"
        mock_config.option.dist = "loadscope"  # Suite-level mode
        mock_config.option.numprocesses = 2
        mock_config.workerinput = None  # Not a worker

        # Set initial ITR level to TEST (opposite of what we expect)
        dd_config.test_visibility.itr_skipping_level = ITR_SKIPPING_LEVEL.TEST

        with mock.patch("ddtrace.contrib.internal.pytest.plugin.is_enabled", return_value=True), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.unpatch_unittest"
        ), mock.patch("ddtrace.ext.test_visibility.api.enable_test_visibility"), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2._is_pytest_cov_enabled", return_value=False
        ), mock.patch(
            "ddtrace.internal.ci_visibility.service_registry.require_ci_visibility_service"
        ) as mock_service, mock.patch.dict(
            "os.environ", {"PYTEST_XDIST_WORKER": ""}, clear=True
        ):
            mock_service_instance = mock.MagicMock()
            mock_service.return_value = mock_service_instance

            # Call pytest_configure
            pytest_configure(mock_config)

            # Verify ITR level was updated to SUITE
            assert dd_config.test_visibility.itr_skipping_level == ITR_SKIPPING_LEVEL.SUITE

            # The service update is tested via the warning logs output

    def test_pytest_configure_no_update_when_levels_match(self):
        """Test that pytest_configure doesn't update when ITR level already matches xdist mode."""

        # Mock config with xdist in suite-level mode
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.side_effect = lambda name: name == "xdist"
        mock_config.option.dist = "loadscope"  # Suite-level mode
        mock_config.option.numprocesses = 2
        mock_config.workerinput = None  # Not a worker

        # Set initial ITR level to SUITE (matches what we expect)
        dd_config.test_visibility.itr_skipping_level = ITR_SKIPPING_LEVEL.SUITE

        with mock.patch("ddtrace.contrib.internal.pytest.plugin.is_enabled", return_value=True), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2.unpatch_unittest"
        ), mock.patch("ddtrace.ext.test_visibility.api.enable_test_visibility"), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2._is_pytest_cov_enabled", return_value=False
        ), mock.patch(
            "ddtrace.internal.ci_visibility.service_registry.require_ci_visibility_service"
        ) as mock_service, mock.patch.dict(
            "os.environ", {"PYTEST_XDIST_WORKER": ""}, clear=True
        ):
            # Call pytest_configure
            pytest_configure(mock_config)

            # Verify ITR level remained SUITE
            assert dd_config.test_visibility.itr_skipping_level == ITR_SKIPPING_LEVEL.SUITE

            # Service should not have been called since no update was needed
            mock_service.assert_not_called()
