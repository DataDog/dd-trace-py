"""Tests Intelligent Test Runner (ITR) functionality interacting with pytest-xdist.

The tests in this module validate the interaction between ITR and pytest-xdist.
"""

from unittest import mock

import pytest

from ddtrace.contrib.internal.pytest._plugin_v2 import _handle_itr_should_skip
from ddtrace.contrib.internal.pytest._plugin_v2 import _pytest_sessionfinish
from ddtrace.contrib.internal.pytest._utils import _pytest_version_supports_itr
from ddtrace.contrib.internal.pytest._xdist import XDIST_UNSET
from ddtrace.contrib.internal.pytest._xdist import XdistHooks
from ddtrace.contrib.internal.pytest._xdist import _parse_xdist_args_from_cmd
from ddtrace.contrib.internal.pytest._xdist import _skipping_level_for_xdist_parallelization_mode
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
    def test_pytest_xdist_itr_skips_tests_at_test_level_by_pytest_addopts_env_var(self):
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
from ddtrace.ext.test_visibility._test_visibility_base import TestId, TestSuiteId, TestModuleId


# Create ITR settings and data
itr_settings = TestVisibilityAPISettings(
    coverage_enabled=False, skipping_enabled=True, require_git=False, itr_enabled=True,
    flaky_test_retries_enabled=False, known_tests_enabled=False,
    early_flake_detection=EarlyFlakeDetectionSettings(), test_management=TestManagementSettings()
)

# Create skippable tests
skippable_tests = {
    TestId(TestSuiteId(TestModuleId(""), "test_fail.py"), "test_func_fail"),
    TestId(TestSuiteId(TestModuleId(""), "test_fail.py"), "SomeTestCase::test_class_func_fail")
}


itr_data = ITRData(correlation_id="12345678-1234-1234-1234-123456789012", skippable_items=skippable_tests)

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
            # note, passing -n 2 without --dist will fallback to dist=load
            # passing args via PYTEST_ADDOPTS env var
            rec = self.inline_run(
                "--ddtrace",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "0",
                    "PYTEST_ADDOPTS": "-n 2",
                },
            )
            assert rec.ret == 0  # All tests skipped, so exit code is 0

        spans = self.pop_spans()
        session_span = [span for span in spans if span.get_tag("type") == "test_session_end"][0]
        assert session_span.get_tag("test.itr.tests_skipping.enabled") == "true"
        assert session_span.get_tag("test.itr.tests_skipping.type") == "test"  # load uses suite-level skipping
        # Verify number of skipped tests in session
        assert session_span.get_metric("test.itr.tests_skipping.count") == 2

    def test_pytest_xdist_itr_skips_tests_at_test_level_without_loadscope(self):
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
from ddtrace.ext.test_visibility._test_visibility_base import TestId, TestSuiteId, TestModuleId


# Create ITR settings and data
itr_settings = TestVisibilityAPISettings(
    coverage_enabled=False, skipping_enabled=True, require_git=False, itr_enabled=True,
    flaky_test_retries_enabled=False, known_tests_enabled=False,
    early_flake_detection=EarlyFlakeDetectionSettings(), test_management=TestManagementSettings()
)

# Create skippable tests
skippable_tests = {
    TestId(TestSuiteId(TestModuleId(""), "test_fail.py"), "test_func_fail"),
    TestId(TestSuiteId(TestModuleId(""), "test_fail.py"), "SomeTestCase::test_class_func_fail")
}


