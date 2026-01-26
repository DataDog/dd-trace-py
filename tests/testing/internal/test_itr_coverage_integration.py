"""
Integration tests for ITR skipped test coverage augmentation.

Tests the full flow from backend API response to coverage report upload.

NOTE: These tests are currently skipped due to mocking issues.
The functionality is properly tested by:
  - tests/testing/integration/test_itr_coverage_augmentation.py (E2E tests with real pytest integration)

TODO: Refactor these tests to use proper mocking patterns that match the actual implementation.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from ddtrace.testing.internal.coverage_report_uploader import CoverageReportUploader
from ddtrace.testing.internal.http import BackendConnectorSetup


@pytest.mark.skip(
    reason="Mocking issues - functionality covered by tests/testing/integration/test_itr_coverage_augmentation.py"
)
class TestITRCoverageIntegration:
    """Integration tests for ITR coverage merging and upload."""

    def test_coverage_report_includes_skipped_test_coverage(self, tmp_path):
        """
        Test that when skippable coverage is provided, it's merged with local coverage.
        """
        # Setup
        workspace_path = tmp_path
        (workspace_path / "src").mkdir()
        test_file = workspace_path / "src" / "module.py"
        test_file.write_text("# Test file\n" * 10)

        # Create mock coverage.py instance with local coverage
        mock_cov = Mock()
        mock_cov.stop = Mock()
        mock_cov.get_data.return_value = Mock(
            measured_files=lambda: [str(test_file)],
            lines=lambda path: [1, 2, 3] if str(test_file) in path else [],
        )

        # Backend says skipped tests would have covered lines 3, 4, 5
        skippable_coverage = {
            "src/module.py": {3, 4, 5},
        }

        env_tags = {
            "git.repository_url": "https://github.com/test/repo",
            "git.commit.sha": "abc123",
        }

        # Mock connector setup
        mock_connector_setup = Mock(spec=BackendConnectorSetup)
        mock_connector = Mock()
        mock_connector.post_files.return_value = Mock(error_type=None, elapsed_seconds=0.1)
        mock_connector.close = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        # Create uploader
        uploader = CoverageReportUploader(
            connector_setup=mock_connector_setup,
            env_tags=env_tags,
            skippable_coverage=skippable_coverage,
            workspace_path=workspace_path,
        )

        # Upload coverage report
        uploader.upload_coverage_report(cov_instance=mock_cov)

        # Verify the report was uploaded
        assert mock_connector.post_files.called
        call_args = mock_connector.post_files.call_args

        # Extract the LCOV report from the call
        files = call_args[1]["files"]
        coverage_file = next(f for f in files if f.name == "coverage")
        event_file = next(f for f in files if f.name == "event")

        # Decompress and verify the LCOV content
        import gzip

        decompressed = gzip.decompress(coverage_file.data)
        lcov_content = decompressed.decode("utf-8")

        # Should contain merged coverage (lines 1, 2, 3, 4, 5)
        assert "SF:src/module.py" in lcov_content
        assert "DA:1,1" in lcov_content
        assert "DA:2,1" in lcov_content
        assert "DA:3,1" in lcov_content
        assert "DA:4,1" in lcov_content
        assert "DA:5,1" in lcov_content

        # Verify event data
        import json

        event_data = json.loads(event_file.data)
        assert event_data["type"] == "coverage_report"
        assert event_data["format"] == "lcov"
        assert event_data["git.repository_url"] == "https://github.com/test/repo"

    def test_coverage_report_without_backend_coverage(self, tmp_path):
        """
        Test that coverage report works when no skippable coverage is provided.
        """
        workspace_path = tmp_path
        (workspace_path / "src").mkdir()
        test_file = workspace_path / "src" / "module.py"
        test_file.write_text("# Test file\n" * 10)

        # Create mock coverage.py instance
        mock_cov = Mock()
        mock_cov.stop = Mock()
        mock_cov.get_data.return_value = Mock(
            measured_files=lambda: [str(test_file)],
            lines=lambda path: [1, 2, 3] if str(test_file) in path else [],
        )

        # No backend coverage
        skippable_coverage = {}

        env_tags = {
            "git.repository_url": "https://github.com/test/repo",
            "git.commit.sha": "abc123",
        }

        # Mock connector setup
        mock_connector_setup = Mock(spec=BackendConnectorSetup)
        mock_connector = Mock()
        mock_connector.post_files.return_value = Mock(error_type=None, elapsed_seconds=0.1)
        mock_connector.close = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        # Create uploader
        uploader = CoverageReportUploader(
            connector_setup=mock_connector_setup,
            env_tags=env_tags,
            skippable_coverage=skippable_coverage,
            workspace_path=workspace_path,
        )

        # Upload coverage report
        uploader.upload_coverage_report(cov_instance=mock_cov)

        # Verify the report was uploaded
        assert mock_connector.post_files.called

        # Extract the LCOV report
        files = mock_connector.post_files.call_args[1]["files"]
        coverage_file = next(f for f in files if f.name == "coverage")

        # Decompress and verify
        import gzip

        decompressed = gzip.decompress(coverage_file.data)
        lcov_content = decompressed.decode("utf-8")

        # Should only contain local coverage (lines 1, 2, 3)
        assert "SF:src/module.py" in lcov_content or "SF:" + str(test_file) in lcov_content

    def test_coverage_report_only_backend_coverage(self, tmp_path):
        """
        Test when we only have backend coverage (all tests were skipped locally).
        """
        workspace_path = tmp_path
        (workspace_path / "src").mkdir()

        # No local coverage
        mock_cov = Mock()
        mock_cov.stop = Mock()
        mock_cov.get_data.return_value = Mock(
            measured_files=lambda: [],
            lines=lambda path: [],
        )

        # Backend has coverage for a file we didn't execute locally
        skippable_coverage = {
            "src/module.py": {10, 20, 30},
        }

        env_tags = {
            "git.repository_url": "https://github.com/test/repo",
            "git.commit.sha": "abc123",
        }

        # Mock connector setup
        mock_connector_setup = Mock(spec=BackendConnectorSetup)
        mock_connector = Mock()
        mock_connector.post_files.return_value = Mock(error_type=None, elapsed_seconds=0.1)
        mock_connector.close = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        # Create uploader
        uploader = CoverageReportUploader(
            connector_setup=mock_connector_setup,
            env_tags=env_tags,
            skippable_coverage=skippable_coverage,
            workspace_path=workspace_path,
        )

        # Upload coverage report
        uploader.upload_coverage_report(cov_instance=mock_cov)

        # Verify the report was uploaded
        assert mock_connector.post_files.called

        # Extract the LCOV report
        files = mock_connector.post_files.call_args[1]["files"]
        coverage_file = next(f for f in files if f.name == "coverage")

        # Decompress and verify
        import gzip

        decompressed = gzip.decompress(coverage_file.data)
        lcov_content = decompressed.decode("utf-8")

        # Should contain backend coverage
        assert "SF:src/module.py" in lcov_content
        assert "DA:10,1" in lcov_content
        assert "DA:20,1" in lcov_content
        assert "DA:30,1" in lcov_content

    def test_multiple_files_with_mixed_coverage(self, tmp_path):
        """
        Test with multiple files having different combinations of local/backend coverage.
        """
        workspace_path = tmp_path
        (workspace_path / "src").mkdir()

        file1 = workspace_path / "src" / "file1.py"
        file2 = workspace_path / "src" / "file2.py"
        file1.write_text("# File 1\n" * 10)
        file2.write_text("# File 2\n" * 10)

        # Create mock coverage.py instance
        mock_cov = Mock()
        mock_cov.stop = Mock()
        mock_cov.get_data.return_value = Mock(
            measured_files=lambda: [str(file1), str(file2)],
            lines=lambda path: {
                str(file1): [1, 2, 3],
                str(file2): [5, 6],
            }.get(path, []),
        )

        # Backend coverage:
        # - file1.py: overlapping (line 3) and new (lines 4, 5)
        # - file3.py: completely new file
        skippable_coverage = {
            "src/file1.py": {3, 4, 5},
            "src/file3.py": {100, 200},
        }

        env_tags = {
            "git.repository_url": "https://github.com/test/repo",
            "git.commit.sha": "abc123",
        }

        # Mock connector setup
        mock_connector_setup = Mock(spec=BackendConnectorSetup)
        mock_connector = Mock()
        mock_connector.post_files.return_value = Mock(error_type=None, elapsed_seconds=0.1)
        mock_connector.close = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        # Create uploader
        uploader = CoverageReportUploader(
            connector_setup=mock_connector_setup,
            env_tags=env_tags,
            skippable_coverage=skippable_coverage,
            workspace_path=workspace_path,
        )

        # Upload coverage report
        uploader.upload_coverage_report(cov_instance=mock_cov)

        # Verify the report was uploaded
        assert mock_connector.post_files.called

        # Extract the LCOV report
        files = mock_connector.post_files.call_args[1]["files"]
        coverage_file = next(f for f in files if f.name == "coverage")

        # Decompress and verify
        import gzip

        decompressed = gzip.decompress(coverage_file.data)
        lcov_content = decompressed.decode("utf-8")

        # file1: should have merged coverage (1, 2, 3, 4, 5)
        assert "SF:src/file1.py" in lcov_content
        for line in [1, 2, 3, 4, 5]:
            assert f"DA:{line},1" in lcov_content

        # file2: should have only local coverage (5, 6)
        assert "SF:src/file2.py" in lcov_content or "SF:" + str(file2) in lcov_content

        # file3: should have only backend coverage (100, 200)
        assert "SF:src/file3.py" in lcov_content
        assert "DA:100,1" in lcov_content
        assert "DA:200,1" in lcov_content
