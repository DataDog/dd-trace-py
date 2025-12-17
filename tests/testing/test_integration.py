#!/usr/bin/env python3

import os
from unittest.mock import Mock
from unittest.mock import patch

from _pytest.monkeypatch import MonkeyPatch
from _pytest.pytester import Pytester

from ddtrace.testing.internal.session_manager import SessionManager
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from ddtrace.testing.internal.test_data import TestSession
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import network_mocks
from tests.testing.mocks import setup_standard_mocks


# Functions moved to tests.testing.mocks for centralization


class TestFeaturesWithMocking:
    """High-level feature tests using pytester with mocked dependencies."""

    def test_simple_plugin_enabled(self, pytester: Pytester) -> None:
        """Test that plugin runs when --ddtrace is used."""
        # Create a simple test file
        pytester.makepyfile(
            """
            def test_simple():
                '''A simple test.'''
                assert True
        """
        )

        # Use network mocks to prevent all real HTTP calls
        with network_mocks(), patch("ddtrace.testing.internal.session_manager.APIClient") as mock_api_client:
            mock_api_client.return_value = mock_api_client_settings()

            result = pytester.runpytest("--ddtrace", "-v")

        assert mock_api_client.call_count == 1

        # Test should pass
        assert result.ret == 0
        result.assert_outcomes(passed=1)

    def test_simple_plugin_disabled(self, pytester: Pytester) -> None:
        """Test that plugin does not run when --no-ddtrace is used."""
        # Create a simple test file
        pytester.makepyfile(
            """
            def test_simple():
                '''A simple test.'''
                assert True
        """
        )

        # Use network mocks to prevent all real HTTP calls
        with network_mocks(), patch("ddtrace.testing.internal.session_manager.APIClient") as mock_api_client:
            mock_api_client.return_value = mock_api_client_settings()

            result = pytester.runpytest("--no-ddtrace", "-v")

        assert mock_api_client.call_count == 0

        # Test should pass
        assert result.ret == 0
        result.assert_outcomes(passed=1)

    def test_simple_plugin_not_explicitly_enabled(self, pytester: Pytester) -> None:
        """Test that plugin does not run when neither --ddtrace nor --no-ddtrace is used."""
        # Create a simple test file
        pytester.makepyfile(
            """
            def test_simple():
                '''A simple test.'''
                assert True
        """
        )

        # Use network mocks to prevent all real HTTP calls
        with network_mocks(), patch("ddtrace.testing.internal.session_manager.APIClient") as mock_api_client:
            mock_api_client.return_value = mock_api_client_settings()

            result = pytester.runpytest("-p", "no:ddtrace", "-v")

        assert mock_api_client.call_count == 0

        # Test should pass
        assert result.ret == 0
        result.assert_outcomes(passed=1)

    def test_simple_plugin_disabled_overrides_enabled(self, pytester: Pytester) -> None:
        """Test that plugin does not run when both --ddtrace nor --no-ddtrace is used."""
        # Create a simple test file
        pytester.makepyfile(
            """
            def test_simple():
                '''A simple test.'''
                assert True
        """
        )

        # Use network mocks to prevent all real HTTP calls
        with network_mocks(), patch("ddtrace.testing.internal.session_manager.APIClient") as mock_api_client:
            mock_api_client.return_value = mock_api_client_settings()

            result = pytester.runpytest("--ddtrace", "--no-ddtrace", "-v")

        assert mock_api_client.call_count == 0

        # Test should pass
        assert result.ret == 0
        result.assert_outcomes(passed=1)

    def test_simple_plugin_disabled_by_kill_switch(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that plugin does not run when DD_CIVISIBILITY_ENABLED is false."""
        # Create a simple test file
        pytester.makepyfile(
            """
            def test_simple():
                '''A simple test.'''
                assert True
        """
        )

        monkeypatch.setenv("DD_CIVISIBILITY_ENABLED", "false")

        # Use network mocks to prevent all real HTTP calls
        with network_mocks(), patch("ddtrace.testing.internal.session_manager.APIClient") as mock_api_client:
            mock_api_client.return_value = mock_api_client_settings()

            result = pytester.runpytest("--ddtrace", "-v")

        assert mock_api_client.call_count == 0

        # Test should pass
        assert result.ret == 0
        result.assert_outcomes(passed=1)

    def test_simple_plugin_pytest_ini_file_enabled(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that plugin can be enabled from pytest.ini file."""
        # Create a simple test file
        pytester.makepyfile(
            """
            def test_simple():
                '''A simple test.'''
                assert True
        """
        )
        pytester.makefile(
            ".ini",
            pytest="""
            [pytest]
            ddtrace = 1
            """,
        )

        # Use network mocks to prevent all real HTTP calls
        with network_mocks(), patch("ddtrace.testing.internal.session_manager.APIClient") as mock_api_client:
            mock_api_client.return_value = mock_api_client_settings()

            result = pytester.runpytest("-v")

        assert mock_api_client.call_count == 1

        # Test should pass
        assert result.ret == 0
        result.assert_outcomes(passed=1)

    def test_simple_plugin_pytest_ini_file_disabled(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that plugin can be disabled from pytest.ini file."""
        # Create a simple test file
        pytester.makepyfile(
            """
            def test_simple():
                '''A simple test.'''
                assert True
        """
        )
        pytester.makefile(
            ".ini",
            pytest="""
            [pytest]
            ddtrace = 0
            """,
        )

        # Use network mocks to prevent all real HTTP calls
        with network_mocks(), patch("ddtrace.testing.internal.session_manager.APIClient") as mock_api_client:
            mock_api_client.return_value = mock_api_client_settings()

            result = pytester.runpytest("-v")

        assert mock_api_client.call_count == 0

        # Test should pass
        assert result.ret == 0
        result.assert_outcomes(passed=1)

    def test_retry_functionality_with_pytester(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that failing tests are retried when auto retry is enabled."""
        # Create a test file with a failing test
        pytester.makepyfile(
            test_failing="""
            def test_always_fails():
                '''A test that always fails to test retry behavior.'''
                assert False, "This test always fails"

            def test_passes():
                '''A test that passes.'''
                assert True
        """
        )

        # Use network mocks to prevent all real HTTP calls
        with network_mocks(), patch("ddtrace.testing.internal.session_manager.APIClient") as mock_api_client:
            mock_api_client.return_value = mock_api_client_settings(auto_retries_enabled=True)
            monkeypatch.setenv("DD_CIVISIBILITY_FLAKY_RETRY_COUNT", "2")
            result = pytester.runpytest("--ddtrace", "-v", "-s")

        # Check that the test failed after retries
        assert result.ret == 1  # Exit code 1 indicates test failures

        # Verify outcomes: the failing test should show as failed, passing test as passed
        result.assert_outcomes(passed=1, failed=1)

        # Check the output for retry indicators
        output = result.stdout.str()

        # Look for test execution lines - should see multiple attempts for the failing test
        assert "test_always_fails" in output
        assert "test_passes" in output

        # Verify that retries happened - should see "RETRY FAILED (Auto Test Retries)" messages
        # DEV: We configured DD_CIVISIBILITY_FLAKY_RETRY_COUNT=2
        # BUT the plugin will show 3 retry attempts (as it includes the initial attempt)
        retry_messages = output.count("RETRY FAILED (Auto Test Retries)")
        assert retry_messages == 3, f"Expected 3 retry messages, got {retry_messages}"

        # Should NOT see the final summary mentioning dd_retry
        assert "dd_retry" not in output

        # The test should ultimately fail after all retries
        assert "test_always_fails FAILED" in output
        assert "test_passes PASSED" in output

    def test_early_flake_detection_with_pytester(self, pytester: Pytester) -> None:
        """Test that EarlyFlakeDetection retries new failing tests."""
        # Create a test file with a new failing test
        pytester.makepyfile(
            test_efd="""
            def test_new_flaky():
                '''A new test that fails initially but should be retried by EFD.'''
                assert False, "This new test fails"

            def test_known_test():
                '''A known test that passes.'''
                assert True
        """
        )

        # Set up known tests - only include the "known" test
        known_suite = SuiteRef(ModuleRef("."), "test_efd.py")
        known_test_ref = TestRef(known_suite, "test_known_test")

        # Use unified mock setup with EFD enabled
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    efd_enabled=True, known_tests_enabled=True, known_tests={known_test_ref}
                ),
            ),
            setup_standard_mocks(),
        ):
            result = pytester.runpytest("--ddtrace", "-v", "-s")

        # Check that the test failed after EFD retries
        assert result.ret == 1  # Exit code 1 indicates test failures

        # Verify outcomes: the failing test should show as failed, passing test as passed
        result.assert_outcomes(passed=1, failed=1)

        # Check the output for EFD retry indicators
        output = result.stdout.str()

        # Verify that EFD retries happened - should see "RETRY FAILED (Early Flake Detection)" messages
        flaky_efd_retry_messages = output.count("test_efd.py::test_new_flaky RETRY FAILED (Early Flake Detection)")
        assert flaky_efd_retry_messages == 11, f"Expected 11 EFD retry messages, got {flaky_efd_retry_messages}"

        known_test_efd_retry_messages = output.count(
            "test_efd.py::test_known_test RETRY FAILED (Early Flake Detection)"
        )
        assert known_test_efd_retry_messages == 0, f"Expected 0 EFD retry messages, got {known_test_efd_retry_messages}"

        # Should NOT see the final summary mentioning dd_retry
        assert "dd_retry" not in output

        # The new test should ultimately fail after EFD retries
        assert "test_new_flaky FAILED" in output
        assert "test_known_test PASSED" in output

    def test_intelligent_test_runner_with_pytester(self, pytester: Pytester) -> None:
        """Test that IntelligentTestRunner skips tests marked as skippable."""
        # Create a test file with multiple tests
        pytester.makepyfile(
            test_itr="""
            def test_should_be_skipped():
                '''A test that should be skipped by ITR.'''
                assert False

            def test_should_run():
                '''A test that should run normally.'''
                assert True
        """
        )

        # Set up skippable tests - mark one test as skippable
        skippable_suite = SuiteRef(ModuleRef("."), "test_itr.py")
        skippable_test_ref = TestRef(skippable_suite, "test_should_be_skipped")

        # Use unified mock setup with ITR enabled
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(skipping_enabled=True, skippable_items={skippable_test_ref}),
            ),
            setup_standard_mocks(),
        ):
            result = pytester.runpytest("--ddtrace", "-v", "-s")

        # Check that tests completed successfully
        assert result.ret == 0  # Exit code 0 indicates success

        # Verify outcomes: one test skipped by ITR, one test passed
        result.assert_outcomes(passed=1, skipped=1)

        # Check the output for ITR skip indicators
        output = result.stdout.str()

        # Verify that ITR skipped the test with the correct reason
        assert "SKIPPED" in output
        # The reason might be truncated in the output, so check for the beginning of the message
        assert "Skipped by Datadog" in output

        # The skippable test should be marked as skipped, the other should pass
        assert "test_should_be_skipped SKIPPED" in output
        assert "test_should_run PASSED" in output


