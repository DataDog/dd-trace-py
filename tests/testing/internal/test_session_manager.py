from contextlib import contextmanager
import os
import typing as t
from unittest.mock import Mock
from unittest.mock import call
from unittest.mock import patch

import pytest

from ddtrace.testing.internal.ci import CITag
from ddtrace.testing.internal.session_manager import SessionManager
from ddtrace.testing.internal.settings_data import AutoTestRetriesSettings
from ddtrace.testing.internal.settings_data import EarlyFlakeDetectionSettings
from ddtrace.testing.internal.settings_data import Settings
from ddtrace.testing.internal.settings_data import TestManagementSettings
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from ddtrace.testing.internal.test_data import TestSession
from tests.testing.mocks import MockDefaults
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import session_manager_mock
from tests.testing.mocks import setup_standard_mocks


class TestSessionManagerIsSkippableTest:
    """Test the new is_skippable_test method in SessionManager."""

    def setup_method(self) -> None:
        """Set up test environment and mocks."""
        self.test_env = MockDefaults.test_environment()

    def test_skipping_disabled_returns_false(self) -> None:
        """Test that is_skippable_test returns False when skipping is disabled."""
        # Create test references
        module_ref = ModuleRef("test_module")
        suite_ref = SuiteRef(module_ref, "test_suite.py")
        test_ref = TestRef(suite_ref, "test_function")

        # Create session manager with skipping disabled
        session_manager = (
            session_manager_mock()
            .with_skipping_enabled(False)
            .with_skippable_items({test_ref})  # Even if test is in skippable_items
            .build_real_with_mocks(self.test_env)
        )

        # Should return False because skipping is disabled
        assert session_manager.is_skippable_test(test_ref) is False

    def test_test_in_skippable_items_returns_true(self) -> None:
        """Test that is_skippable_test returns True when test is in skippable_items."""
        # Create test references
        module_ref = ModuleRef("test_module")
        suite_ref = SuiteRef(module_ref, "test_suite.py")
        test_ref = TestRef(suite_ref, "test_function")

        # Create session manager with test in skippable_items
        session_manager = (
            session_manager_mock()
            .with_skipping_enabled(True)
            .with_skippable_items({test_ref})
            .build_real_with_mocks(self.test_env)
        )

        # Should return True because test is in skippable_items
        assert session_manager.is_skippable_test(test_ref) is True

    def test_suite_in_skippable_items_returns_true(self) -> None:
        """Test that is_skippable_test returns True when test's suite is in skippable_items."""
        # Create test references
        module_ref = ModuleRef("test_module")
        suite_ref = SuiteRef(module_ref, "test_suite.py")
        test_ref = TestRef(suite_ref, "test_function")

        # Create session manager with suite in skippable_items (but not the individual test)
        session_manager = (
            session_manager_mock()
            .with_skipping_enabled(True)
            .with_skippable_items({suite_ref})
            .build_real_with_mocks(self.test_env)
        )

        # Should return True because test's suite is in skippable_items
        assert session_manager.is_skippable_test(test_ref) is True

    def test_both_test_and_suite_in_skippable_items_returns_true(self) -> None:
        """Test that is_skippable_test returns True when both test and suite are in skippable_items."""
        # Create test references
        module_ref = ModuleRef("test_module")
        suite_ref = SuiteRef(module_ref, "test_suite.py")
        test_ref = TestRef(suite_ref, "test_function")

        # Create session manager with both test and suite in skippable_items
        session_manager = (
            session_manager_mock()
            .with_skipping_enabled(True)
            .with_skippable_items({test_ref, suite_ref})
            .build_real_with_mocks(self.test_env)
        )

        # Should return True
        assert session_manager.is_skippable_test(test_ref) is True

    def test_neither_test_nor_suite_in_skippable_items_returns_false(self) -> None:
        """Test that is_skippable_test returns False when neither test nor suite is in skippable_items."""
        # Create test references
        module_ref = ModuleRef("test_module")
        suite_ref = SuiteRef(module_ref, "test_suite.py")
        test_ref = TestRef(suite_ref, "test_function")

        # Create different test/suite that are not the ones we're testing
        other_module_ref = ModuleRef("other_module")
        other_suite_ref = SuiteRef(other_module_ref, "other_suite.py")
        other_test_ref = TestRef(other_suite_ref, "other_function")

        # Create session manager with different test/suite in skippable_items
        session_manager = (
            session_manager_mock()
            .with_skipping_enabled(True)
            .with_skippable_items({other_test_ref, other_suite_ref})
            .build_real_with_mocks(self.test_env)
        )

        # Should return False because neither our test nor suite is in skippable_items
        assert session_manager.is_skippable_test(test_ref) is False

    def test_empty_skippable_items_returns_false(self) -> None:
        """Test that is_skippable_test returns False when skippable_items is empty."""
        # Create test references
        module_ref = ModuleRef("test_module")
        suite_ref = SuiteRef(module_ref, "test_suite.py")
        test_ref = TestRef(suite_ref, "test_function")

        # Create session manager with empty skippable_items
        session_manager = (
            session_manager_mock()
            .with_skipping_enabled(True)
            .with_skippable_items(set())
            .build_real_with_mocks(self.test_env)
        )

        # Should return False because skippable_items is empty
        assert session_manager.is_skippable_test(test_ref) is False

    def test_different_test_same_suite_name_different_module(self) -> None:
        """Test that suite matching is exact (including module)."""
        # Create test references
        module_ref1 = ModuleRef("module1")
        module_ref2 = ModuleRef("module2")
        suite_ref1 = SuiteRef(module_ref1, "test_suite.py")
        suite_ref2 = SuiteRef(module_ref2, "test_suite.py")  # Same suite name, different module
        test_ref = TestRef(suite_ref1, "test_function")

        # Create session manager with suite from different module in skippable_items
        session_manager = (
            session_manager_mock()
            .with_skipping_enabled(True)
            .with_skippable_items({suite_ref2})  # Different module, same suite name
            .build_real_with_mocks(self.test_env)
        )

        # Should return False because the suite is from a different module
        assert session_manager.is_skippable_test(test_ref) is False

    def test_multiple_tests_same_skippable_suite(self) -> None:
        """Test that multiple tests from the same skippable suite are all skippable."""
        # Create test references
        module_ref = ModuleRef("test_module")
        suite_ref = SuiteRef(module_ref, "test_suite.py")
        test_ref1 = TestRef(suite_ref, "test_function1")
        test_ref2 = TestRef(suite_ref, "test_function2")
        test_ref3 = TestRef(suite_ref, "test_function3")

        # Create session manager with suite in skippable_items
        session_manager = (
            session_manager_mock()
            .with_skipping_enabled(True)
            .with_skippable_items({suite_ref})
            .build_real_with_mocks(self.test_env)
        )

        # All tests from the same suite should be skippable
        assert session_manager.is_skippable_test(test_ref1) is True
        assert session_manager.is_skippable_test(test_ref2) is True
        assert session_manager.is_skippable_test(test_ref3) is True


