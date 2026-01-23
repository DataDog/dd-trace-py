"""Integration tests for coverage report upload feature."""

import gzip
import json
from unittest.mock import Mock
from unittest.mock import patch

from _pytest.pytester import Pytester
from pytest import MonkeyPatch

from ddtrace.testing.internal.git import GitTag
from ddtrace.testing.internal.http import BackendResult
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


class TestCoverageReportUploadIntegration:
    """Integration tests for coverage report upload."""

    def test_coverage_report_upload_enabled_via_env_var(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that coverage report is uploaded when enabled via environment variable."""
        # Create a simple test file
        pytester.makepyfile(
            test_foo="""
            def add(a, b):
                return a + b

            def test_addition():
                result = add(1, 2)
                assert result == 3
        """
        )

        # Track calls to post_files to capture the upload
        captured_uploads = []

        def mock_post_files(path, files, **kwargs):
            captured_uploads.append({"path": path, "files": files, "kwargs": kwargs})
            return BackendResult(response=Mock(status=200), response_length=100, elapsed_seconds=0.5)

        # Set up mocks
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    coverage_enabled=False,  # ITR coverage disabled
                    coverage_report_upload_enabled=True,  # Report upload enabled
                ),
            ),
            setup_standard_mocks(),
            patch(
                "ddtrace.testing.internal.http.BackendConnector.post_files",
                side_effect=mock_post_files,
            ),
        ):
            # Run pytest with ddtrace plugin
            result = pytester.inline_run("--ddtrace", "-v", "-s")

        # Verify test passed
        result.assertoutcome(passed=1)

        # Verify coverage report was uploaded
        assert len(captured_uploads) > 0, "Expected coverage report upload"

        # Find the coverage report upload (to /api/v2/cicovreprt)
        coverage_uploads = [u for u in captured_uploads if u["path"] == "/api/v2/cicovreprt"]
        assert len(coverage_uploads) == 1, f"Expected 1 coverage report upload, got {len(coverage_uploads)}"

        upload = coverage_uploads[0]
        files = upload["files"]

        # Verify we have 2 attachments (coverage + event)
        assert len(files) == 2, f"Expected 2 file attachments, got {len(files)}"

        # Find coverage and event attachments
        coverage_file = next((f for f in files if f.name == "coverage"), None)
        event_file = next((f for f in files if f.name == "event"), None)

        assert coverage_file is not None, "Expected coverage attachment"
        assert event_file is not None, "Expected event attachment"

        # Verify coverage file
        assert coverage_file.filename == "coverage.lcov.gz"
        assert coverage_file.content_type == "application/octet-stream"
        assert len(coverage_file.data) > 0, "Coverage data should not be empty"

        # Decompress and verify LCOV format
        lcov_data = gzip.decompress(coverage_file.data).decode("utf-8")
        assert "TN:" in lcov_data, "LCOV report should contain TN: (test name)"
        assert "SF:" in lcov_data, "LCOV report should contain SF: (source file)"
        assert "test_foo.py" in lcov_data, "LCOV report should contain our test file"
        assert "DA:" in lcov_data, "LCOV report should contain DA: (line coverage)"
        assert "end_of_record" in lcov_data, "LCOV report should contain end_of_record"

        # Verify event file
        assert event_file.filename == "event.json"
        assert event_file.content_type == "application/json"
        event_data = json.loads(event_file.data)

        assert event_data["type"] == "coverage_report"
        assert event_data["format"] == "lcov"

    def test_coverage_report_upload_disabled_by_default(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that coverage report is NOT uploaded when disabled."""
        pytester.makepyfile(
            test_foo="""
            def test_ok():
                assert True
        """
        )

        captured_uploads = []

        def mock_post_files(path, files, **kwargs):
            captured_uploads.append({"path": path, "files": files})
            return BackendResult(response=Mock(status=200), response_length=100, elapsed_seconds=0.5)

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    coverage_report_upload_enabled=False,  # Disabled
                ),
            ),
            setup_standard_mocks(),
            patch(
                "ddtrace.testing.internal.http.BackendConnector.post_files",
                side_effect=mock_post_files,
            ),
        ):
            result = pytester.inline_run("--ddtrace", "-v", "-s")

        result.assertoutcome(passed=1)

        # Verify NO coverage report was uploaded
        coverage_uploads = [u for u in captured_uploads if u["path"] == "/api/v2/cicovreprt"]
        assert len(coverage_uploads) == 0, "Expected no coverage report upload when disabled"

    def test_coverage_report_upload_env_var_override(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that environment variable can override backend setting."""
        pytester.makepyfile(
            test_foo="""
            def test_ok():
                assert True
        """
        )

        # Set environment variable to enable (even though backend will say disabled)
        monkeypatch.setenv("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "true")

        captured_uploads = []

        def mock_post_files(path, files, **kwargs):
            captured_uploads.append({"path": path, "files": files})
            return BackendResult(response=Mock(status=200), response_length=100, elapsed_seconds=0.5)

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    coverage_report_upload_enabled=False,  # Backend says disabled
                ),
            ),
            setup_standard_mocks(),
            patch(
                "ddtrace.testing.internal.http.BackendConnector.post_files",
                side_effect=mock_post_files,
            ),
        ):
            result = pytester.inline_run("--ddtrace", "-v", "-s")

        result.assertoutcome(passed=1)

        # Verify coverage report WAS uploaded (env var override)
        coverage_uploads = [u for u in captured_uploads if u["path"] == "/api/v2/cicovreprt"]
        assert len(coverage_uploads) == 1, "Expected coverage report upload due to env var override"

    def test_coverage_report_contains_git_tags(self, pytester: Pytester, monkeypatch: MonkeyPatch) -> None:
        """Test that coverage report event contains git and CI tags."""
        pytester.makepyfile(
            test_foo="""
            def test_ok():
                assert True
        """
        )

        captured_uploads = []

        def mock_post_files(path, files, **kwargs):
            captured_uploads.append({"path": path, "files": files})
            return BackendResult(response=Mock(status=200), response_length=100, elapsed_seconds=0.5)

        # Mock git tags
        mock_env_tags = {
            GitTag.REPOSITORY_URL: "https://github.com/DataDog/dd-trace-py.git",
            GitTag.COMMIT_SHA: "abc123def456",
            GitTag.BRANCH: "feature-branch",
            "ci.provider.name": "github",
            "ci.pipeline.id": "12345",
            "git.pull_request.number": "42",
        }

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    coverage_report_upload_enabled=True,
                ),
            ),
            setup_standard_mocks(),
            patch("ddtrace.testing.internal.session_manager.get_env_tags", return_value=mock_env_tags),
            patch(
                "ddtrace.testing.internal.http.BackendConnector.post_files",
                side_effect=mock_post_files,
            ),
        ):
            result = pytester.inline_run("--ddtrace", "-v", "-s")

        result.assertoutcome(passed=1)

        # Get the coverage upload
        coverage_uploads = [u for u in captured_uploads if u["path"] == "/api/v2/cicovreprt"]
        assert len(coverage_uploads) == 1

        # Extract event data
        files = coverage_uploads[0]["files"]
        event_file = next((f for f in files if f.name == "event"), None)
        event_data = json.loads(event_file.data)

        # Verify event structure
        assert event_data["type"] == "coverage_report"
        assert event_data["format"] == "lcov"

        # Verify git tags are present in nested structure
        assert "git" in event_data
        assert event_data["git"]["repository_url"] == "https://github.com/DataDog/dd-trace-py.git"
        assert event_data["git"]["commit_sha"] == "abc123def456"
        assert event_data["git"]["branch"] == "feature-branch"

        # Verify CI tags are present in nested structure
        assert "ci" in event_data

    def test_coverage_report_no_upload_without_coverage_data(
        self, pytester: Pytester, monkeypatch: MonkeyPatch
    ) -> None:
        """Test that no upload happens if coverage collection fails or has no data."""
        pytester.makepyfile(
            test_foo="""
            def test_ok():
                assert True
        """
        )

        captured_uploads = []

        def mock_post_files(path, files, **kwargs):
            captured_uploads.append({"path": path, "files": files})
            return BackendResult(response=Mock(status=200), response_length=100, elapsed_seconds=0.5)

        # Mock coverage.py to have no measured files
        mock_cov_instance = Mock()
        mock_cov_instance.get_data().measured_files.return_value = []

        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(
                    coverage_report_upload_enabled=True,
                ),
            ),
            setup_standard_mocks(),
            patch(
                "ddtrace.testing.internal.http.BackendConnector.post_files",
                side_effect=mock_post_files,
            ),
            patch("ddtrace.testing.internal.coverage_report.coverage.Coverage", return_value=mock_cov_instance),
        ):
            result = pytester.inline_run("--ddtrace", "-v", "-s")

        result.assertoutcome(passed=1)

        # Verify NO coverage report was uploaded (no data)
        coverage_uploads = [u for u in captured_uploads if u["path"] == "/api/v2/cicovreprt"]
        assert len(coverage_uploads) == 0, "Expected no upload when coverage has no data"
