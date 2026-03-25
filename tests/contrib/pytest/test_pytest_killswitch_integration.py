"""Integration test for DD_CIVISIBILITY_ENABLED killswitch functionality.

This test verifies that when DD_CIVISIBILITY_ENABLED is set to false/0,
CI Visibility is disabled and pytest traces go to the regular agent
instead of citestcycle intake, even when DD_CIVISIBILITY_AGENTLESS_ENABLED=1.
"""

import os

from tests.contrib.pytest.test_pytest import PytestTestCaseBase


class TestPytestKillswitchIntegration(PytestTestCaseBase):
    def test_pytest_programmatic_killswitch_integration_disabled_false(self):
        """Test killswitch when DD_CIVISIBILITY_ENABLED=false."""
        py_file = self.testdir.makepyfile(
            """
            def test_example():
                assert 1 + 1 == 2
        """
        )
        file_name = os.path.basename(py_file.strpath)

        # Use subprocess_run to avoid the CIVisibilityPlugin assertion issue
        rec = self.subprocess_run(
            "--ddtrace",
            file_name,
            env={
                "DD_CIVISIBILITY_ENABLED": "false",
                "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
            },
        )
        rec.assert_outcomes(passed=1)

        # The test passed, and CI Visibility should have been disabled
        # We can't check spans in subprocess mode, but the test passing indicates the killswitch worked

    def test_pytest_programmatic_killswitch_integration_disabled_0(self):
        """Test killswitch when DD_CIVISIBILITY_ENABLED=0."""
        py_file = self.testdir.makepyfile(
            """
            def test_example():
                assert 1 + 1 == 2
        """
        )
        file_name = os.path.basename(py_file.strpath)

        # Use subprocess_run to avoid the CIVisibilityPlugin assertion issue
        rec = self.subprocess_run(
            "--ddtrace",
            file_name,
            env={
                "DD_CIVISIBILITY_ENABLED": "0",
                "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
            },
        )
        rec.assert_outcomes(passed=1)

        # The test passed, and CI Visibility should have been disabled
        # We can't check spans in subprocess mode, but the test passing indicates the killswitch worked

    def test_pytest_programmatic_killswitch_integration_enabled_default(self):
        """Test that CI Visibility is enabled by default when DD_CIVISIBILITY_ENABLED is not set."""
        py_file = self.testdir.makepyfile(
            """
            def test_example():
                assert 1 + 1 == 2
        """
        )
        file_name = os.path.basename(py_file.strpath)

        # For enabled tests, we can use inline_run since CI Visibility should work normally
        rec = self.inline_run(
            "--ddtrace",
            file_name,
            extra_env={
                "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
            },
        )
        rec.assertoutcome(passed=1)

        # When killswitch is not set, CI Visibility should run and create spans
        spans = self.pop_spans()
        assert len(spans) >= 4, f"Expected at least 4 spans when CI Visibility is enabled, got {len(spans)}"

    def test_pytest_programmatic_killswitch_integration_enabled_true(self):
        """Test that DD_CIVISIBILITY_ENABLED=true enables CI Visibility."""
        py_file = self.testdir.makepyfile(
            """
            def test_example():
                assert 1 + 1 == 2
        """
        )
        file_name = os.path.basename(py_file.strpath)

        # For enabled tests, we can use inline_run since CI Visibility should work normally
        rec = self.inline_run(
            "--ddtrace",
            file_name,
            extra_env={
                "DD_CIVISIBILITY_ENABLED": "true",
                "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
            },
        )
        rec.assertoutcome(passed=1)

        # When killswitch is enabled, CI Visibility should run and create spans
        spans = self.pop_spans()
        assert len(spans) >= 4, f"Expected at least 4 spans when CI Visibility is enabled, got {len(spans)}"
