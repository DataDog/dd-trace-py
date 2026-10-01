"""Tests for ddtrace.contrib.internal.coverage.patch module."""

from io import StringIO
from pathlib import Path
import runpy
import tempfile
from unittest.mock import Mock
from unittest.mock import patch

from coverage import Coverage
from coverage.exceptions import NoDataError
import pytest

from ddtrace.contrib.internal.coverage import patch as coverage_patch


@pytest.fixture
def measured_coverage(tmp_path: Path) -> tuple[Coverage, Path]:
    """Return isolated coverage data for a fully executed source file."""
    source_path = tmp_path / "measured.py"
    source_path.write_text("value = 1\nvalue += 1\n")

    cov = Coverage(config_file=False, data_file=None, include=[str(source_path)])
    cov.start()
    try:
        runpy.run_path(str(source_path))
    finally:
        cov.stop()

    return cov, source_path


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

    def test_stop_coverage_does_not_modify_external_instance(self) -> None:
        """Coverage sessions started by tools such as pytest-cov remain externally managed."""
        external_cov = Mock()

        with patch.object(coverage_patch.Coverage, "current", return_value=external_cov):
            assert coverage_patch.start_coverage() is external_cov

        assert coverage_patch.stop_coverage(save=True, erase=True) is external_cov
        external_cov.stop.assert_not_called()
        external_cov.save.assert_not_called()
        external_cov.erase.assert_not_called()
        coverage_patch.reset_coverage_state()

    def test_generate_lcov_report_returns_percentage(
        self, measured_coverage: tuple[Coverage, Path], tmp_path: Path
    ) -> None:
        """Test that generating LCOV report returns coverage percentage."""
        cov, source_path = measured_coverage
        report_path = tmp_path / "coverage.lcov"

        pct_covered = coverage_patch.generate_lcov_report(cov=cov, outfile=str(report_path))

        assert pct_covered == 100.0
        assert report_path.exists()
        lcov_content = report_path.read_text()
        assert f"SF:{source_path}" in lcov_content
        assert "DA:1,1" in lcov_content
        assert "DA:2,1" in lcov_content
        assert "end_of_record" in lcov_content

    def test_get_coverage_percentage(self, measured_coverage: tuple[Coverage, Path], tmp_path: Path) -> None:
        """Test retrieving stored coverage percentage."""
        cov, _ = measured_coverage
        report_path = tmp_path / "coverage.lcov"

        pct_from_gen = coverage_patch.generate_lcov_report(cov=cov, outfile=str(report_path))

        assert pct_from_gen == 100.0
        assert coverage_patch.get_coverage_percentage() == pct_from_gen

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

    def test_lcov_report_with_no_data(self, tmp_path: Path) -> None:
        """Test generating LCOV report with no coverage data."""
        cov = coverage_patch.start_coverage(config_file=False, data_file=None, omit=["*"])
        assert cov is not None
        coverage_patch.stop_coverage(save=False)
        report_path = tmp_path / "coverage.lcov"

        with pytest.raises(NoDataError):
            cov.lcov_report(outfile=str(report_path))

        assert coverage_patch.generate_lcov_report(cov=cov, outfile=str(report_path)) is None

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

    def test_generate_report_with_invalid_path(self) -> None:
        """Test that an output-path error is handled without masking the failure."""
        invalid_path = "/nonexistent/directory/coverage.lcov"
        error = OSError("unable to write coverage report")
        cov = Mock()
        cov.lcov_report.side_effect = error

        with patch.object(coverage_patch.log, "warning") as log_warning:
            result = coverage_patch.generate_lcov_report(cov=cov, outfile=invalid_path)

        assert result is None
        cov.lcov_report.assert_called_once_with(outfile=invalid_path)
        log_warning.assert_called_once_with("An exception occurred when running a coverage report: %s", error)

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
        assert coverage_patch.coverage._datadog_patch is True  # type:ignore[attr-defined]

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

    def test_generate_coverage_report_with_different_formats(
        self, measured_coverage: tuple[Coverage, Path], tmp_path: Path
    ) -> None:
        """Test generating coverage reports with different formats."""
        cov, source_path = measured_coverage
        text_output = StringIO()
        lcov_path = tmp_path / "coverage.lcov"

        text_pct = coverage_patch.generate_coverage_report("text", cov=cov, file=text_output)
        lcov_pct = coverage_patch.generate_coverage_report("lcov", cov=cov, outfile=str(lcov_path))

        assert text_pct == 100.0
        assert lcov_pct == 100.0
        assert "100%" in text_output.getvalue()
        lcov_content = lcov_path.read_text()
        assert f"SF:{source_path}" in lcov_content
        assert "end_of_record" in lcov_content
        assert coverage_patch.get_coverage_percentage() == lcov_pct

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

        coverage_patch.stop_coverage(save=False, erase=True)

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