class TestSessionNameTest:
    def test_session_name_explicitly_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = {"DD_TEST_SESSION_NAME": "the_name", "DD_API_KEY": "somekey", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"}
        monkeypatch.setattr(os, "environ", env)
        session_manager = session_manager_mock().with_env_tags({CITag.JOB_NAME: "the_job"}).build_real_with_mocks(env)

        expected_name = "the_name"
        assert session_manager._get_test_session_name() == expected_name
        assert session_manager.writer.metadata["*"]["test_session.name"] == expected_name

    def test_session_name_from_job_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = {"DD_API_KEY": "somekey", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"}
        monkeypatch.setattr(os, "environ", env)
        session_manager = session_manager_mock().with_env_tags({CITag.JOB_NAME: "the_job"}).build_real_with_mocks(env)

        expected_name = "the_job-pytest"
        assert session_manager._get_test_session_name() == expected_name
        assert session_manager.writer.metadata["*"]["test_session.name"] == expected_name

    def test_session_name_from_test_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = {"DD_API_KEY": "somekey", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"}
        monkeypatch.setattr(os, "environ", env)
        session_manager = session_manager_mock().with_env_tags({}).build_real_with_mocks(env)

        expected_name = "pytest"
        assert session_manager._get_test_session_name() == expected_name
        assert session_manager.writer.metadata["*"]["test_session.name"] == expected_name


class TestSessionManagerEnvVarOverrides:
    def setup_method(self) -> None:
        self.session = TestSession("pytest")
        self.session.set_attributes(
            test_command="pytest --ddtrace", test_framework="pytest", test_framework_version="9.0.0"
        )

    @contextmanager
    def mock_settings(self, **kwargs) -> t.Generator[None, None, None]:
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(**kwargs),
            ),
            setup_standard_mocks(),
        ):
            yield

    @pytest.mark.parametrize("env_var_value, expected_setting", [(None, True), ("true", True), ("false", False)])
    def test_session_manager_efd_kill_switch(self, monkeypatch, env_var_value, expected_setting):
        with self.mock_settings(efd_enabled=True):
            if env_var_value is not None:
                monkeypatch.setenv("DD_CIVISIBILITY_EARLY_FLAKE_DETECTION_ENABLED", env_var_value)
            session_manager = SessionManager(self.session)
            assert session_manager.settings.early_flake_detection.enabled is expected_setting

    @pytest.mark.parametrize("env_var_value, expected_setting", [(None, True), ("true", True), ("false", False)])
    def test_session_manager_atr_kill_switch(self, monkeypatch, env_var_value, expected_setting):
        with self.mock_settings(auto_retries_enabled=True):
            if env_var_value is not None:
                monkeypatch.setenv("DD_CIVISIBILITY_FLAKY_RETRY_ENABLED", env_var_value)
            session_manager = SessionManager(self.session)
            assert session_manager.settings.auto_test_retries.enabled is expected_setting

    @pytest.mark.parametrize("env_var_value, expected_setting", [(None, True), ("true", True), ("false", False)])
    def test_session_manager_itr_kill_switch(self, monkeypatch, env_var_value, expected_setting):
        with self.mock_settings(skipping_enabled=True):
            if env_var_value is not None:
                monkeypatch.setenv("DD_CIVISIBILITY_ITR_ENABLED", env_var_value)
            session_manager = SessionManager(self.session)
            assert session_manager.settings.itr_enabled is expected_setting

    @pytest.mark.parametrize("env_var_value, expected_setting", [(None, True), ("true", False), ("false", True)])
    def test_session_manager_skipping_kill_switch(self, monkeypatch, env_var_value, expected_setting):
        with self.mock_settings(skipping_enabled=True):
            if env_var_value is not None:
                monkeypatch.setenv("_DD_CIVISIBILITY_ITR_PREVENT_TEST_SKIPPING", env_var_value)
            session_manager = SessionManager(self.session)
            assert session_manager.settings.skipping_enabled is expected_setting

    @pytest.mark.parametrize("env_var_value, expected_setting", [(None, False), ("true", True), ("false", False)])
    def test_session_manager_force_coverage(self, monkeypatch, env_var_value, expected_setting):
        with self.mock_settings():
            if env_var_value is not None:
                monkeypatch.setenv("_DD_CIVISIBILITY_ITR_FORCE_ENABLE_COVERAGE", env_var_value)
            session_manager = SessionManager(self.session)
            assert session_manager.settings.coverage_enabled is expected_setting

    @pytest.mark.parametrize(
        "env_var, env_value, setting_attr, expected",
        [
            ("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "false", "coverage_report_upload_enabled", False),
            ("DD_CIVISIBILITY_EARLY_FLAKE_DETECTION_ENABLED", "false", "early_flake_detection.enabled", False),
            ("DD_CIVISIBILITY_FLAKY_RETRY_ENABLED", "false", "auto_test_retries.enabled", False),
            ("DD_CIVISIBILITY_ITR_ENABLED", "false", "itr_enabled", False),
        ],
    )
    def test_kill_switches_honored_after_require_git_refetch(
        self, monkeypatch, env_var, env_value, setting_attr, expected
    ):
        """Regression test: when require_git=True the session manager fetches settings twice.
        Kill switch env vars must be re-applied after the second fetch so they are not silently
        overwritten by the backend response.
        """
        backend_settings = Settings(
            coverage_report_upload_enabled=True,
            itr_enabled=True,
            require_git=True,
            early_flake_detection=EarlyFlakeDetectionSettings(enabled=True),
            auto_test_retries=AutoTestRetriesSettings(enabled=True),
            test_management=TestManagementSettings(enabled=False),
        )
        # Second fetch (after git upload) returns the same backend-enabled values but
        # without require_git, simulating the normal post-git-upload response.
        backend_settings_after_git = Settings(
            coverage_report_upload_enabled=True,
            itr_enabled=True,
            require_git=False,
            early_flake_detection=EarlyFlakeDetectionSettings(enabled=True),
            auto_test_retries=AutoTestRetriesSettings(enabled=True),
            test_management=TestManagementSettings(enabled=False),
        )

        mock_client = Mock()
        mock_client.get_settings.side_effect = [backend_settings, backend_settings_after_git]
        mock_client.get_known_tests.return_value = set()
        mock_client.get_test_management_properties.return_value = {}
        mock_client.get_known_commits.return_value = []
        mock_client.send_git_pack_file.return_value = None
        mock_client.get_skippable_tests.return_value = (set(), None)
        mock_client.close.return_value = None
        mock_client.configuration_errors = {}

        monkeypatch.setenv(env_var, env_value)

        with patch("ddtrace.testing.internal.session_manager.APIClient", return_value=mock_client):
            with setup_standard_mocks():
                session_manager = SessionManager(self.session)

        # Navigate dotted attribute path (e.g. "early_flake_detection.enabled")
        obj = session_manager.settings
        for attr in setting_attr.split("."):
            obj = getattr(obj, attr)
        assert obj is expected


class TestSessionManagerGitHandling:
    def test_upload_git_data_skips_when_git_missing(self) -> None:
        # Create a session_manager avoid running __init__ bc of side-effects
        session_manager = SessionManager.__new__(SessionManager)
        with (
            patch("ddtrace.testing.internal.session_manager.Git", side_effect=RuntimeError("`git` command not found")),
            patch("ddtrace.testing.internal.session_manager.log.warning") as mock_warning,
        ):
            session_manager.upload_git_data()

        mock_warning.assert_any_call("Error calling git binary, skipping metadata upload")

    def test_upload_git_data_records_telemetry_when_git_missing(self) -> None:
        session_manager = SessionManager.__new__(SessionManager)
        mock_telemetry_instance = Mock()

        with (
            patch("ddtrace.testing.internal.session_manager.Git", side_effect=RuntimeError("`git` command not found")),
            patch("ddtrace.testing.internal.session_manager.log"),
            patch("ddtrace.testing.internal.session_manager.TelemetryAPI") as mock_telemetry_cls,
        ):
            mock_telemetry_cls._instance = Mock()
            mock_telemetry_cls.get.return_value = mock_telemetry_instance
            session_manager.upload_git_data()

        mock_telemetry_instance.record_git_missing.assert_called_once()

    def test_upload_git_data_aborts_when_search_commits_fails(self) -> None:
        session_manager = SessionManager.__new__(SessionManager)
        session_manager.api_client = Mock()
        session_manager.api_client.get_known_commits.return_value = None

        mock_git = Mock()
        mock_git.get_latest_commits.return_value = ["commit-1", "commit-2"]

        with patch("ddtrace.testing.internal.session_manager.Git", return_value=mock_git):
            with patch("ddtrace.testing.internal.session_manager.TelemetryAPI") as mock_telemetry:
                with patch("ddtrace.testing.internal.session_manager.log") as mock_log:
                    session_manager.upload_git_data()

        session_manager.api_client.get_known_commits.assert_called_once_with(["commit-1", "commit-2"])
        mock_git.get_filtered_revisions.assert_not_called()
        mock_git.pack_objects.assert_not_called()
        session_manager.api_client.send_git_pack_file.assert_not_called()
        mock_log.warning.assert_called_with("search_commits failed, aborting git metadata upload")
        mock_telemetry.get.return_value.record_git_pack_data.assert_called_once_with(0, 0)

    def test_upload_git_data_aborts_when_search_commits_fails_after_unshallow(self) -> None:
        session_manager = SessionManager.__new__(SessionManager)
        session_manager.api_client = Mock()
        session_manager.api_client.get_known_commits.side_effect = [[], None]

        mock_git = Mock()
        mock_git.get_latest_commits.side_effect = [["commit-1"], ["commit-1", "commit-2"]]
        mock_git.is_shallow_repository.return_value = True
        mock_git.get_git_version.return_value = (2, 27, 0)
        mock_git.try_all_unshallow_repository_methods.return_value = True

        with patch("ddtrace.testing.internal.session_manager.Git", return_value=mock_git):
            with patch("ddtrace.testing.internal.session_manager.TelemetryAPI") as mock_telemetry:
                with patch("ddtrace.testing.internal.session_manager.log") as mock_log:
                    session_manager.upload_git_data()

        assert session_manager.api_client.get_known_commits.call_args_list == [
            call(["commit-1"]),
            call(["commit-1", "commit-2"]),
        ]
        mock_git.get_filtered_revisions.assert_not_called()
        mock_git.pack_objects.assert_not_called()
        session_manager.api_client.send_git_pack_file.assert_not_called()
        mock_log.warning.assert_called_with("search_commits failed after unshallow, aborting git metadata upload")
        mock_telemetry.get.return_value.record_git_pack_data.assert_called_once_with(0, 0)