class TestPytestPluginIntegration:
    """Integration tests for the pytest plugin using pytester for better performance and reliability."""

    def test_basic_test_execution(self, pytester: Pytester) -> None:
        """Test that a basic test runs with the ddtrace.testing plugin."""
        # Create test file using pytester
        pytester.makepyfile(
            """
            def test_simple():
                '''A simple test that should pass.'''
                assert True

            def test_with_assertion():
                '''A test with a real assertion.'''
                result = 2 + 2
                assert result == 4
            """
        )

        # Set up mocks and environment
        with (
            patch("ddtrace.testing.internal.session_manager.APIClient", return_value=mock_api_client_settings()),
            setup_standard_mocks(),
        ):
            result = pytester.runpytest("--ddtrace", "-v")

        # Check that tests ran successfully
        assert result.ret == 0
        result.assert_outcomes(passed=2)

    def test_failing_test_execution(self, pytester: Pytester) -> None:
        """Test that failing tests are properly handled."""
        # Create test file using pytester
        pytester.makepyfile(
            """
            def test_failing():
                '''A test that should fail.'''
                assert False, "This test should fail"

            def test_passing():
                '''A test that should pass.'''
                assert True
            """
        )

        # Set up mocks and environment
        with (
            patch("ddtrace.testing.internal.session_manager.APIClient", return_value=mock_api_client_settings()),
            setup_standard_mocks(),
        ):
            result = pytester.runpytest("--ddtrace", "-v")

        # Check that one test failed and one passed
        assert result.ret == 1  # pytest exits with 1 when tests fail
        result.assert_outcomes(passed=1, failed=1)

    def test_plugin_loads_correctly(self, pytester: Pytester) -> None:
        """Test that the ddtrace.testing plugin loads without errors."""
        # Create test file using pytester
        pytester.makepyfile(
            """
            def test_plugin_loaded():
                '''Test to verify plugin is loaded.'''
                assert True
            """
        )

        # Set up mocks and environment
        with (
            patch("ddtrace.testing.internal.session_manager.APIClient", return_value=mock_api_client_settings()),
            setup_standard_mocks(),
        ):
            result = pytester.runpytest("--ddtrace", "--tb=short", "-v")

        # Should run without plugin loading errors
        assert result.ret == 0
        result.assert_outcomes(passed=1)

        # Should not have any error messages about plugin loading
        output = result.stdout.str()
        assert "Error setting up Test Optimization plugin" not in output

    def test_test_session_name_extraction(self, pytester: Pytester) -> None:
        """Test that the pytest session command is properly extracted."""
        # Create test file using pytester
        pytester.makepyfile(
            """
            def test_command_extraction():
                '''Test for command extraction functionality.'''
                assert True
            """
        )

        # Set up mocks and environment
        with (
            patch("ddtrace.testing.internal.session_manager.APIClient", return_value=mock_api_client_settings()),
            setup_standard_mocks(),
        ):
            # Run with specific arguments that should be captured
            result = pytester.runpytest("--ddtrace", "--tb=short", "-x", "-v")

        assert result.ret == 0
        result.assert_outcomes(passed=1)

    def test_retry_environment_variables_respected(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that retry environment variables are properly read by the plugin."""
        # Create test file using pytester
        pytester.makepyfile(
            """
            def test_env_vars():
                '''Test to verify environment variables are read.'''
                import os
                # These should be set by our test environment
                assert os.getenv("DD_CIVISIBILITY_FLAKY_RETRY_ENABLED") == "true"
                assert os.getenv("DD_CIVISIBILITY_FLAKY_RETRY_COUNT") == "2"

            def test_simple_pass():
                '''Simple passing test.'''
                assert True
            """
        )

        # Set up mocks and environment (including retry env vars)
        with (
            patch("ddtrace.testing.internal.session_manager.APIClient", return_value=mock_api_client_settings()),
            setup_standard_mocks(),
        ):
            # Set all environment variables via monkeypatch

            monkeypatch.setenv("DD_CIVISIBILITY_FLAKY_RETRY_ENABLED", "true")
            monkeypatch.setenv("DD_CIVISIBILITY_FLAKY_RETRY_COUNT", "2")
            monkeypatch.setenv("DD_CIVISIBILITY_TOTAL_FLAKY_RETRY_COUNT", "5")

            result = pytester.runpytest("--ddtrace", "-v")

        # Tests should pass
        assert result.ret == 0
        result.assert_outcomes(passed=2)


class TestIASTTerminalSummary:
    def test_ddtrace_iast_terminal_summary_enabled(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that IAST terminal summary is present when IAST is enabled."""
        pytester.makepyfile(
            test_iast="""
            def test_ok():
                assert True
        """
        )

        monkeypatch.setenv("DD_IAST_ENABLED", "true")
        monkeypatch.setenv("DD_CIVISIBILITY_AGENTLESS_ENABLED", "true")
        result = pytester.runpytest_subprocess("--ddtrace", "-v", "-s")

        output = result.stdout.str()
        assert "Datadog Code Security Report" in output

    def test_ddtrace_iast_terminal_summary_disabled(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that IAST terminal summary is NOT present when IAST is disabled."""
        pytester.makepyfile(
            test_iast="""
            def test_ok():
                assert True
        """
        )

        monkeypatch.setenv("DD_IAST_ENABLED", "false")
        monkeypatch.setenv("DD_CIVISIBILITY_AGENTLESS_ENABLED", "true")
        result = pytester.runpytest_subprocess("--ddtrace", "-v", "-s")

        output = result.stdout.str()
        assert "Datadog Code Security Report" not in output


class TestRetryHandler:
    """Test auto retry functionality using mocking for unit testing."""

    def test_retry_handler_configuration(self) -> None:
        """Test that AutoTestRetriesHandler is configured correctly with mocked settings."""
        # Use unified mock setup with auto retries enabled
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(auto_retries_enabled=True),
            ),
            setup_standard_mocks(),
            patch.dict(
                os.environ,  # Mock environment variables
                {
                    "DD_API_KEY": "test-key",
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true",
                    "DD_CIVISIBILITY_FLAKY_RETRY_ENABLED": "true",
                    "DD_CIVISIBILITY_FLAKY_RETRY_COUNT": "3",
                    "DD_CIVISIBILITY_TOTAL_FLAKY_RETRY_COUNT": "10",
                },
            ),
        ):
            # Create a test session with proper attributes
            test_session = TestSession(name="test")
            test_session.set_attributes(test_command="pytest", test_framework="pytest", test_framework_version="1.0.0")

            # Create session manager with mocked dependencies
            session_manager = SessionManager(session=test_session)
            session_manager.setup_retry_handlers()

            # Check that AutoTestRetriesHandler was added
            from ddtrace.testing.internal.retry_handlers import AutoTestRetriesHandler

            retry_handlers = session_manager.retry_handlers
            auto_retry_handler = next((h for h in retry_handlers if isinstance(h, AutoTestRetriesHandler)), None)

            assert auto_retry_handler is not None, "AutoTestRetriesHandler should be configured"
            assert auto_retry_handler.max_retries_per_test == 3
            assert auto_retry_handler.max_tests_to_retry_per_session == 10

    def test_retry_handler_logic(self) -> None:
        """Test the retry logic of AutoTestRetriesHandler."""
        from ddtrace.testing.internal.retry_handlers import AutoTestRetriesHandler
        from ddtrace.testing.internal.test_data import ModuleRef
        from ddtrace.testing.internal.test_data import SuiteRef
        from ddtrace.testing.internal.test_data import Test
        from ddtrace.testing.internal.test_data import TestRef
        from ddtrace.testing.internal.test_data import TestStatus

        # Create a mock session manager
        mock_session_manager = Mock()

        # Create AutoTestRetriesHandler
        with patch.dict(
            os.environ,
            {
                "DD_API_KEY": "foobar",
                "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true",
                "DD_CIVISIBILITY_FLAKY_RETRY_COUNT": "2",
                "DD_CIVISIBILITY_TOTAL_FLAKY_RETRY_COUNT": "5",
            },
        ):
            handler = AutoTestRetriesHandler(mock_session_manager)

        # Create a test with a mock parent (suite)
        module_ref = ModuleRef("module")
        suite_ref = SuiteRef(module_ref, "suite")
        test_ref = TestRef(suite_ref, "test_name")

        # Create a mock suite as parent
        mock_suite = Mock()
        mock_suite.ref = suite_ref

        test = Test(test_ref.name, parent=mock_suite)

        # Test should_apply
        assert handler.should_apply(test) is True

        # Create a failing test run
        test_run = test.make_test_run()
        test_run.start()
        test_run.set_status(TestStatus.FAIL)
        test_run.finish()

        # First retry should be allowed
        assert handler.should_retry(test) is True

        # Add another failed run
        test_run2 = test.make_test_run()
        test_run2.start()
        test_run2.set_status(TestStatus.FAIL)
        test_run2.finish()

        # Second retry should be allowed
        assert handler.should_retry(test) is True

        # Add third failed run
        test_run3 = test.make_test_run()
        test_run3.start()
        test_run3.set_status(TestStatus.FAIL)
        test_run3.finish()

        # Should not retry after max attempts
        assert handler.should_retry(test) is False
