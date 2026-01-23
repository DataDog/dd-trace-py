"""Tests for coverage report generation."""

import gzip
import json
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.testing.internal.coverage_report import CoverageReportFormat
from ddtrace.testing.internal.coverage_report import compress_coverage_report
from ddtrace.testing.internal.coverage_report import create_coverage_report_event
from ddtrace.testing.internal.coverage_report import generate_coverage_report_lcov_from_coverage_py
from ddtrace.testing.internal.git import GitTag


class TestCoverageReportGeneration:
    """Tests for coverage report generation."""

    def test_generate_coverage_report_lcov_no_instance(self) -> None:
        """Test that None is returned when no coverage instance is provided."""
        result = generate_coverage_report_lcov_from_coverage_py(None, Path("/tmp"), {})
        assert result is None

    def test_generate_coverage_report_lcov_no_data(self) -> None:
        """Test that None is returned when there's no coverage data."""
        mock_cov = Mock()
        mock_cov_data = Mock()
        mock_cov.stop = Mock()
        mock_cov.get_data.return_value = mock_cov_data
        mock_cov_data.measured_files.return_value = []

        result = generate_coverage_report_lcov_from_coverage_py(mock_cov, Path("/tmp"), {})

        assert result is None
        mock_cov.stop.assert_called_once()

    @pytest.mark.skip(
        reason="Mocking issues - functionality covered by tests/testing/integration/test_itr_coverage_augmentation.py"
    )
    def test_generate_coverage_report_lcov_success(self) -> None:
        """Test successful LCOV report generation."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_path = Path(tmpdir)
            test_file = workspace_path / "test.py"
            test_file.write_text("# test file")

            mock_cov = Mock()
            mock_cov_data = Mock()
            mock_cov.stop = Mock()
            mock_cov.get_data.return_value = mock_cov_data
            mock_cov_data.measured_files.return_value = [str(test_file)]
            mock_cov_data.lines.return_value = [1, 2, 3]

            def mock_lcov_report(outfile):
                # Simulate coverage writing the LCOV file
                with open(outfile, "w") as f:
                    f.write("TN:\nSF:test.py\nDA:1,1\nend_of_record\n")

            with patch("coverage.CoverageData"), patch("coverage.Coverage") as mock_cov_class:
                mock_report_cov = mock_cov_class.return_value
                mock_report_cov.lcov_report.side_effect = mock_lcov_report

                result = generate_coverage_report_lcov_from_coverage_py(mock_cov, workspace_path, {})

            assert result is not None
            assert b"TN:" in result
            assert b"SF:test.py" in result
            mock_cov.stop.assert_called_once()

    def test_generate_coverage_report_lcov_error(self) -> None:
        """Test that None is returned when an error occurs during report generation."""
        mock_cov = Mock()
        mock_cov.stop.side_effect = Exception("Test error")

        result = generate_coverage_report_lcov_from_coverage_py(mock_cov, Path("/tmp"), {})

        assert result is None

    def test_generate_coverage_report_lcov_with_missing_lines(self) -> None:
        """Test that LCOV format correctly reports both covered and uncovered executable lines."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_path = Path(tmpdir)

            mock_cov = Mock()
            mock_cov_data = Mock()
            mock_cov.stop = Mock()
            mock_cov.get_data.return_value = mock_cov_data

            # File has lines 1, 2, 3, 4, 5 as executable
            # Lines 1, 3, 5 are covered
            # Lines 2, 4 are missing (uncovered but executable)
            test_file_abs = str(workspace_path / "test.py")
            mock_cov_data.measured_files.return_value = [test_file_abs]
            mock_cov_data.lines.return_value = [1, 3, 5]  # covered lines
            mock_cov_data.missing.return_value = [2, 4]  # missing lines

            result = generate_coverage_report_lcov_from_coverage_py(mock_cov, workspace_path, {})

            assert result is not None
            lcov_content = result.decode("utf-8")

            # Should contain test file
            assert "SF:test.py" in lcov_content

            # Should report all executable lines with correct execution counts
            assert "DA:1,1" in lcov_content  # covered
            assert "DA:2,0" in lcov_content  # uncovered but executable
            assert "DA:3,1" in lcov_content  # covered
            assert "DA:4,0" in lcov_content  # uncovered but executable
            assert "DA:5,1" in lcov_content  # covered

            # Should have correct summary statistics
            assert "LF:5" in lcov_content  # 5 lines found (executable)
            assert "LH:3" in lcov_content  # 3 lines hit (covered)

    def test_generate_coverage_report_lcov_with_backend_coverage_merge(self) -> None:
        """Test that LCOV format correctly merges local and backend coverage."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_path = Path(tmpdir)

            mock_cov = Mock()
            mock_cov_data = Mock()
            mock_cov.stop = Mock()
            mock_cov.get_data.return_value = mock_cov_data

            # Local coverage: lines 1, 3 covered, line 2 missing
            test_file_abs = str(workspace_path / "test.py")
            mock_cov_data.measured_files.return_value = [test_file_abs]
            mock_cov_data.lines.return_value = [1, 3]  # local covered lines
            mock_cov_data.missing.return_value = [2]  # local missing lines

            # Backend coverage adds line 2 (was missing locally) and line 4 (new)
            backend_coverage = {"test.py": {2, 4}}

            result = generate_coverage_report_lcov_from_coverage_py(mock_cov, workspace_path, backend_coverage)

            assert result is not None
            lcov_content = result.decode("utf-8")

            # Should contain test file
            assert "SF:test.py" in lcov_content

            # Should report merged coverage
            assert "DA:1,1" in lcov_content  # local coverage
            assert "DA:2,1" in lcov_content  # merged (was missing locally, covered by backend)
            assert "DA:3,1" in lcov_content  # local coverage
            assert "DA:4,1" in lcov_content  # backend-only coverage

            # Should have correct summary statistics
            assert "LF:4" in lcov_content  # 4 lines found (executable)
            assert "LH:4" in lcov_content  # 4 lines hit (all covered after merge)

    def test_generate_coverage_report_lcov_backend_only_file(self) -> None:
        """Test LCOV format for files that only exist in backend coverage."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_path = Path(tmpdir)

            mock_cov = Mock()
            mock_cov_data = Mock()
            mock_cov.stop = Mock()
            mock_cov.get_data.return_value = mock_cov_data
            mock_cov_data.measured_files.return_value = []  # No local files

            # Backend-only coverage
            backend_coverage = {"backend_file.py": {1, 3, 5}}

            result = generate_coverage_report_lcov_from_coverage_py(mock_cov, workspace_path, backend_coverage)

            assert result is not None
            lcov_content = result.decode("utf-8")

            # Should contain backend file
            assert "SF:backend_file.py" in lcov_content

            # For backend-only files, assume all covered lines are executable
            assert "DA:1,1" in lcov_content  # backend coverage
            assert "DA:3,1" in lcov_content  # backend coverage
            assert "DA:5,1" in lcov_content  # backend coverage

            # Should have correct summary statistics
            assert "LF:3" in lcov_content  # 3 lines found (executable)
            assert "LH:3" in lcov_content  # 3 lines hit (all backend lines)


