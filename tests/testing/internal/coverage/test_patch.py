"""Tests for ddtrace.contrib.internal.coverage.patch module."""

from pathlib import Path
import tempfile

import pytest

from ddtrace.contrib.internal.coverage import patch as coverage_patch


class TestCoverageIntegration:
    """Tests for coverage.py integration functions."""

    def test_start_and_stop_coverage(self) -> None:
        """Test starting and stopping coverage collection."""
        # Start coverage
        coverage_patch.start_coverage()
        assert coverage_patch.is_coverage_running()

        # Get coverage instance
        cov = coverage_patch.get_coverage_instance()
        assert cov is not None

        # Stop coverage
        coverage_patch.stop_coverage(save=False, erase=True)
        assert not coverage_patch.is_coverage_running()

    def test_generate_lcov_report_returns_percentage(self) -> None:
        """Test that generating LCOV report returns coverage percentage."""
        # Start coverage for this test
        coverage_patch.start_coverage()

        # Execute some code to get coverage data
        def sample_function():
            x = 1
            y = 2
            return x + y

        result = sample_function()
        assert result == 3

        # Stop coverage
        coverage_patch.stop_coverage()

        # Generate LCOV report
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "coverage.lcov"
            pct_covered = coverage_patch.generate_lcov_report(outfile=str(report_path))

            # Verify file was created
            assert report_path.exists()

            # Verify percentage was returned
            assert pct_covered is not None
            assert isinstance(pct_covered, float)
            assert 0.0 <= pct_covered <= 100.0

            # Verify LCOV file has valid content
            lcov_content = report_path.read_text()
            assert "SF:" in lcov_content
            assert "DA:" in lcov_content
            assert "end_of_record" in lcov_content

        # Cleanup
        coverage_patch.erase_coverage()

    def test_get_coverage_percentage(self) -> None:
        """Test retrieving stored coverage percentage."""
        coverage_patch.start_coverage()

        # Execute some code
        _ = 1 + 1

        coverage_patch.stop_coverage()

        # Generate report to store percentage
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "coverage.lcov"
            pct_from_gen = coverage_patch.generate_lcov_report(outfile=str(report_path))

        # Retrieve percentage
        pct_from_get = coverage_patch.get_coverage_percentage()

        assert pct_from_get is not None
        assert pct_from_get == pct_from_gen

        # Cleanup
        coverage_patch.erase_coverage()

    def test_coverage_instance_available_when_running(self) -> None:
        """Test that coverage instance is available when coverage is running."""
        coverage_patch.start_coverage()

        cov = coverage_patch.get_coverage_instance()
        assert cov is not None

        # Should be able to call coverage methods
        assert hasattr(cov, "start")
        assert hasattr(cov, "stop")
        assert hasattr(cov, "save")

        coverage_patch.stop_coverage(save=False, erase=True)

    def test_coverage_instance_none_when_not_running(self) -> None:
        """Test that coverage instance is None when not running."""
        # Ensure coverage is not running
        if coverage_patch.is_coverage_running():
            coverage_patch.stop_coverage(save=False, erase=True)

        cov = coverage_patch.get_coverage_instance()
        assert cov is None

    def test_generate_report_without_coverage_running(self) -> None:
        """Test generating report when coverage was not running."""
        # Ensure coverage is stopped
        if coverage_patch.is_coverage_running():
            coverage_patch.stop_coverage(save=False, erase=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "coverage.lcov"

            # Should handle gracefully
            pct = coverage_patch.generate_lcov_report(outfile=str(report_path))

            # May return None or 0 depending on implementation
            assert pct is None or pct == 0.0

    def test_multiple_start_stop_cycles(self) -> None:
        """Test multiple cycles of starting and stopping coverage."""
        # First cycle
        coverage_patch.start_coverage()
        assert coverage_patch.is_coverage_running()
        coverage_patch.stop_coverage(save=False, erase=True)
        assert not coverage_patch.is_coverage_running()

        # Second cycle
        coverage_patch.start_coverage()
        assert coverage_patch.is_coverage_running()
        coverage_patch.stop_coverage(save=False, erase=True)
        assert not coverage_patch.is_coverage_running()

    def test_stop_coverage_saves_data(self) -> None:
        """Test that stopping coverage with save=True preserves data."""
        coverage_patch.start_coverage()

        # Execute some code
        def test_func():
            return 42

        test_func()

        # Stop with save
        coverage_patch.stop_coverage(save=True, erase=False)

        # Get coverage data should not be empty
        data = coverage_patch.get_coverage_data()
        assert data is not None

        # Cleanup
        coverage_patch.erase_coverage()

    def test_lcov_report_with_no_data(self) -> None:
        """Test generating LCOV report with no coverage data."""
        # Start and immediately stop
        coverage_patch.start_coverage()
        coverage_patch.stop_coverage(save=True, erase=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "coverage.lcov"
            pct = coverage_patch.generate_lcov_report(outfile=str(report_path))

            # Should handle empty coverage gracefully
            assert pct is not None or pct == 0.0

        coverage_patch.erase_coverage()

    def test_get_coverage_data_returns_dict(self) -> None:
        """Test that get_coverage_data returns a dictionary."""
        coverage_patch.start_coverage()

        # Execute some code
        _ = 1 + 1

        coverage_patch.stop_coverage(save=True)

        data = coverage_patch.get_coverage_data()
        assert data is not None
        assert isinstance(data, dict)

        coverage_patch.erase_coverage()


class TestCoverageErrorHandling:
    """Tests for error handling in coverage integration."""

    def test_stop_coverage_when_not_started(self) -> None:
        """Test stopping coverage when it was never started."""
        # Ensure coverage is not running
        if coverage_patch.is_coverage_running():
            coverage_patch.stop_coverage(save=False, erase=True)

        # Should handle gracefully without raising
        coverage_patch.stop_coverage()
        assert not coverage_patch.is_coverage_running()

    def test_generate_report_with_invalid_path(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test generating report with invalid path."""
        coverage_patch.start_coverage()
        coverage_patch.stop_coverage()

        # Try to generate report in non-existent directory
        invalid_path = "/nonexistent/directory/coverage.lcov"

        # Should handle error gracefully and return None
        result = coverage_patch.generate_lcov_report(outfile=invalid_path)
        assert result is None

        # Verify that the error was logged
        assert any(
            "An exception occurred when running a coverage report" in record.message for record in caplog.records
        )
        assert any(record.levelname == "WARNING" for record in caplog.records)

        coverage_patch.erase_coverage()

    def test_erase_coverage_when_not_running(self) -> None:
        """Test erasing coverage data when coverage is not running."""
        # Ensure coverage is not running
        if coverage_patch.is_coverage_running():
            coverage_patch.stop_coverage(save=False, erase=True)

        # Should handle gracefully
        coverage_patch.erase_coverage()
        assert not coverage_patch.is_coverage_running()
