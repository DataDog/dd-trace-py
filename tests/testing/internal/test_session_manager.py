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

    @pytest.mark.parametrize("env_var_value, expected_setting", [(None, True), ("true", True), ("false", False)])
    def test_session_manager_test_management_kill_switch(self, monkeypatch, env_var_value, expected_setting):
        with self.mock_settings(test_management_enabled=True):
            if env_var_value is not None:
                monkeypatch.setenv("DD_TEST_MANAGEMENT_ENABLED", env_var_value)
            session_manager = SessionManager(self.session)
            assert session_manager.settings.test_management.enabled is expected_setting

    def test_session_manager_test_management_kill_switch_skips_properties_fetch(self, monkeypatch):
        mock_client = mock_api_client_settings(test_management_enabled=True)
        monkeypatch.setenv("DD_TEST_MANAGEMENT_ENABLED", "0")

        with patch("ddtrace.testing.internal.session_manager.APIClient", return_value=mock_client):
            with setup_standard_mocks():
                SessionManager(self.session)

        mock_client.get_test_management_properties.assert_not_called()

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
            ("DD_TEST_MANAGEMENT_ENABLED", "false", "test_management.enabled", False),
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
            test_management=TestManagementSettings(enabled=True),
        )
        # Second fetch (after git upload) returns the same backend-enabled values but
        # without require_git, simulating the normal post-git-upload response.
        backend_settings_after_git = Settings(
            coverage_report_upload_enabled=True,
            itr_enabled=True,
            require_git=False,
            early_flake_detection=EarlyFlakeDetectionSettings(enabled=True),
            auto_test_retries=AutoTestRetriesSettings(enabled=True),
            test_management=TestManagementSettings(enabled=True),
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


class TestUpdatePrMergeBase:
    """Tests for SessionManager._update_pr_merge_base and its integration in upload_git_data."""

    def _make_session_manager(self, env_tags: dict) -> SessionManager:
        sm = SessionManager.__new__(SessionManager)
        sm.env_tags = env_tags
        return sm

    def test_skips_when_already_set(self) -> None:
        from ddtrace.testing.internal.git import GitTag

        sm = self._make_session_manager(
            {
                GitTag.PULL_REQUEST_BASE_BRANCH_SHA: "existing",
                GitTag.PULL_REQUEST_BASE_BRANCH_HEAD_SHA: "base",
                GitTag.COMMIT_HEAD_SHA: "head",
            }
        )
        mock_git = Mock()
        sm._update_pr_merge_base(mock_git)
        mock_git.get_merge_base.assert_not_called()

    def test_skips_when_shas_missing(self) -> None:
        sm = self._make_session_manager({})
        mock_git = Mock()
        sm._update_pr_merge_base(mock_git)
        mock_git.get_merge_base.assert_not_called()

    def test_sets_merge_base_when_both_shas_present(self) -> None:
        from ddtrace.testing.internal.git import GitTag

        sm = self._make_session_manager(
            {
                GitTag.PULL_REQUEST_BASE_BRANCH_HEAD_SHA: "base-sha",
                GitTag.COMMIT_HEAD_SHA: "head-sha",
            }
        )
        mock_git = Mock()
        mock_git.get_merge_base.return_value = "merge-base-sha"
        sm._update_pr_merge_base(mock_git)
        mock_git.get_merge_base.assert_called_once_with("base-sha", "head-sha")
        assert sm.env_tags[GitTag.PULL_REQUEST_BASE_BRANCH_SHA] == "merge-base-sha"

    def test_skips_update_when_merge_base_empty(self) -> None:
        from ddtrace.testing.internal.git import GitTag

        sm = self._make_session_manager(
            {
                GitTag.PULL_REQUEST_BASE_BRANCH_HEAD_SHA: "base-sha",
                GitTag.COMMIT_HEAD_SHA: "head-sha",
            }
        )
        mock_git = Mock()
        mock_git.get_merge_base.return_value = ""
        sm._update_pr_merge_base(mock_git)
        assert GitTag.PULL_REQUEST_BASE_BRANCH_SHA not in sm.env_tags

    def test_upload_git_data_computes_merge_base_after_unshallow(self) -> None:
        """merge-base is computed after unshallow succeeds on a shallow repo."""
        from ddtrace.testing.internal.git import GitTag

        base_sha = "base-sha"
        head_sha = "head-sha"
        expected_merge_base = "merge-base-sha"

        sm = SessionManager.__new__(SessionManager)
        sm.env_tags = {
            GitTag.PULL_REQUEST_BASE_BRANCH_HEAD_SHA: base_sha,
            GitTag.COMMIT_HEAD_SHA: head_sha,
        }
        sm.api_client = Mock()
        sm.api_client.get_known_commits.side_effect = [["commit-1"], ["commit-1"]]

        mock_git = Mock()
        mock_git.get_latest_commits.return_value = ["commit-1", "commit-2"]
        mock_git.is_shallow_repository.return_value = True
        mock_git.get_git_version.return_value = (2, 27, 0)
        mock_git.try_all_unshallow_repository_methods.return_value = True
        mock_git.get_merge_base.return_value = expected_merge_base
        mock_git.get_filtered_revisions.return_value = []
        mock_git.pack_objects.return_value = iter([])

        with (
            patch("ddtrace.testing.internal.session_manager.Git", return_value=mock_git),
            patch("ddtrace.testing.internal.session_manager.TelemetryAPI"),
        ):
            sm.upload_git_data()

        mock_git.get_merge_base.assert_called_once_with(base_sha, head_sha)
        assert sm.env_tags[GitTag.PULL_REQUEST_BASE_BRANCH_SHA] == expected_merge_base

    def test_upload_git_data_computes_merge_base_on_non_shallow_repo(self) -> None:
        """merge-base is computed even when the repo is not shallow."""
        from ddtrace.testing.internal.git import GitTag

        base_sha = "base-sha"
        head_sha = "head-sha"
        expected_merge_base = "merge-base-sha"

        sm = SessionManager.__new__(SessionManager)
        sm.env_tags = {
            GitTag.PULL_REQUEST_BASE_BRANCH_HEAD_SHA: base_sha,
            GitTag.COMMIT_HEAD_SHA: head_sha,
        }
        sm.api_client = Mock()
        sm.api_client.get_known_commits.return_value = []

        mock_git = Mock()
        mock_git.get_latest_commits.return_value = ["commit-1", "commit-2"]
        mock_git.is_shallow_repository.return_value = False
        mock_git.get_merge_base.return_value = expected_merge_base
        mock_git.get_filtered_revisions.return_value = []
        mock_git.pack_objects.return_value = iter([])

        with (
            patch("ddtrace.testing.internal.session_manager.Git", return_value=mock_git),
            patch("ddtrace.testing.internal.session_manager.TelemetryAPI"),
        ):
            sm.upload_git_data()

        mock_git.get_merge_base.assert_called_once_with(base_sha, head_sha)
        assert sm.env_tags[GitTag.PULL_REQUEST_BASE_BRANCH_SHA] == expected_merge_base

    def test_upload_git_data_computes_merge_base_when_all_commits_known(self) -> None:
        """merge-base is populated even when all commits are already in the backend (no pack upload)."""
        from ddtrace.testing.internal.git import GitTag

        base_sha = "base-sha"
        head_sha = "head-sha"
        expected_merge_base = "merge-base-sha"

        sm = SessionManager.__new__(SessionManager)
        sm.env_tags = {
            GitTag.PULL_REQUEST_BASE_BRANCH_HEAD_SHA: base_sha,
            GitTag.COMMIT_HEAD_SHA: head_sha,
        }
        sm.api_client = Mock()
        # Backend already knows all commits — no pack upload will happen.
        sm.api_client.get_known_commits.return_value = ["commit-1", "commit-2"]

        mock_git = Mock()
        mock_git.get_latest_commits.return_value = ["commit-1", "commit-2"]
        mock_git.is_shallow_repository.return_value = False
        mock_git.get_merge_base.return_value = expected_merge_base

        with (
            patch("ddtrace.testing.internal.session_manager.Git", return_value=mock_git),
            patch("ddtrace.testing.internal.session_manager.TelemetryAPI"),
        ):
            sm.upload_git_data()

        mock_git.get_merge_base.assert_called_once_with(base_sha, head_sha)
        assert sm.env_tags[GitTag.PULL_REQUEST_BASE_BRANCH_SHA] == expected_merge_base
        mock_git.pack_objects.assert_not_called()


class TestUploadSentinel:
    """Tests for the upload-completion sentinel used to deduplicate work across xdist workers."""

    def _make_sm(self, workspace_path, head_sha: t.Optional[str] = None) -> SessionManager:
        from ddtrace.testing.internal.git import GitTag

        sm = SessionManager.__new__(SessionManager)
        sm.workspace_path = workspace_path
        sm.env_tags = {GitTag.COMMIT_SHA: head_sha} if head_sha else {}
        sm.api_client = Mock()
        return sm

    def _write_sentinel(self, workspace_path, data) -> None:
        import json as _json

        sentinel_dir = workspace_path / ".git"
        sentinel_dir.mkdir(exist_ok=True)
        (sentinel_dir / "dd-trace-py.upload-done").write_text(_json.dumps(data))

    def test_no_sentinel_returns_false(self, tmp_path) -> None:
        (tmp_path / ".git").mkdir()
        sm = self._make_sm(tmp_path, head_sha="head-sha")
        assert sm._read_upload_sentinel() is None

    def test_fresh_matching_sentinel_returns_true(self, tmp_path) -> None:
        import time as _time

        sm = self._make_sm(tmp_path, head_sha="head-sha")
        self._write_sentinel(tmp_path, {"head_sha": "head-sha", "timestamp": _time.time()})
        assert sm._read_upload_sentinel() is not None

    def test_stale_sentinel_returns_false(self, tmp_path) -> None:
        import time as _time

        sm = self._make_sm(tmp_path, head_sha="head-sha")
        # 1 hour in the past — well beyond the 5-minute TTL.
        self._write_sentinel(tmp_path, {"head_sha": "head-sha", "timestamp": _time.time() - 3600})
        assert sm._read_upload_sentinel() is None

    def test_different_head_sentinel_returns_false(self, tmp_path) -> None:
        import time as _time

        sm = self._make_sm(tmp_path, head_sha="head-sha")
        self._write_sentinel(tmp_path, {"head_sha": "other-sha", "timestamp": _time.time()})
        assert sm._read_upload_sentinel() is None

    def test_malformed_sentinel_returns_false(self, tmp_path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "dd-trace-py.upload-done").write_text("not json{{")
        sm = self._make_sm(tmp_path, head_sha="head-sha")
        assert sm._read_upload_sentinel() is None

    def test_missing_head_sha_skips_check(self, tmp_path) -> None:
        import time as _time

        # No COMMIT_SHA in env_tags: even a matching sentinel must not be honored,
        # because we don't know what HEAD to validate against.
        sm = self._make_sm(tmp_path, head_sha=None)
        self._write_sentinel(tmp_path, {"head_sha": "head-sha", "timestamp": _time.time()})
        assert sm._read_upload_sentinel() is None

    def test_missing_workspace_path_attribute_returns_false(self) -> None:
        from ddtrace.testing.internal.git import GitTag

        # Some tests build SessionManager via __new__ without setting workspace_path.
        sm = SessionManager.__new__(SessionManager)
        sm.env_tags = {GitTag.COMMIT_SHA: "head-sha"}
        assert sm._read_upload_sentinel() is None

    def test_mark_writes_expected_payload(self, tmp_path) -> None:
        import json as _json

        (tmp_path / ".git").mkdir()
        sm = self._make_sm(tmp_path, head_sha="head-sha")
        sm._mark_upload_done()
        data = _json.loads((tmp_path / ".git" / "dd-trace-py.upload-done").read_text())
        assert data["head_sha"] == "head-sha"
        assert isinstance(data["timestamp"], (int, float))

    def test_mark_noop_without_head_sha(self, tmp_path) -> None:
        (tmp_path / ".git").mkdir()
        sm = self._make_sm(tmp_path, head_sha=None)
        sm._mark_upload_done()
        assert not (tmp_path / ".git" / "dd-trace-py.upload-done").exists()

    def test_upload_git_data_skips_when_sentinel_fresh(self, tmp_path) -> None:
        """When the sentinel matches, upload_git_data skips git entirely."""
        import time as _time

        from ddtrace.testing.internal.git import GitTag

        sm = self._make_sm(tmp_path, head_sha="head-sha")
        self._write_sentinel(tmp_path, {"head_sha": "head-sha", "timestamp": _time.time()})
        assert sm.env_tags[GitTag.COMMIT_SHA] == "head-sha"

        with patch("ddtrace.testing.internal.session_manager.Git") as mock_git_cls:
            sm.upload_git_data()

        mock_git_cls.assert_not_called()
        sm.api_client.get_known_commits.assert_not_called()

    def test_upload_git_data_writes_sentinel_on_all_commits_known(self, tmp_path) -> None:
        """The sentinel is written when the early-return on all-commits-known path is taken."""
        import json as _json

        sm = self._make_sm(tmp_path, head_sha="head-sha")
        (tmp_path / ".git").mkdir()
        sm.api_client.get_known_commits.return_value = ["commit-1", "commit-2"]

        mock_git = Mock()
        mock_git.get_latest_commits.return_value = ["commit-1", "commit-2"]
        mock_git.is_shallow_repository.return_value = False

        with (
            patch("ddtrace.testing.internal.session_manager.Git", return_value=mock_git),
            patch("ddtrace.testing.internal.session_manager.TelemetryAPI"),
        ):
            sm.upload_git_data()

        data = _json.loads((tmp_path / ".git" / "dd-trace-py.upload-done").read_text())
        assert data["head_sha"] == "head-sha"

    def test_upload_git_data_writes_sentinel_after_pack_upload(self, tmp_path) -> None:
        """The sentinel is written after the pack-upload path completes successfully."""
        import json as _json
        from pathlib import Path as _Path

        sm = self._make_sm(tmp_path, head_sha="head-sha")
        (tmp_path / ".git").mkdir()
        sm.api_client.get_known_commits.return_value = []
        sm.api_client.send_git_pack_file.return_value = 123  # bytes uploaded

        mock_git = Mock()
        mock_git.get_latest_commits.return_value = ["commit-1"]
        mock_git.is_shallow_repository.return_value = False
        mock_git.get_filtered_revisions.return_value = ["commit-1"]
        mock_git.pack_objects.return_value = iter([_Path("/tmp/fake.pack")])

        with (
            patch("ddtrace.testing.internal.session_manager.Git", return_value=mock_git),
            patch("ddtrace.testing.internal.session_manager.TelemetryAPI"),
        ):
            sm.upload_git_data()

        data = _json.loads((tmp_path / ".git" / "dd-trace-py.upload-done").read_text())
        assert data["head_sha"] == "head-sha"

    def test_upload_git_data_does_not_write_sentinel_when_pack_objects_yields_nothing(self, tmp_path) -> None:
        """If pack_objects fails silently (yields no files), don't trust peers with our sentinel."""
        sm = self._make_sm(tmp_path, head_sha="head-sha")
        (tmp_path / ".git").mkdir()
        sm.api_client.get_known_commits.return_value = []

        mock_git = Mock()
        mock_git.get_latest_commits.return_value = ["commit-1"]
        mock_git.is_shallow_repository.return_value = False
        mock_git.get_filtered_revisions.return_value = ["commit-1"]
        mock_git.pack_objects.return_value = iter([])  # nothing yielded

        with (
            patch("ddtrace.testing.internal.session_manager.Git", return_value=mock_git),
            patch("ddtrace.testing.internal.session_manager.TelemetryAPI"),
        ):
            sm.upload_git_data()

        assert not (tmp_path / ".git" / "dd-trace-py.upload-done").exists()

    def test_upload_git_data_does_not_write_sentinel_when_all_uploads_fail(self, tmp_path) -> None:
        """If every send_git_pack_file returns None, peers should retry rather than skip."""
        from pathlib import Path as _Path

        sm = self._make_sm(tmp_path, head_sha="head-sha")
        (tmp_path / ".git").mkdir()
        sm.api_client.get_known_commits.return_value = []
        sm.api_client.send_git_pack_file.return_value = None  # upload failure

        mock_git = Mock()
        mock_git.get_latest_commits.return_value = ["commit-1"]
        mock_git.is_shallow_repository.return_value = False
        mock_git.get_filtered_revisions.return_value = ["commit-1"]
        mock_git.pack_objects.return_value = iter([_Path("/tmp/fake.pack")])

        with (
            patch("ddtrace.testing.internal.session_manager.Git", return_value=mock_git),
            patch("ddtrace.testing.internal.session_manager.TelemetryAPI"),
        ):
            sm.upload_git_data()

        assert not (tmp_path / ".git" / "dd-trace-py.upload-done").exists()

    def test_upload_git_data_does_not_write_sentinel_on_search_commits_failure(self, tmp_path) -> None:
        """If search_commits fails, no sentinel is written so peers can retry."""
        sm = self._make_sm(tmp_path, head_sha="head-sha")
        (tmp_path / ".git").mkdir()
        # API returning None signals a failure that aborts the upload.
        sm.api_client.get_known_commits.return_value = None

        mock_git = Mock()
        mock_git.get_latest_commits.return_value = ["commit-1"]

        with (
            patch("ddtrace.testing.internal.session_manager.Git", return_value=mock_git),
            patch("ddtrace.testing.internal.session_manager.TelemetryAPI"),
        ):
            sm.upload_git_data()

        assert not (tmp_path / ".git" / "dd-trace-py.upload-done").exists()

    def test_mark_includes_merge_base_when_set(self, tmp_path) -> None:
        """The sentinel encodes merge_base_sha so peers can recover it on skip."""
        import json as _json

        from ddtrace.testing.internal.git import GitTag

        (tmp_path / ".git").mkdir()
        sm = self._make_sm(tmp_path, head_sha="head-sha")
        sm.env_tags[GitTag.PULL_REQUEST_BASE_BRANCH_SHA] = "merge-base-sha"
        sm._mark_upload_done()
        data = _json.loads((tmp_path / ".git" / "dd-trace-py.upload-done").read_text())
        assert data["merge_base_sha"] == "merge-base-sha"

    def test_mark_omits_merge_base_when_unset(self, tmp_path) -> None:
        """If env_tags has no merge-base, the sentinel doesn't include the key."""
        import json as _json

        (tmp_path / ".git").mkdir()
        sm = self._make_sm(tmp_path, head_sha="head-sha")
        sm._mark_upload_done()
        data = _json.loads((tmp_path / ".git" / "dd-trace-py.upload-done").read_text())
        assert "merge_base_sha" not in data

    def test_apply_sentinel_populates_merge_base(self, tmp_path) -> None:
        """A peer that skips uploads recovers the merge-base SHA from the sentinel."""
        from ddtrace.testing.internal.git import GitTag

        sm = self._make_sm(tmp_path, head_sha="head-sha")
        sm._apply_upload_sentinel({"head_sha": "head-sha", "merge_base_sha": "recovered-sha"})
        assert sm.env_tags[GitTag.PULL_REQUEST_BASE_BRANCH_SHA] == "recovered-sha"

    def test_apply_sentinel_does_not_overwrite_existing_merge_base(self, tmp_path) -> None:
        """User-supplied PR base SHA takes precedence over the sentinel's value."""
        from ddtrace.testing.internal.git import GitTag

        sm = self._make_sm(tmp_path, head_sha="head-sha")
        sm.env_tags[GitTag.PULL_REQUEST_BASE_BRANCH_SHA] = "existing-sha"
        sm._apply_upload_sentinel({"head_sha": "head-sha", "merge_base_sha": "sentinel-sha"})
        assert sm.env_tags[GitTag.PULL_REQUEST_BASE_BRANCH_SHA] == "existing-sha"

    def test_apply_sentinel_skips_when_merge_base_absent(self, tmp_path) -> None:
        """Sentinel without merge_base_sha leaves env_tags untouched."""
        from ddtrace.testing.internal.git import GitTag

        sm = self._make_sm(tmp_path, head_sha="head-sha")
        sm._apply_upload_sentinel({"head_sha": "head-sha"})
        assert GitTag.PULL_REQUEST_BASE_BRANCH_SHA not in sm.env_tags

    def test_upload_git_data_skip_applies_sentinel_merge_base(self, tmp_path) -> None:
        """End-to-end: a peer skipping upload recovers the merge-base from the sentinel."""
        import time as _time

        from ddtrace.testing.internal.git import GitTag

        sm = self._make_sm(tmp_path, head_sha="head-sha")
        self._write_sentinel(
            tmp_path,
            {"head_sha": "head-sha", "timestamp": _time.time(), "merge_base_sha": "peer-merge-base"},
        )

        with patch("ddtrace.testing.internal.session_manager.Git") as mock_git_cls:
            sm.upload_git_data()

        mock_git_cls.assert_not_called()
        assert sm.env_tags[GitTag.PULL_REQUEST_BASE_BRANCH_SHA] == "peer-merge-base"


class TestUploadLock:
    """Tests for the cross-process lock around upload_git_data."""

    def _make_sm(self, workspace_path, head_sha: t.Optional[str] = None) -> SessionManager:
        from ddtrace.testing.internal.git import GitTag

        sm = SessionManager.__new__(SessionManager)
        sm.workspace_path = workspace_path
        sm.env_tags = {GitTag.COMMIT_SHA: head_sha} if head_sha else {}
        sm.api_client = Mock()
        sm.api_client.get_known_commits.return_value = []
        return sm

    def test_lock_acquired_when_no_contention(self, tmp_path) -> None:
        """Single process: lock yields True and runs the upload body."""
        (tmp_path / ".git").mkdir()
        sm = self._make_sm(tmp_path, head_sha="head-sha")

        with sm._upload_lock() as acquired:
            assert acquired is True

    def test_lock_yields_true_when_path_unavailable(self) -> None:
        """Without workspace_path there is no peer coordination possible; yield True (proceed as sole uploader)."""
        sm = SessionManager.__new__(SessionManager)
        sm.env_tags = {}

        with sm._upload_lock() as acquired:
            assert acquired is True

    def test_lock_yields_true_when_fcntl_unavailable(self, tmp_path) -> None:
        """If fcntl is unavailable (e.g. Windows), no coordination is possible; yield True (sole uploader)."""
        (tmp_path / ".git").mkdir()
        sm = self._make_sm(tmp_path, head_sha="head-sha")

        with patch("ddtrace.testing.internal.session_manager._FCNTL_AVAILABLE", False):
            with sm._upload_lock() as acquired:
                assert acquired is True

    def test_lock_yields_false_on_timeout(self, tmp_path) -> None:
        """If flock keeps raising BlockingIOError past the deadline, yield False."""
        (tmp_path / ".git").mkdir()
        sm = self._make_sm(tmp_path, head_sha="head-sha")

        # Force flock to always say "would block" and drive time forward past the deadline.
        with (
            patch("ddtrace.testing.internal.session_manager.fcntl.flock", side_effect=BlockingIOError()),
            patch("ddtrace.testing.internal.session_manager.time.monotonic", side_effect=[0.0, 9999.0]),
            patch("ddtrace.testing.internal.session_manager.time.sleep"),
        ):
            with sm._upload_lock() as acquired:
                assert acquired is False

    def test_upload_git_data_runs_under_lock(self, tmp_path) -> None:
        """upload_git_data acquires the lock and proceeds to the upload work."""
        from ddtrace.testing.internal.git import GitTag

        (tmp_path / ".git").mkdir()
        sm = self._make_sm(tmp_path, head_sha="head-sha")
        sm.api_client.get_known_commits.return_value = ["c1"]

        mock_git = Mock()
        mock_git.get_latest_commits.return_value = ["c1"]
        mock_git.is_shallow_repository.return_value = False

        with (
            patch("ddtrace.testing.internal.session_manager.Git", return_value=mock_git) as mock_git_cls,
            patch("ddtrace.testing.internal.session_manager.TelemetryAPI"),
        ):
            sm.upload_git_data()

        mock_git_cls.assert_called_once()
        # Sentinel should be there now — a second call should short-circuit.
        sentinel_path = tmp_path / ".git" / "dd-trace-py.upload-done"
        assert sentinel_path.exists()
        assert sm.env_tags.get(GitTag.COMMIT_SHA) == "head-sha"

    def test_upload_git_data_rechecks_sentinel_after_lock(self, tmp_path) -> None:
        """If a peer writes the sentinel while we wait for the lock, we skip on re-check."""
        import time as _time

        from ddtrace.testing.internal.git import GitTag

        (tmp_path / ".git").mkdir()
        sm = self._make_sm(tmp_path, head_sha="head-sha")

        @contextmanager
        def fake_lock_with_peer_completion():
            # Simulate: while we waited for the lock, a peer finished and wrote the sentinel.
            (tmp_path / ".git" / "dd-trace-py.upload-done").write_text(
                _json.dumps({"head_sha": "head-sha", "timestamp": _time.time(), "merge_base_sha": "peer-mb"})
            )
            yield True

        import json as _json

        with (
            patch.object(sm, "_upload_lock", fake_lock_with_peer_completion),
            patch("ddtrace.testing.internal.session_manager.Git") as mock_git_cls,
        ):
            sm.upload_git_data()

        mock_git_cls.assert_not_called()
        assert sm.env_tags[GitTag.PULL_REQUEST_BASE_BRANCH_SHA] == "peer-mb"

    def test_upload_git_data_skips_when_lock_times_out(self, tmp_path) -> None:
        """If the lock times out (peer is alive and uploading), the upload is skipped.

        A timeout means a peer is alive and uploading; a crashed peer would have
        released the flock lock immediately. We bail rather than duplicate the work.
        """
        (tmp_path / ".git").mkdir()
        sm = self._make_sm(tmp_path, head_sha="head-sha")

        @contextmanager
        def no_lock():
            yield False  # timeout: live peer holds the lock

        with (
            patch.object(sm, "_upload_lock", no_lock),
            patch("ddtrace.testing.internal.session_manager.Git") as mock_git_cls,
        ):
            sm.upload_git_data()

        mock_git_cls.assert_not_called()

    def test_upload_git_data_applies_sentinel_on_lock_timeout(self, tmp_path) -> None:
        """When the lock times out but the peer wrote the sentinel while we waited, apply it."""
        import json as _json
        import time as _time

        from ddtrace.testing.internal.git import GitTag

        (tmp_path / ".git").mkdir()
        sm = self._make_sm(tmp_path, head_sha="head-sha")

        @contextmanager
        def no_lock():
            # Simulate: peer finished and wrote the sentinel just before our timeout fired.
            (tmp_path / ".git" / "dd-trace-py.upload-done").write_text(
                _json.dumps({"head_sha": "head-sha", "timestamp": _time.time(), "merge_base_sha": "peer-mb"})
            )
            yield False  # timeout: live peer holds the lock

        with (
            patch.object(sm, "_upload_lock", no_lock),
            patch("ddtrace.testing.internal.session_manager.Git") as mock_git_cls,
        ):
            sm.upload_git_data()

        mock_git_cls.assert_not_called()
        assert sm.env_tags[GitTag.PULL_REQUEST_BASE_BRANCH_SHA] == "peer-mb"

    def test_cleanup_removes_sentinel_and_lock(self, tmp_path) -> None:
        """cleanup_upload_artifacts() deletes both files when present."""
        (tmp_path / ".git").mkdir()
        sm = self._make_sm(tmp_path, head_sha="head-sha")
        sentinel = tmp_path / ".git" / "dd-trace-py.upload-done"
        lock = tmp_path / ".git" / "dd-trace-py.upload.lock"
        sentinel.write_text("{}")
        lock.write_text("")

        sm.cleanup_upload_artifacts()

        assert not sentinel.exists()
        assert not lock.exists()

    def test_cleanup_is_noop_when_files_absent(self, tmp_path) -> None:
        """cleanup_upload_artifacts() does not raise when files are already gone."""
        (tmp_path / ".git").mkdir()
        sm = self._make_sm(tmp_path, head_sha="head-sha")
        sm.cleanup_upload_artifacts()  # no files present — must not raise

    def test_cleanup_is_noop_without_workspace_path(self) -> None:
        """cleanup_upload_artifacts() does not raise when workspace_path is unset."""
        sm = SessionManager.__new__(SessionManager)
        sm.env_tags = {}
        sm.cleanup_upload_artifacts()  # no workspace_path — must not raise
