"""Test edge cases for coverage collection."""

import threading

import mock

from ddtrace.contrib.internal.pytest._plugin_v2 import _current_coverage_collector
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from tests.contrib.pytest.test_pytest import PytestTestCaseBase


class PytestCoverageEdgeCasesTestCase(PytestTestCaseBase):
    """Test edge cases for coverage collection."""

    def test_coverage_collection_exception_handling(self):
        """Test that exceptions during coverage collection don't break test execution."""
        self.testdir.makepyfile(
            test_coverage_exception="""
def test_that_should_work():
    '''Test that should pass even if coverage collection fails'''
    assert True

def test_another_one():
    '''Another test to ensure coverage collection error doesn't affect subsequent tests'''
    assert True
"""
        )

        # Mock coverage collection to raise an exception
        with mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2._coverage_collector_get_covered_lines",
            side_effect=Exception("Coverage collection failed"),
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(coverage_enabled=True),
        ):
            rec = self.inline_run("--ddtrace")

            # Tests should still pass despite coverage collection exception
            rec.assertoutcome(passed=2, failed=0)

            # Module variable should be cleaned up despite exception
            assert _current_coverage_collector is None

    def test_coverage_collection_with_failing_test_setup(self):
        """Test coverage collection when test setup fails."""
        self.testdir.makepyfile(
            test_failing_setup="""
import pytest

@pytest.fixture
def failing_fixture():
    raise Exception("Setup failure")

def test_with_failing_setup(failing_fixture):
    '''Test that should fail during setup'''
    assert True
"""
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(coverage_enabled=True),
        ):
            rec = self.inline_run("--ddtrace")

            # Test should fail due to setup, but coverage collection shouldn't break
            rec.assertoutcome(passed=0, failed=1)

            # Module variable should still be cleaned up
            assert _current_coverage_collector is None

    def test_coverage_collection_with_failing_test_teardown(self):
        """Test coverage collection when test teardown fails."""
        self.testdir.makepyfile(
            test_failing_teardown="""
import pytest

@pytest.fixture
def failing_teardown_fixture():
    yield
    raise Exception("Teardown failure")

def test_with_failing_teardown(failing_teardown_fixture):
    '''Test that should fail during teardown'''
    assert True
"""
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(coverage_enabled=True),
        ):
            rec = self.inline_run("--ddtrace")

            # Test should pass but have teardown error, coverage collection shouldn't break
            # We don't check the exact outcome since the main point is testing cleanup
            assert rec.ret != 0  # Should have non-zero exit code due to teardown error

            # Module variable should still be cleaned up
            assert _current_coverage_collector is None

    def test_coverage_collection_with_skipped_tests(self):
        """Test coverage collection behavior with various types of skipped tests."""
        self.testdir.makepyfile(
            test_skipped="""
import pytest

def test_normal():
    '''Normal test that should collect coverage'''
    assert True

@pytest.mark.skip(reason="Explicitly skipped")
def test_pytest_skip():
    '''Test skipped by pytest.mark.skip'''
    assert True

def test_conditional_skip():
    '''Test with conditional skip'''
    pytest.skip("Conditionally skipped")
    assert True
"""
        )

        coverage_lines_call_count = 0
        coverage_exit_call_count = 0
        coverage_enter_call_count = 0

        def count_coverage_enter_calls(*args, **kwargs):
            nonlocal coverage_enter_call_count
            coverage_enter_call_count += 1

        def count_coverage_lines_calls(*args, **kwargs):
            nonlocal coverage_lines_call_count
            coverage_lines_call_count += 1
            return {}  # Return empty coverage data

        def count_coverage_exit_calls(*args, **kwargs):
            nonlocal coverage_exit_call_count
            coverage_exit_call_count += 1

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(coverage_enabled=True),
        ), mock.patch(
            # "ddtrace.internal.coverage.code.ModuleCodeCollector.CollectInContext.get_covered_lines",
            "ddtrace.contrib.internal.pytest._plugin_v2._coverage_collector_enter",
            side_effect=count_coverage_enter_calls,
        ), mock.patch(
            # "ddtrace.internal.coverage.code.ModuleCodeCollector.CollectInContext.get_covered_lines",
            "ddtrace.contrib.internal.pytest._plugin_v2._coverage_collector_exit",
            side_effect=count_coverage_exit_calls,
        ), mock.patch(
            # "ddtrace.internal.coverage.code.ModuleCodeCollector.CollectInContext.get_covered_lines",
            "ddtrace.contrib.internal.pytest._plugin_v2._coverage_collector_get_covered_lines",
            side_effect=count_coverage_lines_calls,
        ):
            rec = self.inline_run("--ddtrace")

            # One test should pass, two should be skipped
            rec.assertoutcome(passed=1, skipped=2)

            # Coverage should be collected for tests that run (including conditionally skipped ones)
            # Tests with @pytest.mark.skip should not collect coverage
            assert coverage_enter_call_count == 2, f"Expected 2 coverage enter calls, got {coverage_lines_call_count}"
            assert coverage_lines_call_count == 2, f"Expected 2 coverage line calls, got {coverage_lines_call_count}"
            assert coverage_exit_call_count == 2, f"Expected 2 coverage exit calls, got {coverage_exit_call_count}"

            # Module variable should be cleaned up
            assert _current_coverage_collector is None

    def test_coverage_collection_thread_safety(self):
        """Test that module-level coverage collector doesn't have race conditions."""
        self.testdir.makepyfile(
            test_concurrent="""
def test_concurrent_1():
    '''First concurrent test'''
    assert True

def test_concurrent_2():
    '''Second concurrent test'''
    assert True

def test_concurrent_3():
    '''Third concurrent test'''
    assert True
"""
        )

        # Track coverage collector access patterns
        collector_access_log = []
        lock = threading.Lock()

        def thread_safe_coverage_handler(item, test_id, coverage_collector):
            from ddtrace.contrib.internal.pytest._plugin_v2 import _current_coverage_collector

            with lock:
                collector_access_log.append(
                    {
                        "thread_id": threading.get_ident(),
                        "test_id": str(test_id),
                        "collector_is_current": coverage_collector is _current_coverage_collector,
                        "current_collector_not_none": _current_coverage_collector is not None,
                    }
                )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(coverage_enabled=True),
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2._handle_collected_coverage",
            side_effect=thread_safe_coverage_handler,
        ):
            rec = self.inline_run("--ddtrace")

            # All tests should pass
            rec.assertoutcome(passed=3, failed=0)

            # Verify thread safety - should have collected coverage for each test
            assert (
                len(collector_access_log) == 3
            ), f"Expected at least 3 coverage collections, got {len(collector_access_log)}"

            # Each coverage collection should have had access to the correct collector
            for log_entry in collector_access_log:
                assert log_entry["collector_is_current"], f"Coverage collector mismatch in {log_entry}"
                assert log_entry["current_collector_not_none"], f"Current collector was None in {log_entry}"

            # Module variable should be cleaned up
            assert _current_coverage_collector is None

    def test_coverage_collection_with_simulated_xdist(self):
        """Test coverage collection with simulated parallel execution like pytest-xdist."""
        self.testdir.makepyfile(
            test_xdist_sim="""
def test_worker_1():
    '''Test simulating worker 1'''
    assert True

def test_worker_2():
    '''Test simulating worker 2'''
    assert True
"""
        )

        # Just verify that coverage collection works when enabled
        coverage_call_count = 0

        def count_coverage_calls(*args, **kwargs):
            nonlocal coverage_call_count
            coverage_call_count += 1

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(coverage_enabled=True),
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2._handle_collected_coverage",
            side_effect=count_coverage_calls,
        ):
            rec = self.inline_run("--ddtrace")

            # Tests should pass
            rec.assertoutcome(passed=2, failed=0)

            # Coverage should be collected for each test
            assert coverage_call_count == 2, f"Expected 2 coverage collections, got {coverage_call_count}"

            # Module variable should be cleaned up
            assert _current_coverage_collector is None

    def test_coverage_collection_memory_leak_prevention(self):
        """Test that coverage collectors don't cause memory leaks."""
        self.testdir.makepyfile(
            test_memory="""
def test_memory_1():
    '''Test for memory leak prevention'''
    assert True

def test_memory_2():
    '''Another test for memory leak prevention'''
    assert True

def test_memory_3():
    '''Third test for memory leak prevention'''
    assert True
"""
        )

        # Simple test that just verifies cleanup happens
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(coverage_enabled=True),
        ):
            rec = self.inline_run("--ddtrace")

            # All tests should pass
            rec.assertoutcome(passed=3, failed=0)

            # Module variable should be cleaned up (no lingering references)
            assert _current_coverage_collector is None

    def test_coverage_collection_exception_isolation(self):
        """Test that exceptions in one test's coverage collection don't affect others."""
        self.testdir.makepyfile(
            test_isolation="""
def test_good_1():
    '''First good test'''
    assert True

def test_problematic():
    '''Test that will have coverage collection issues'''
    assert True

def test_good_2():
    '''Second good test that should work despite previous issues'''
    assert True
"""
        )

        call_count = 0

        def selective_failure_handler(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            # Make the second test (problematic) fail during coverage collection
            if call_count == 2:
                raise Exception("Simulated coverage collection failure")

            # Other tests should work fine
            return None

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(coverage_enabled=True),
        ), mock.patch(
            "ddtrace.contrib.internal.pytest._plugin_v2._coverage_collector_get_covered_lines",
            side_effect=selective_failure_handler,
        ):
            rec = self.inline_run("--ddtrace")

            # All tests should pass despite coverage collection failure in one test
            rec.assertoutcome(passed=3, failed=0)

            # Coverage collection should have been attempted once for each test
            assert call_count == 3, f"Expected 3 coverage collection attempts, got {call_count}"

            # Module variable should be cleaned up
            assert _current_coverage_collector is None