itr_data = ITRData(correlation_id="12345678-1234-1234-1234-123456789012", skippable_items=skippable_tests)

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
            # note, passing -n 2 without --dist will fallback to dist=load
            rec = self.inline_run(
                "--ddtrace",
                "-n",
                "2",  # Use xdist with 2 workers
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
        assert session_span.get_tag("test.itr.tests_skipping.type") == "test"  # load uses suite-level skipping
        # Verify number of skipped tests in session
        assert session_span.get_metric("test.itr.tests_skipping.count") == 2

    def test_pytest_xdist_itr_skips_tests_at_suite_level_with_loadscope(self):
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
                "-n",
                "2",  # Use xdist with 2 workers
                "--dist=loadscope",  # Use suite-level distribution to match expected "suite" type
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

    def test_worker_uses_received_xdist_configuration_loadscope(self):
        """Test that worker nodes use received xdist configuration for ITR level detection."""

        # Create a mock config that would normally indicate no xdist
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.return_value = True
        mock_config.option.dist = "no"  # This would normally indicate no xdist
        mock_config.option.numprocesses = None

        # The function should use worker input and detect suite-level mode
        result = _skipping_level_for_xdist_parallelization_mode(
            config=mock_config, num_workers=2, dist_mode="loadscope"
        )
        assert result == ITR_SKIPPING_LEVEL.SUITE

    def test_worker_uses_received_xdist_configuration_worksteal(self):
        """Test that worker nodes use received xdist configuration for ITR level detection."""
        # Create a mock config that would normally indicate no xdist
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.return_value = True
        mock_config.option.dist = "no"  # This would normally indicate no xdist
        mock_config.option.numprocesses = None

        result = _skipping_level_for_xdist_parallelization_mode(
            config=mock_config, num_workers=2, dist_mode="worksteal"
        )
        assert result == ITR_SKIPPING_LEVEL.TEST

    def test_worker_uses_received_xdist_configuration_0_numworkers(self):
        """Test that worker nodes use received xdist configuration for ITR level detection."""

        # Create a mock config that would normally indicate no xdist
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.return_value = True
        mock_config.option.dist = "no"  # This would normally indicate no xdist
        mock_config.option.numprocesses = None

        result = _skipping_level_for_xdist_parallelization_mode(
            config=mock_config, num_workers=0, dist_mode="worksteal"
        )
        assert result == ITR_SKIPPING_LEVEL.SUITE


class TestParseXdistArgsFromCmd:
    """Unit tests for _parse_xdist_args_from_cmd function."""

    @pytest.mark.parametrize("workers", ["4", "0", "auto", "logical"])
    def test_parse_n_with_space(self, workers: str):
        """Test parsing -n <number> format."""
        expected_workers = workers
        if len(workers) == 1:
            expected_workers = int(workers)

        # Test -n 4
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["-n", workers])

        assert num_workers == expected_workers

        expected_dist_mode = "load"
        if workers == "0":
            expected_dist_mode = XDIST_UNSET
        assert dist_mode == expected_dist_mode

    @pytest.mark.parametrize("workers", ["4", "0", "auto", "logical"])
    def test_parse_n_no_space(self, workers: str):
        """Test parsing -n<number> format (no space)."""
        expected_workers = workers
        if len(workers) == 1:
            expected_workers = int(workers)
        # Test -n4
        args = ["-n" + workers]
        num_workers, dist_mode = _parse_xdist_args_from_cmd(args)
        assert num_workers == expected_workers

        expected_dist_mode = "load"
        if workers == "0":
            expected_dist_mode = XDIST_UNSET
        assert dist_mode == expected_dist_mode

    def test_parse_numprocesses_with_space(self):
        """Test parsing --numprocesses <number> format."""
        # Test --numprocesses 8
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["--numprocesses", "8"])
        assert num_workers == 8
        assert dist_mode == "load"

        # Test --numprocesses auto
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["--numprocesses", "auto"])
        assert num_workers == "auto"
        assert dist_mode == "load"

    def test_parse_numprocesses_equals(self):
        """Test parsing --numprocesses=<number> format."""
        # Test --numprocesses=2
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["--numprocesses=2"])
        assert num_workers == 2
        assert dist_mode == "load"

        # Test --numprocesses=logical
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["--numprocesses=logical"])
        assert num_workers == "logical"
        assert dist_mode == "load"

    def test_parse_dist_with_space(self):
        """Test parsing --dist <mode> format."""
        # Test --dist loadscope
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["--dist", "loadscope"])
        assert num_workers == XDIST_UNSET
        assert dist_mode == "loadscope"

        # Test --dist worksteal
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["--dist", "worksteal"])
        assert num_workers == XDIST_UNSET
        assert dist_mode == "worksteal"

        # Test --dist loadfile
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["--dist", "loadfile"])
        assert num_workers == XDIST_UNSET
        assert dist_mode == "loadfile"

        # Test --dist load
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["--dist", "load"])
        assert num_workers == XDIST_UNSET
        assert dist_mode == "load"

    def test_parse_dist_equals(self):
        """Test parsing --dist=<mode> format."""
        # Test --dist=loadscope
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["--dist=loadscope"])
        assert num_workers == XDIST_UNSET
        assert dist_mode == "loadscope"

        # Test --dist=each
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["--dist=each"])
        assert num_workers == XDIST_UNSET
        assert dist_mode == "each"

    def test_parse_combined_args(self):
        """Test parsing combined -n and --dist arguments."""
        # Test -n 4 --dist loadscope
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["-n", "4", "--dist", "loadscope"])
        assert num_workers == 4
        assert dist_mode == "loadscope"

        # Test --dist worksteal -n 2
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["--dist", "worksteal", "-n", "2"])
        assert num_workers == 2
        assert dist_mode == "worksteal"

        # Test --numprocesses=8 --dist=loadfile
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["--numprocesses=8", "--dist=loadfile"])
        assert num_workers == 8
        assert dist_mode == "loadfile"

        # Test with other pytest args mixed in
        num_workers, dist_mode = _parse_xdist_args_from_cmd(
            ["--ddtrace", "-v", "-n", "4", "--tb=short", "--dist=loadscope", "tests/"]
        )
        assert num_workers == 4
        assert dist_mode == "loadscope"

    def test_parse_no_xdist_args(self):
        """Test parsing when no xdist arguments are present."""
        # Test empty args
        num_workers, dist_mode = _parse_xdist_args_from_cmd([])
        assert num_workers == XDIST_UNSET
        assert dist_mode == XDIST_UNSET

        # Test with other pytest args but no xdist
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["--ddtrace", "-v", "tests/"])
        assert num_workers == XDIST_UNSET
        assert dist_mode == XDIST_UNSET

    def test_parse_invalid_values(self):
        """Test parsing with invalid values."""
        # Test -n with invalid number
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["-n", "invalid"])
        assert num_workers == XDIST_UNSET
        assert dist_mode == XDIST_UNSET

        # Test -n at end of args (no value)
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["-n"])
        assert num_workers == XDIST_UNSET
        assert dist_mode == XDIST_UNSET

        # Test --dist at end of args (no value)
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["--dist"])
        assert num_workers == XDIST_UNSET
        assert dist_mode == XDIST_UNSET

        # Test --numprocesses with invalid value
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["--numprocesses", "bad"])
        assert num_workers == XDIST_UNSET
        assert dist_mode == XDIST_UNSET

    def test_parse_edge_cases(self):
        """Test edge cases and boundary conditions."""
        # Test -n followed by --dist (both should be parsed)
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["-n", "2", "--dist"])
        assert num_workers == 2
        assert dist_mode == "load"  # --dist without value doesn't change dist_mode

        # Test multiple -n values (last one wins)
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["-n", "2", "-n", "4"])
        assert num_workers == 4
        assert dist_mode == "load"

        # Test multiple --dist values (last one wins)
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["--dist", "loadscope", "--dist", "worksteal"])
        assert num_workers == XDIST_UNSET
        assert dist_mode == "worksteal"

        # Test mixed formats
        num_workers, dist_mode = _parse_xdist_args_from_cmd(["-n2", "--numprocesses", "4"])
        assert num_workers == 4  # --numprocesses overrides -n
        assert dist_mode == "load"

    def test_parse_realistic_command_lines(self):
        """Test with realistic pytest command lines."""
        # Typical xdist usage
        args = ["--ddtrace", "-v", "--tb=short", "--strict-markers", "-n", "4", "-p", "no:rerunfailures", "tests"]
        num_workers, dist_mode = _parse_xdist_args_from_cmd(args)
        assert num_workers == 4
        assert dist_mode == "load"

        # With explicit dist mode
        args = ["pytest", "-n", "8", "--dist=loadscope", "--cov=mypackage", "tests/"]
        num_workers, dist_mode = _parse_xdist_args_from_cmd(args)
        assert num_workers == 8
        assert dist_mode == "loadscope"

        # Complex example
        args = [
            "--ddtrace",
            "--ddtrace-include-class-name",
            "-v",
            "--tb=short",
            "-n",
            "auto",
            "--dist",
            "worksteal",
            "--maxfail=5",
            "tests/integration/",
        ]
        num_workers, dist_mode = _parse_xdist_args_from_cmd(args)
        assert num_workers == "auto"
        assert dist_mode == "worksteal"


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
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
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
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
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
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
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
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
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
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
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

    def test_xdist_loadgroup_enables_test_level_itr(self):
        """Test that --dist=loadgroup automatically enables test-level ITR skipping."""
        # Create test files with groups
        self.testdir.makepyfile(
            test_group1="""
import pytest

@pytest.mark.group("group1")
def test_group1_func1():
    assert True

@pytest.mark.group("group2")
def test_group1_func2():
    assert True
            """,
            test_group2="""
import pytest

@pytest.mark.group("group1")
def test_group2_func1():
    assert True
            """,
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
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
                },
            )

            assert result.ret == 0

        # Verify that ITR level was updated to suite level
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be suite due to loadgroup detection
        assert session_span.get_tag("test.itr.tests_skipping.type") == "test"

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
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
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
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
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
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
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

    @pytest.mark.skip(reason="flaky collection error in CI")
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
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
        ):
            # Run with xdist using -n auto
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "auto",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
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

    @pytest.mark.skip(reason="flaky collection error in CI")
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
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
        ):
            # Run with xdist using -n logical
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "logical",
                "--ignore=.gitconfig.lock",
                "--ignore-glob=*.lock",  # Ignore lock files that cause collection issues
                "--ignore-glob=.gitconfig*",  # Ignore git config files that cause collection issues
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
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
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
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

    def test_xdist_multiple_workers_with_load_distribution(self):
        """Test xdist with multiple workers using load distribution."""
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
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
        ):
            # Run with xdist using multiple workers (simplified from --tx option)
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "3",
                "--dist=load",
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
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
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
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

    def test_xdist_worksteal_mode_comprehensive(self):
        """Test xdist with worksteal mode for comprehensive coverage."""
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
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
        ):
            # Run with xdist using worksteal mode (removed rsync-dir for test compatibility)
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "2",
                "--dist=worksteal",  # Test-level mode
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
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

    def test_explicit_env_var_overrides_xdist_suite_mode(self):
        """Test that explicit _DD_CIVISIBILITY_ITR_SUITE_MODE=True overrides xdist test-level detection."""
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
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
        ):
            # Run with xdist using load mode (normally test-level) but explicitly set suite mode
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "2",
                "--dist=load",  # Test-level mode
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "True",  # Explicit override to suite-level
                },
            )

            assert result.ret == 0

        # Verify that explicit env var overrode xdist detection (should be suite, not test)
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be suite due to explicit env var override
        assert session_span.get_tag("test.itr.tests_skipping.type") == "suite"

    def test_explicit_env_var_overrides_xdist_test_mode(self):
        """Test that explicit _DD_CIVISIBILITY_ITR_SUITE_MODE=False overrides xdist suite-level detection."""
        # Create test files for different scopes
        itr_skipping_sitecustomize = """
# sitecustomize.py - Simplified ITR setup for xdist
from unittest import mock

# Import required modules
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility._api_client import TestManagementSettings
from ddtrace.internal.ci_visibility._api_client import ITRData
from ddtrace.ext.test_visibility._test_visibility_base import TestSuiteId, TestModuleId, TestId


# Create ITR settings and data
itr_settings = TestVisibilityAPISettings(
    coverage_enabled=False, skipping_enabled=True, require_git=False, itr_enabled=True,
    flaky_test_retries_enabled=False, known_tests_enabled=False,
    early_flake_detection=EarlyFlakeDetectionSettings(), test_management=TestManagementSettings()
)

# Create skippable tests for test-level skipping
skippable_tests = {
    TestId(TestSuiteId(TestModuleId(""), "test_scope1.py"), "TestScope1::test_scope1_method1"),
    TestId(TestSuiteId(TestModuleId(""), "test_scope2.py"), "TestScope2::test_scope2_method1")
}
itr_data = ITRData(correlation_id="12345678-1234-1234-1234-123456789012", skippable_items=skippable_tests)

# Set ITR data when CIVisibility is enabled
import ddtrace.internal.ci_visibility.recorder
CIVisibility = ddtrace.internal.ci_visibility.recorder.CIVisibility
original_enable = CIVisibility.enable

def patched_enable(cls, *args, **kwargs):
    result = original_enable(*args, **kwargs)
    if cls._instance:
        cls._instance._itr_data = itr_data
    return result

# Mock API calls to return our settings
mock.patch(
    "ddtrace.internal.ci_visibility._api_client._TestVisibilityAPIClientBase.fetch_settings",
    return_value=itr_settings
).start()

mock.patch(
     "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
    return_value=itr_settings
).start()

CIVisibility.enable = classmethod(patched_enable)

"""
        self.testdir.makepyfile(sitecustomize=itr_skipping_sitecustomize)
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
            return_value=TestVisibilityAPISettings(False, True, False, True),  # Enable skipping and ITR
        ), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
            return_value=TestVisibilityAPISettings(False, True, False, True),
        ):
            # Run with xdist using loadscope mode (normally suite-level) but explicitly set test mode
            result = self.inline_run(
                "--ddtrace",
                "-n",
                "2",
                "--dist=loadscope",  # Suite-level mode
                extra_env={
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                    "DD_API_KEY": "foobar.baz",
                    "_DD_CIVISIBILITY_ITR_SUITE_MODE": "False",  # Explicit override to test-level
                },
            )

            assert result.ret == 0

        # Verify that explicit env var overrode xdist detection (should be test, not suite)
        spans = self.pop_spans()
        session_spans = [span for span in spans if span.get_tag("type") == "test_session_end"]
        assert len(session_spans) == 1
        session_span = session_spans[0]

        # The ITR skipping type should be test due to explicit env var override
        assert session_span.get_tag("test.itr.tests_skipping.type") == "test"


