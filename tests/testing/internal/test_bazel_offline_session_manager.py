"""Tests for Bazel offline mode integration in SessionManager and env_tags."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

# Path is also used in test_env_tags_non_empty_in_online_mode for get_workspace_path mock
import pytest

from ddtrace.testing.internal.cached_file_provider import CachedFileDataProvider
from ddtrace.testing.internal.http import NoOpBackendConnectorSetup
import ddtrace.testing.internal.offline_mode as offline_module
from ddtrace.testing.internal.session_manager import SessionManager
from ddtrace.testing.internal.test_data import TestSession
from tests.testing.mocks import MockDefaults
from tests.testing.mocks import mock_api_client_settings


@pytest.fixture(autouse=True)
def reset_offline_singleton(monkeypatch):
    """Reset the offline mode singleton before each test."""
    monkeypatch.setattr(offline_module, "_offline_mode", None)


def _make_manifest_dir(tmp_path: Path) -> Path:
    """Create a .testoptimization dir with a valid manifest.txt."""
    opt_dir = tmp_path / ".testoptimization"
    opt_dir.mkdir()
    (opt_dir / "manifest.txt").write_text("1")
    return opt_dir


def _make_session() -> TestSession:
    session = TestSession(name="test")
    session.set_attributes(test_command="pytest", test_framework="pytest", test_framework_version="8.0.0")
    return session


# ---------------------------------------------------------------------------
# Provider selection in SessionManager
# ---------------------------------------------------------------------------


class TestSessionManagerProviderSelection:
    def test_uses_api_client_when_manifest_not_set(self, monkeypatch, tmp_path):
        """Without DD_TEST_OPTIMIZATION_MANIFEST_FILE, APIClient is used."""
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", raising=False)

        env = MockDefaults.test_environment()

        with (
            patch("ddtrace.testing.internal.session_manager.APIClient") as mock_api_client_cls,
            patch("ddtrace.testing.internal.session_manager.get_env_tags", return_value={}),
            patch("ddtrace.testing.internal.session_manager.get_platform_tags", return_value={}),
            patch.dict(os.environ, env),
        ):
            mock_client = mock_api_client_settings()
            mock_api_client_cls.return_value = mock_client

            sm = SessionManager(session=_make_session())

        mock_api_client_cls.assert_called_once()
        assert not isinstance(sm.api_client, CachedFileDataProvider)

    def test_uses_cached_file_provider_when_manifest_set(self, monkeypatch, tmp_path):
        """With a valid manifest, CachedFileDataProvider is used and APIClient is not."""
        opt_dir = _make_manifest_dir(tmp_path)
        monkeypatch.setenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", str(opt_dir / "manifest.txt"))
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", raising=False)

        env = MockDefaults.test_environment()

        with (
            patch("ddtrace.testing.internal.session_manager.APIClient") as mock_api_client_cls,
            patch("ddtrace.testing.internal.session_manager.get_env_tags", return_value={}),
            patch("ddtrace.testing.internal.session_manager.get_platform_tags", return_value={}),
            patch.dict(os.environ, env),
        ):
            sm = SessionManager(session=_make_session())

        mock_api_client_cls.assert_not_called()
        assert isinstance(sm.api_client, CachedFileDataProvider)

    def test_connector_is_noop_in_manifest_mode(self, monkeypatch, tmp_path):
        """In manifest mode the connector setup must be NoOpBackendConnectorSetup."""
        opt_dir = _make_manifest_dir(tmp_path)
        monkeypatch.setenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", str(opt_dir / "manifest.txt"))
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", raising=False)

        env = MockDefaults.test_environment()

        with (
            patch("ddtrace.testing.internal.session_manager.get_env_tags", return_value={}),
            patch("ddtrace.testing.internal.session_manager.get_platform_tags", return_value={}),
            patch.dict(os.environ, env),
        ):
            sm = SessionManager(session=_make_session())

        assert isinstance(sm.connector_setup, NoOpBackendConnectorSetup)


# ---------------------------------------------------------------------------
# upload_git_data skipping
# ---------------------------------------------------------------------------


class TestUploadGitDataSkipping:
    def _build_sm_with_mocked_api(self, monkeypatch, env: dict) -> SessionManager:
        with (
            patch("ddtrace.testing.internal.session_manager.APIClient") as mock_api_client_cls,
            patch("ddtrace.testing.internal.session_manager.get_env_tags", return_value={}),
            patch("ddtrace.testing.internal.session_manager.get_platform_tags", return_value={}),
            patch.dict(os.environ, env),
        ):
            mock_client = mock_api_client_settings()
            mock_api_client_cls.return_value = mock_client
            sm = SessionManager(session=_make_session())
        return sm

    def test_git_upload_skipped_in_manifest_mode(self, monkeypatch, tmp_path):
        opt_dir = _make_manifest_dir(tmp_path)
        monkeypatch.setenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", str(opt_dir / "manifest.txt"))
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", raising=False)
        env = MockDefaults.test_environment()

        with (
            patch("ddtrace.testing.internal.session_manager.get_env_tags", return_value={}),
            patch("ddtrace.testing.internal.session_manager.get_platform_tags", return_value={}),
            patch.dict(os.environ, env),
        ):
            sm = SessionManager(session=_make_session())

        # upload_git_data was already called during __init__; call again explicitly
        # to confirm Git is never instantiated in manifest mode.
        with patch("ddtrace.testing.internal.session_manager.Git") as mock_git_cls:
            sm.upload_git_data()
            mock_git_cls.assert_not_called()

    def test_git_upload_skipped_in_payload_files_mode(self, monkeypatch, tmp_path):
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)
        monkeypatch.setenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", "true")
        monkeypatch.setenv("TEST_UNDECLARED_OUTPUTS_DIR", str(output_dir))
        env = MockDefaults.test_environment()

        sm = self._build_sm_with_mocked_api(monkeypatch, env)

        # Explicitly call upload_git_data; Git must not be instantiated.
        with patch("ddtrace.testing.internal.session_manager.Git") as mock_git_cls:
            sm.upload_git_data()
            mock_git_cls.assert_not_called()

    def test_git_upload_proceeds_in_online_mode(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", raising=False)
        env = MockDefaults.test_environment()

        sm = self._build_sm_with_mocked_api(monkeypatch, env)

        mock_git_instance = Mock()
        mock_git_instance.get_latest_commits.return_value = []
        mock_git_instance.get_filtered_revisions.return_value = []
        mock_git_instance.pack_objects.return_value = iter([])

        with (
            patch("ddtrace.testing.internal.session_manager.Git", return_value=mock_git_instance) as mock_git_cls,
        ):
            sm.upload_git_data()
            mock_git_cls.assert_called_once()


# ---------------------------------------------------------------------------
# env_tags stripping in payload-files mode
# ---------------------------------------------------------------------------


class TestEnvTagsStripping:
    def test_env_tags_empty_in_payload_files_mode(self, monkeypatch, tmp_path):
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        monkeypatch.setenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", "true")
        monkeypatch.setenv("TEST_UNDECLARED_OUTPUTS_DIR", str(output_dir))
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)

        # Import here so offline singleton is re-read with the env set.
        from ddtrace.testing.internal.env_tags import get_env_tags

        tags = get_env_tags()
        assert tags == {}

    def test_env_tags_non_empty_in_online_mode(self, monkeypatch):
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", raising=False)
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)

        from ddtrace.testing.internal.env_tags import get_env_tags
        from ddtrace.testing.internal.git import GitTag

        # Patch the underlying tag collectors to return known values.
        with (
            patch(
                "ddtrace.testing.internal.env_tags.ci.get_ci_tags",
                return_value={},
            ),
            patch(
                "ddtrace.testing.internal.env_tags.git.get_git_tags_from_git_command",
                return_value={GitTag.REPOSITORY_URL: "https://github.com/example/repo"},
            ),
            patch(
                "ddtrace.testing.internal.env_tags.git.get_git_tags_from_dd_variables",
                return_value={},
            ),
            patch(
                "ddtrace.testing.internal.env_tags.git.get_git_head_tags_from_git_command",
                return_value={},
            ),
            patch(
                "ddtrace.testing.internal.env_tags.get_workspace_path",
                return_value=Path("/workspace"),
            ),
        ):
            tags = get_env_tags()

        # Not empty because WORKSPACE_PATH is always populated.
        assert isinstance(tags, dict)
        assert len(tags) > 0
