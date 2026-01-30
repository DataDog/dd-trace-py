"""Tests for ddtrace.contrib.internal.coverage.patch module."""

from pathlib import Path
import tempfile
from unittest.mock import Mock

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

        # The result could be None or a valid percentage depending on implementation
        # The key is that it doesn't crash

        # Check if error was logged (may or may not happen depending on coverage.py behavior)
        error_logged = any(
            "An exception occurred when running a coverage report" in record.message for record in caplog.records
        )
        assert error_logged
        # We don't assert this as it depends on how coverage.py handles the invalid path

        coverage_patch.erase_coverage()

    def test_erase_coverage_when_not_running(self) -> None:
        """Test erasing coverage data when coverage is not running."""
        # Ensure coverage is not running
        if coverage_patch.is_coverage_running():
            coverage_patch.stop_coverage(save=False, erase=True)

        # Should handle gracefully
        coverage_patch.erase_coverage()
        assert not coverage_patch.is_coverage_running()


class TestCoveragePatching:
    """Tests for coverage patching functionality."""

    def test_patch_and_unpatch_coverage(self) -> None:
        """Test patching and unpatching coverage.py."""
        # Ensure coverage is not patched initially
        coverage_patch.unpatch()

        # Patch coverage
        coverage_patch.patch()

        # Should be marked as patched
        assert hasattr(coverage_patch.coverage, "_datadog_patch")
        assert coverage_patch.coverage._datadog_patch is True

        # Unpatch coverage
        coverage_patch.unpatch()

        # Should no longer be patched
        assert not hasattr(coverage_patch.coverage, "_datadog_patch") or coverage_patch.coverage._datadog_patch is False

    def test_double_patch_is_safe(self) -> None:
        """Test that patching twice doesn't cause issues."""
        coverage_patch.unpatch()

        # Patch twice
        coverage_patch.patch()
        coverage_patch.patch()

        # Should still be marked as patched once
        assert coverage_patch.coverage._datadog_patch is True

        coverage_patch.unpatch()

    def test_double_unpatch_is_safe(self) -> None:
        """Test that unpatching twice doesn't cause issues."""
        coverage_patch.patch()

        # Unpatch twice
        coverage_patch.unpatch()
        coverage_patch.unpatch()

        # Should not cause errors
        assert not hasattr(coverage_patch.coverage, "_datadog_patch") or coverage_patch.coverage._datadog_patch is False

    def test_coverage_report_wrapper_caches_percentage(self) -> None:
        """Test that the coverage report wrapper caches the percentage."""
        coverage_patch.reset_coverage_state()

        # Mock function that returns a percentage
        def mock_report_func(*args, **kwargs):
            return 85.5

        # Call the wrapper
        result = coverage_patch.coverage_report_wrapper(mock_report_func, None, (), {})

        # Should return the percentage and cache it
        assert result == 85.5
        assert coverage_patch.get_coverage_percentage() == 85.5

    def test_generate_coverage_report_with_different_formats(self) -> None:
        """Test generating coverage reports with different formats."""
        coverage_patch.start_coverage()

        # Execute some code
        _ = 1 + 1

        coverage_patch.stop_coverage()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Test text report (without outfile parameter which is not supported by coverage.report())
            text_pct = coverage_patch.generate_coverage_report("text")
            assert text_pct is not None
            assert text_pct >= 7.0

            # Test LCOV report
            lcov_path = Path(tmpdir) / "coverage.lcov"
            lcov_pct = coverage_patch.generate_coverage_report("lcov", outfile=str(lcov_path))
            assert lcov_pct is not None
            assert lcov_pct >= 7.0

            # Verify LCOV file was created
            if lcov_path.exists():
                lcov_content = lcov_path.read_text()
                assert "SF:" in lcov_content or lcov_content.strip() == ""

        coverage_patch.erase_coverage()

    def test_start_coverage_with_custom_parameters(self) -> None:
        """Test starting coverage with custom parameters."""
        if coverage_patch.is_coverage_running():
            coverage_patch.stop_coverage(erase=True)

        # Start with custom parameters
        cov = coverage_patch.start_coverage(source=["test_file.py"], omit=["*/tests/*"], auto_data=True)

        assert cov is not None
        assert coverage_patch.is_coverage_running()

        # Stop and cleanup
        coverage_patch.stop_coverage(save=False, erase=True)

    def test_coverage_instance_management(self) -> None:
        """Test coverage instance management functions."""
        # Start with clean state
        coverage_patch.reset_coverage_state()

        # Should be None initially
        assert coverage_patch.get_coverage_instance() is None

        # Start coverage
        cov = coverage_patch.start_coverage()
        assert cov is not None

        # Should be able to get the same instance
        same_cov = coverage_patch.get_coverage_instance()
        assert same_cov is not None

        # Set a different instance
        mock_cov = Mock()
        coverage_patch.set_coverage_instance(mock_cov)
        retrieved_cov = coverage_patch.get_coverage_instance()
        assert retrieved_cov is mock_cov

        # Reset state
        # Note: Coverage.current() might still return an instance even after reset
        coverage_patch.reset_coverage_state()

    def test_get_coverage_data_backwards_compatibility(self) -> None:
        """Test get_coverage_data function for backwards compatibility."""
        coverage_patch.reset_coverage_state()

        # Should return empty dict when no percentage cached
        data = coverage_patch.get_coverage_data()
        assert data == {}

        # Set a percentage and verify it's returned
        coverage_patch.start_coverage()
        coverage_patch.stop_coverage()

        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "coverage.lcov"
            pct = coverage_patch.generate_lcov_report(outfile=str(report_path))

            if pct is not None:
                data = coverage_patch.get_coverage_data()
                assert coverage_patch.PCT_COVERED_KEY in data
                assert data[coverage_patch.PCT_COVERED_KEY] == pct

        coverage_patch.erase_coverage()