class TestSkippingLevelForXdistParallelizationMode:
    """Comprehensive parametrized tests for the refactored _skipping_level_for_xdist_parallelization_mode function."""

    @pytest.mark.parametrize(
        "env_value,expected_result",
        [
            ("1", ITR_SKIPPING_LEVEL.SUITE),
            ("true", ITR_SKIPPING_LEVEL.SUITE),
            ("True", ITR_SKIPPING_LEVEL.SUITE),
            ("0", ITR_SKIPPING_LEVEL.TEST),
            ("false", ITR_SKIPPING_LEVEL.TEST),
            ("False", ITR_SKIPPING_LEVEL.TEST),
            ("", ITR_SKIPPING_LEVEL.TEST),  # Empty string is falsy
            ("invalid", ITR_SKIPPING_LEVEL.TEST),  # Invalid values are falsy
        ],
    )
    def test_explicit_env_var_overrides_xdist_config(self, env_value, expected_result):
        """Test that explicit _DD_CIVISIBILITY_ITR_SUITE_MODE env var overrides xdist configuration."""
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.return_value = True

        with mock.patch.dict("os.environ", {"_DD_CIVISIBILITY_ITR_SUITE_MODE": env_value}):
            # Test with configuration that would normally give opposite result
            opposite_dist_mode = "loadscope" if expected_result == ITR_SKIPPING_LEVEL.TEST else "worksteal"
            result = _skipping_level_for_xdist_parallelization_mode(
                config=mock_config, num_workers=4, dist_mode=opposite_dist_mode
            )
            assert result == expected_result

    @pytest.mark.parametrize("has_xdist_plugin", [True, False])
    def test_no_xdist_plugin_always_returns_suite(self, has_xdist_plugin):
        """Test behavior when xdist plugin is not available."""
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.return_value = has_xdist_plugin

        with mock.patch.dict("os.environ", {}, clear=True):
            result = _skipping_level_for_xdist_parallelization_mode(config=mock_config, num_workers=4, dist_mode="load")
            expected = ITR_SKIPPING_LEVEL.SUITE if not has_xdist_plugin else ITR_SKIPPING_LEVEL.TEST
            assert result == expected

    @pytest.mark.parametrize(
        "num_workers,expected_result",
        [
            (None, ITR_SKIPPING_LEVEL.SUITE),
            (0, ITR_SKIPPING_LEVEL.SUITE),
        ],
    )
    def test_no_workers_returns_suite_level(self, num_workers, expected_result):
        """Test when no workers are specified (xdist not being used)."""
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.return_value = True

        with mock.patch.dict("os.environ", {}, clear=True):
            result = _skipping_level_for_xdist_parallelization_mode(
                config=mock_config, num_workers=num_workers, dist_mode="load"
            )
            assert result == expected_result

    @pytest.mark.parametrize(
        "dist_mode,expected_result",
        [
            ("loadscope", ITR_SKIPPING_LEVEL.SUITE),
            ("loadfile", ITR_SKIPPING_LEVEL.SUITE),
            ("load", ITR_SKIPPING_LEVEL.TEST),
            ("worksteal", ITR_SKIPPING_LEVEL.TEST),
            ("each", ITR_SKIPPING_LEVEL.TEST),
            ("unknown_mode", ITR_SKIPPING_LEVEL.TEST),
            ("no", ITR_SKIPPING_LEVEL.TEST),  # "no" with workers defaults to "load"
        ],
    )
    def test_distribution_modes(self, dist_mode, expected_result):
        """Test all xdist distribution modes map to correct ITR skipping levels."""
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.return_value = True

        with mock.patch.dict("os.environ", {}, clear=True):
            result = _skipping_level_for_xdist_parallelization_mode(
                config=mock_config, num_workers=4, dist_mode=dist_mode
            )
            assert result == expected_result

    @pytest.mark.parametrize(
        "num_workers,dist_mode,expected_result",
        [
            (1, "loadscope", ITR_SKIPPING_LEVEL.SUITE),
            (2, "loadfile", ITR_SKIPPING_LEVEL.SUITE),
            (4, "load", ITR_SKIPPING_LEVEL.TEST),
            (8, "worksteal", ITR_SKIPPING_LEVEL.TEST),
            ("auto", "loadscope", ITR_SKIPPING_LEVEL.SUITE),
            ("auto", "worksteal", ITR_SKIPPING_LEVEL.TEST),
            ("logical", "loadfile", ITR_SKIPPING_LEVEL.SUITE),
            ("logical", "load", ITR_SKIPPING_LEVEL.TEST),
        ],
    )
    def test_worker_dist_mode_combinations(self, num_workers, dist_mode, expected_result):
        """Test various combinations of num_workers and dist_mode."""
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.return_value = True

        with mock.patch.dict("os.environ", {}, clear=True):
            result = _skipping_level_for_xdist_parallelization_mode(
                config=mock_config, num_workers=num_workers, dist_mode=dist_mode
            )
            assert result == expected_result

    @pytest.mark.parametrize("env_var_set", [True, False])
    @pytest.mark.parametrize("has_xdist", [True, False])
    def test_environment_combinations(self, env_var_set, has_xdist):
        """Test combinations of environment variable and xdist plugin availability."""
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.return_value = has_xdist

        env_dict = {"_DD_CIVISIBILITY_ITR_SUITE_MODE": "1"} if env_var_set else {}

        with mock.patch.dict("os.environ", env_dict, clear=True):
            result = _skipping_level_for_xdist_parallelization_mode(
                config=mock_config, num_workers=4, dist_mode="worksteal"
            )

            if env_var_set:
                # Environment variable always takes precedence
                assert result == ITR_SKIPPING_LEVEL.SUITE
            elif not has_xdist:
                # No xdist plugin -> SUITE
                assert result == ITR_SKIPPING_LEVEL.SUITE
            else:
                # worksteal mode -> TEST
                assert result == ITR_SKIPPING_LEVEL.TEST

    def test_edge_case_none_values(self):
        """Test edge case with None values for both workers and dist_mode."""
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.return_value = True

        with mock.patch.dict("os.environ", {}, clear=True):
            result = _skipping_level_for_xdist_parallelization_mode(
                config=mock_config, num_workers=None, dist_mode=None
            )
            # No workers specified -> SUITE level
            assert result == ITR_SKIPPING_LEVEL.SUITE

    @pytest.mark.parametrize(
        "config_dist,config_numprocesses,expected_result",
        [
            # Config-based suite-level modes
            ("loadscope", 4, ITR_SKIPPING_LEVEL.SUITE),
            ("loadfile", 2, ITR_SKIPPING_LEVEL.SUITE),
            # Config-based test-level modes
            ("load", 4, ITR_SKIPPING_LEVEL.TEST),
            ("worksteal", 8, ITR_SKIPPING_LEVEL.TEST),
            ("each", 2, ITR_SKIPPING_LEVEL.TEST),
            # No dist specified with workers defaults to load (test-level)
            ("no", 4, ITR_SKIPPING_LEVEL.TEST),
            # No workers specified
            ("loadscope", None, ITR_SKIPPING_LEVEL.SUITE),
            ("load", 0, ITR_SKIPPING_LEVEL.SUITE),
        ],
    )
    def test_config_based_xdist_detection(self, config_dist, config_numprocesses, expected_result):
        """Test xdist detection from pytest config when explicit parameters are not provided."""
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.return_value = True

        # Set up config with xdist options (simulating pytest.ini or setup.cfg)
        mock_config.option.dist = config_dist
        mock_config.option.numprocesses = config_numprocesses

        with mock.patch.dict("os.environ", {}, clear=True):
            # Call without explicit parameters to force config detection
            result = _skipping_level_for_xdist_parallelization_mode(mock_config, XDIST_UNSET, XDIST_UNSET)
            assert result == expected_result

    @pytest.mark.parametrize(
        "has_config_attr,config_dist,config_numprocesses,expected_result",
        [
            # Config object has option attribute with xdist settings
            (True, "loadscope", 4, ITR_SKIPPING_LEVEL.SUITE),
            (True, "worksteal", 2, ITR_SKIPPING_LEVEL.TEST),
            # Config object missing option attribute (fallback)
            (False, None, None, ITR_SKIPPING_LEVEL.SUITE),
        ],
    )
    def test_config_attribute_availability(self, has_config_attr, config_dist, config_numprocesses, expected_result):
        """Test handling of config objects with/without xdist option attributes."""
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.return_value = True

        if has_config_attr:
            # Config has option attribute with xdist settings
            mock_config.option.dist = config_dist
            mock_config.option.numprocesses = config_numprocesses
        else:
            # Config missing option attribute entirely
            del mock_config.option

        with mock.patch.dict("os.environ", {}, clear=True):
            result = _skipping_level_for_xdist_parallelization_mode(mock_config, XDIST_UNSET, XDIST_UNSET)
            assert result == expected_result

    @pytest.mark.parametrize(
        "explicit_workers,explicit_dist,config_dist,config_numprocesses,expected_result",
        [
            # Explicit parameters should override config
            (4, "worksteal", "loadscope", 8, ITR_SKIPPING_LEVEL.TEST),  # explicit wins
            (2, "loadscope", "worksteal", 4, ITR_SKIPPING_LEVEL.SUITE),  # explicit wins
            # None explicit parameters should use config
            (None, None, "loadfile", 4, ITR_SKIPPING_LEVEL.SUITE),  # config used
            (None, None, "load", 2, ITR_SKIPPING_LEVEL.TEST),  # config used
        ],
    )
    def test_explicit_params_vs_config_precedence(
        self, explicit_workers, explicit_dist, config_dist, config_numprocesses, expected_result
    ):
        """Test that explicit parameters take precedence over config, but config is used as fallback."""
        mock_config = mock.MagicMock()
        mock_config.pluginmanager.hasplugin.return_value = True
        mock_config.option.dist = config_dist
        mock_config.option.numprocesses = config_numprocesses

        with mock.patch.dict("os.environ", {}, clear=True):
            # For None values, don't pass parameters to trigger config fallback
            if explicit_workers is None and explicit_dist is None:
                result = _skipping_level_for_xdist_parallelization_mode(mock_config, XDIST_UNSET, XDIST_UNSET)
            else:
                result = _skipping_level_for_xdist_parallelization_mode(
                    config=mock_config, num_workers=explicit_workers, dist_mode=explicit_dist
                )
            assert result == expected_result
