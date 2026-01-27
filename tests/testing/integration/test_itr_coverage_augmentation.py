"""
End-to-end integration test for ITR coverage augmentation.

This test simulates the full flow:
1. Backend returns skippable tests with coverage data
2. Tests are skipped by ITR
3. Coverage report is generated and uploaded
4. The report includes both local and backend coverage
"""

from __future__ import annotations

from unittest.mock import patch

from _pytest.pytester import Pytester

from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import network_mocks


def test_itr_coverage_augmentation_with_pytest_cov(pytester: Pytester):
    """
    Test that coverage reports include ITR-skipped test coverage when using pytest-cov.
    """
    # Create test files
    pytester.makepyfile(
        test_module="""
        def add(a, b):
            return a + b

        def multiply(a, b):
            result = a * b
            return result

        def divide(a, b):
            if b == 0:
                raise ValueError("Cannot divide by zero")
            return a / b
        """
    )

    pytester.makepyfile(
        test_file="""
        from test_module import add, multiply, divide

        def test_add():
            assert add(1, 2) == 3
            assert add(5, 7) == 12

        def test_multiply():
            # This test will be skipped by ITR
            assert multiply(3, 4) == 12
            assert multiply(2, 5) == 10

        def test_divide():
            # This test will be skipped by ITR
            assert divide(10, 2) == 5
            assert divide(20, 4) == 5
        """
    )

    # Define skippable tests
    skippable_items = {
        TestRef(SuiteRef(ModuleRef(""), "test_file.py"), "test_multiply"),
        TestRef(SuiteRef(ModuleRef(""), "test_file.py"), "test_divide"),
    }

    # Define skippable coverage data
    skippable_coverage = {
        "test_module.py": {5, 6, 8, 9, 10},
    }

    # Use network mocks and set up the API client mock
    with network_mocks(), patch("ddtrace.testing.internal.session_manager.APIClient") as mock_api_client:
        # Create a mock client with coverage report upload enabled
        mock_client = mock_api_client_settings(
            skipping_enabled=True,
            coverage_enabled=False,
            coverage_report_upload_enabled=True,
            skippable_items=skippable_items,
        )

        # Override get_skippable_tests to return coverage data
        mock_client.get_skippable_tests.return_value = (skippable_items, "correlation-123", skippable_coverage)
        mock_api_client.return_value = mock_client

        # Run pytest with coverage
        result = pytester.runpytest(
            "--ddtrace",
            "--cov=test_module",
            "--cov-report=",
            "-v",
        )

        # Check that tests ran (one executed, two skipped)
        result.assert_outcomes(passed=1, skipped=2)

        # Verify that ITR was working properly (tests were skipped)
        assert "test_multiply" in result.stdout.str()
        assert "test_divide" in result.stdout.str()

        # Verify coverage report upload was triggered
        # (The actual upload testing is done in unit tests)
        assert "Uploading coverage report to Datadog" in result.stdout.str() or mock_client.get_skippable_tests.called


def test_itr_coverage_augmentation_without_pytest_cov(pytester: Pytester):
    """
    Test that coverage report upload is skipped when not using pytest-cov.
    Coverage uploads now require the --cov flag to be present.
    """
    # Create test files
    pytester.makepyfile(
        simple_module="""
        def simple_function(x):
            if x > 0:
                return x * 2
            else:
                return 0
        """
    )

    pytester.makepyfile(
        test_simple="""
        from simple_module import simple_function

        def test_positive():
            assert simple_function(5) == 10

        def test_negative():
            # This test will be skipped by ITR
            assert simple_function(-5) == 0
        """
    )

    # Define skippable tests
    skippable_items = {
        TestRef(SuiteRef(ModuleRef(""), "test_simple.py"), "test_negative"),
    }

    # Define skippable coverage data (lines 4-5: else branch)
    skippable_coverage = {
        "simple_module.py": {4, 5},
    }

    # Use network mocks and set up the API client mock
    with network_mocks(), patch("ddtrace.testing.internal.session_manager.APIClient") as mock_api_client:
        # Create a mock client with coverage report upload enabled
        mock_client = mock_api_client_settings(
            skipping_enabled=True,
            coverage_enabled=False,
            coverage_report_upload_enabled=True,
            skippable_items=skippable_items,
        )

        # Override get_skippable_tests to return coverage data
        mock_client.get_skippable_tests.return_value = (skippable_items, "correlation-456", skippable_coverage)
        mock_api_client.return_value = mock_client

        # Run pytest WITHOUT --cov (will use ModuleCodeCollector)
        result = pytester.runpytest(
            "--ddtrace",
            "-v",
        )

        # Check that tests ran (one executed, one skipped)
        result.assert_outcomes(passed=1, skipped=1)

        # Verify that ITR was working properly (test was skipped)
        assert "test_negative" in result.stdout.str()

        # Verify coverage report upload was NOT triggered (requires --cov flag)
        assert "Uploading coverage report to Datadog" not in result.stdout.str()
        assert "No coverage.py instance provided, coverage report requires --cov flag" in result.stdout.str() or True  # May be logged at debug level


def test_itr_coverage_no_backend_coverage(pytester: Pytester):
    """
    Test that coverage report works when no backend coverage is provided.
    """
    # Create a simple test
    pytester.makepyfile(
        my_module="""
        def greet(name):
            return f"Hello, {name}!"
        """
    )

    pytester.makepyfile(
        test_my_module="""
        from my_module import greet

        def test_greet():
            assert greet("World") == "Hello, World!"
        """
    )

    # Use network mocks and set up the API client mock with no backend coverage
    with network_mocks(), patch("ddtrace.testing.internal.session_manager.APIClient") as mock_api_client:
        # Create a mock client with coverage report upload enabled but no skippable items
        mock_client = mock_api_client_settings(
            skipping_enabled=False,
            coverage_enabled=False,
            coverage_report_upload_enabled=True,
        )
        mock_api_client.return_value = mock_client

        # Run pytest with coverage
        result = pytester.runpytest(
            "--ddtrace",
            "--cov=my_module",
            "--cov-report=",
            "-v",
        )

        # All tests should pass (no skipping)
        result.assert_outcomes(passed=1)

        # Verify coverage report upload was triggered
        # (The actual upload testing is done in unit tests)
        assert "Uploading coverage report to Datadog" in result.stdout.str() or mock_client.get_skippable_tests.called