class TestCoverageReportCompression:
    """Tests for coverage report compression."""

    def test_compress_coverage_report(self) -> None:
        """Test that coverage report data is properly compressed."""
        # Use a larger data sample to ensure gzip compression is effective
        report_data = b"TN:\n" + b"SF:test.py\nDA:1,1\nDA:2,1\nDA:3,0\n" * 100 + b"end_of_record\n"

        compressed = compress_coverage_report(report_data)

        # Verify it's compressed (should be smaller for larger data)
        assert compressed != report_data
        assert len(compressed) < len(report_data)

        # Verify we can decompress it
        decompressed = gzip.decompress(compressed)
        assert decompressed == report_data


class TestCoverageReportEvent:
    """Tests for coverage report event creation."""

    def test_create_coverage_report_event_minimal(self) -> None:
        """Test event creation with minimal tags."""
        env_tags = {
            GitTag.REPOSITORY_URL: "https://github.com/DataDog/dd-trace-py.git",
            GitTag.COMMIT_SHA: "abc123",
            GitTag.BRANCH: "main",
        }

        event_data = create_coverage_report_event(env_tags, CoverageReportFormat.LCOV)

        event = json.loads(event_data)
        assert event["type"] == "coverage_report"
        assert event["format"] == "lcov"
        assert event[GitTag.REPOSITORY_URL] == "https://github.com/DataDog/dd-trace-py.git"
        assert event[GitTag.COMMIT_SHA] == "abc123"
        assert event[GitTag.BRANCH] == "main"

    def test_create_coverage_report_event_with_ci_tags(self) -> None:
        """Test event creation with CI tags."""
        from ddtrace.testing.internal.ci import CITag

        env_tags = {
            GitTag.REPOSITORY_URL: "https://github.com/DataDog/dd-trace-py.git",
            GitTag.COMMIT_SHA: "abc123",
            GitTag.BRANCH: "main",
            CITag.PROVIDER_NAME: "github",
            CITag.PIPELINE_ID: "12345",
            CITag.JOB_NAME: "test-job",
        }

        event_data = create_coverage_report_event(env_tags, CoverageReportFormat.LCOV)

        event = json.loads(event_data)
        assert event["type"] == "coverage_report"
        assert event["format"] == "lcov"
        assert event[CITag.PROVIDER_NAME] == "github"
        assert event[CITag.PIPELINE_ID] == "12345"
        assert event[CITag.JOB_NAME] == "test-job"

    def test_create_coverage_report_event_with_pr_number(self) -> None:
        """Test event creation with PR number."""
        env_tags = {
            GitTag.REPOSITORY_URL: "https://github.com/DataDog/dd-trace-py.git",
            GitTag.COMMIT_SHA: "abc123",
            GitTag.BRANCH: "feature-branch",
            "git.pull_request.number": "42",
        }

        event_data = create_coverage_report_event(env_tags, CoverageReportFormat.LCOV)

        event = json.loads(event_data)
        assert event["pr.number"] == "42"

    def test_create_coverage_report_event_ignores_missing_tags(self) -> None:
        """Test that missing tags are not included in the event."""
        env_tags = {
            GitTag.REPOSITORY_URL: "https://github.com/DataDog/dd-trace-py.git",
        }

        event_data = create_coverage_report_event(env_tags, CoverageReportFormat.LCOV)

        event = json.loads(event_data)
        assert event["type"] == "coverage_report"
        assert event["format"] == "lcov"
        assert GitTag.COMMIT_SHA not in event
        assert GitTag.BRANCH not in event
