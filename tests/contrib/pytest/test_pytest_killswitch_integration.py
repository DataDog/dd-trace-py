"""Integration test for DD_CIVISIBILITY_ENABLED killswitch functionality.

This test verifies that when DD_CIVISIBILITY_ENABLED is set to false/0,
CI Visibility is disabled and pytest traces go to the regular agent
instead of citestcycle intake, even when DD_CIVISIBILITY_AGENTLESS_ENABLED=1.
"""

import os
from unittest import mock

from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
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

        rec = self.inline_run(
            "--ddtrace",
            file_name,
            extra_env={
                "DD_CIVISIBILITY_ENABLED": "false",
                "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
            },
            expect_enabled=False,
        )
        rec.assertoutcome(passed=1)
        assert len(self.pop_spans()) == 0

    def test_pytest_programmatic_killswitch_integration_disabled_0(self):
        """Test killswitch when DD_CIVISIBILITY_ENABLED=0."""
        py_file = self.testdir.makepyfile(
            """
            def test_example():
                assert 1 + 1 == 2
        """
        )
        file_name = os.path.basename(py_file.strpath)

        rec = self.inline_run(
            "--ddtrace",
            file_name,
            extra_env={
                "DD_CIVISIBILITY_ENABLED": "0",
                "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
            },
            expect_enabled=False,
        )
        rec.assertoutcome(passed=1)
        assert len(self.pop_spans()) == 0

    def test_pytest_programmatic_killswitch_integration_enabled_default(self):
        """Test that CI Visibility is enabled by default when DD_CIVISIBILITY_ENABLED is not set."""
        py_file = self.testdir.makepyfile(
            """
            def test_example():
                assert 1 + 1 == 2
        """
        )
        file_name = os.path.basename(py_file.strpath)

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ) as mock_check_enabled_features:
            rec = self.inline_run(
                "--ddtrace",
                file_name,
                extra_env={"DD_CIVISIBILITY_AGENTLESS_ENABLED": "1"},
            )

        rec.assertoutcome(passed=1)
        assert mock_check_enabled_features.called, (
            "_check_enabled_features was not called — CI Visibility may not have started"
        )
        assert len(self.pop_spans()) >= 4

    def test_pytest_programmatic_killswitch_integration_enabled_true(self):
        """Test that DD_CIVISIBILITY_ENABLED=true enables CI Visibility."""
        py_file = self.testdir.makepyfile(
            """
            def test_example():
                assert 1 + 1 == 2
        """
        )
        file_name = os.path.basename(py_file.strpath)

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ) as mock_check_enabled_features:
            rec = self.inline_run(
                "--ddtrace",
                file_name,
                extra_env={
                    "DD_CIVISIBILITY_ENABLED": "true",
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                },
            )

        rec.assertoutcome(passed=1)
        assert mock_check_enabled_features.called, (
            "_check_enabled_features was not called — CI Visibility may not have started"
        )
        assert len(self.pop_spans()) >= 4
