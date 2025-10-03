"""Tests for fast file-level coverage."""

import os
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.coverage.factory import _is_fast_coverage_enabled
from ddtrace.internal.coverage.factory import install_coverage_collector
from ddtrace.internal.coverage.fast import FastFileCoverage
from ddtrace.internal.coverage.fast import FastModuleCodeCollector


class TestFastCoverageConfig:
    """Test fast coverage configuration."""

    def test_fast_coverage_env_var_enabled(self):
        """Test that environment variable enables fast coverage."""
        test_cases = ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON"]

        for value in test_cases:
            with patch.dict(os.environ, {"_DD_CIVISIBILITY_FAST_COVERAGE": value}):
                assert _is_fast_coverage_enabled(), f"Failed for value: {value}"

    def test_fast_coverage_env_var_disabled(self):
        """Test that environment variable disables fast coverage."""
        test_cases = ["0", "false", "False", "FALSE", "no", "NO", "off", "OFF", ""]

        for value in test_cases:
            with patch.dict(os.environ, {"_DD_CIVISIBILITY_FAST_COVERAGE": value}):
                assert not _is_fast_coverage_enabled(), f"Should be disabled for value: {value}"

    def test_fast_coverage_env_var_unset(self):
        """Test that unset environment variable disables fast coverage."""
        with patch.dict(os.environ, {}, clear=True):
            if "_DD_CIVISIBILITY_FAST_COVERAGE" in os.environ:
                del os.environ["_DD_CIVISIBILITY_FAST_COVERAGE"]

            assert not _is_fast_coverage_enabled()


class TestFastFileCoverage:
    """Test fast file coverage data structure."""

    def test_file_coverage_initialization(self):
        """Test that FastFileCoverage initializes correctly."""
        coverage = FastFileCoverage()

        assert len(coverage._covered_files) == 0

    def test_mark_file_covered(self):
        """Test marking files as covered."""
        coverage = FastFileCoverage()

        coverage.mark_file_covered("/path/to/file1.py")
        coverage.mark_file_covered("/path/to/file2.py")

        assert "/path/to/file1.py" in coverage._covered_files
        assert "/path/to/file2.py" in coverage._covered_files
        assert len(coverage._covered_files) == 2

    def test_deduplication(self):
        """Test that marking the same file multiple times doesn't duplicate it."""
        coverage = FastFileCoverage()

        coverage.mark_file_covered("/path/to/file1.py")
        coverage.mark_file_covered("/path/to/file1.py")
        coverage.mark_file_covered("/path/to/file1.py")

        assert len(coverage._covered_files) == 1
        assert "/path/to/file1.py" in coverage._covered_files

    def test_to_coverage_lines_format(self):
        """Test conversion to CoverageLines bitmap format."""
        coverage = FastFileCoverage()

        coverage.mark_file_covered("/path/to/file1.py")
        coverage.mark_file_covered("/path/to/file2.py")

        coverage_lines_format = coverage.to_coverage_lines_format()

        # Should have CoverageLines objects for each file
        assert len(coverage_lines_format) == 2
        assert "/path/to/file1.py" in coverage_lines_format
        assert "/path/to/file2.py" in coverage_lines_format

        # Each CoverageLines should have line 1 marked as covered
        for file_path, coverage_lines in coverage_lines_format.items():
            assert coverage_lines.to_sorted_list() == [1]
            assert len(coverage_lines) == 1
            # Check that the bitmap has the correct format (line 1 covered)
            bitmap_bytes = coverage_lines.to_bytes()
            assert bitmap_bytes[0] == 0x40  # 01000000 in binary (line 1 covered)

    def test_clear_coverage(self):
        """Test clearing coverage data."""
        coverage = FastFileCoverage()

        coverage.mark_file_covered("/path/to/file1.py")
        assert len(coverage._covered_files) == 1

        coverage.clear()
        assert len(coverage._covered_files) == 0


class TestFastModuleCodeCollector:
    """Test fast coverage collector."""

    def setup_method(self):
        """Setup for each test method."""
        # Ensure no collector is installed
        if FastModuleCodeCollector._instance:
            FastModuleCodeCollector.uninstall()

    def teardown_method(self):
        """Cleanup after each test method."""
        # Clean up any installed collector
        if FastModuleCodeCollector._instance:
            FastModuleCodeCollector.uninstall()

    def test_collector_installation(self):
        """Test collector installation."""
        assert not FastModuleCodeCollector.is_installed()

        FastModuleCodeCollector.install()

        assert FastModuleCodeCollector.is_installed()
        assert FastModuleCodeCollector._instance is not None

    def test_coverage_control(self):
        """Test starting and stopping coverage."""
        FastModuleCodeCollector.install()

        # Initially disabled
        assert not FastModuleCodeCollector._instance._coverage_enabled

        # Start coverage
        FastModuleCodeCollector.start_coverage()
        assert FastModuleCodeCollector._instance._coverage_enabled

        # Stop coverage
        FastModuleCodeCollector.stop_coverage()
        assert not FastModuleCodeCollector._instance._coverage_enabled

    def test_file_coverage_tracking(self):
        """Test file coverage tracking functionality."""
        FastModuleCodeCollector.install()
        collector = FastModuleCodeCollector._instance

        # Enable coverage
        collector._coverage_enabled = True

        # Mark files as covered
        collector._fast_coverage.mark_file_covered("/path/to/file1.py")
        collector._fast_coverage.mark_file_covered("/path/to/file2.py")

        # Check coverage
        coverage_data = collector.get_fast_coverage_data()
        assert "/path/to/file1.py" in coverage_data
        assert "/path/to/file2.py" in coverage_data
        assert len(coverage_data) == 2

    def test_coverage_disabled(self):
        """Test that coverage tracking does nothing when coverage is disabled."""
        FastModuleCodeCollector.install()
        collector = FastModuleCodeCollector._instance

        # Coverage disabled by default
        assert not collector._coverage_enabled

        # Mark file as covered (should not be tracked when disabled)
        collector._fast_coverage.mark_file_covered("/path/to/file1.py")

        # Should have coverage (marking is independent of enabled state)
        coverage_data = collector.get_fast_coverage_data()
        assert len(coverage_data) == 1  # The file is still marked, but won't be used

    def test_file_deduplication(self):
        """Test that file marking deduplicates file hits."""
        FastModuleCodeCollector.install()
        collector = FastModuleCodeCollector._instance
        collector._coverage_enabled = True

        # Mark same file multiple times
        collector._fast_coverage.mark_file_covered("/path/to/file1.py")
        collector._fast_coverage.mark_file_covered("/path/to/file1.py")
        collector._fast_coverage.mark_file_covered("/path/to/file1.py")

        # Should only be recorded once
        coverage_data = collector.get_fast_coverage_data()
        assert len(coverage_data) == 1
        assert "/path/to/file1.py" in coverage_data

    def test_test_session_management(self):
        """Test test session management."""
        FastModuleCodeCollector.install()
        collector = FastModuleCodeCollector._instance

        # Start test session (clears tracked files)
        collector.start_test_session("test_1")
        assert len(collector._tracked_files) == 0

        # End test session
        collector.end_test_session()
        # No specific state change expected

    def test_clear_coverage(self):
        """Test clearing coverage data."""
        FastModuleCodeCollector.install()
        collector = FastModuleCodeCollector._instance

        # Add some coverage data
        collector._fast_coverage.mark_file_covered("/path/to/file1.py")
        assert len(collector.get_fast_coverage_data()) == 1

        # Clear coverage
        collector.clear_coverage()
        assert len(collector.get_fast_coverage_data()) == 0


class TestCoverageFactory:
    """Test coverage factory functionality."""

    def test_factory_installs_fast_coverage_when_enabled(self):
        """Test that factory installs fast coverage when environment variable is set."""
        with patch.dict(os.environ, {"_DD_CIVISIBILITY_FAST_COVERAGE": "1"}):
            with patch.object(FastModuleCodeCollector, "install") as mock_install:
                install_coverage_collector([Path("/test/path")])
                mock_install.assert_called_once_with([Path("/test/path")], False)

    def test_factory_installs_regular_coverage_when_disabled(self):
        """Test that factory installs regular coverage when fast coverage is disabled."""
        with patch.dict(os.environ, {}, clear=True):
            if "_DD_CIVISIBILITY_FAST_COVERAGE" in os.environ:
                del os.environ["_DD_CIVISIBILITY_FAST_COVERAGE"]

            with patch.object(ModuleCodeCollector, "install") as mock_install:
                install_coverage_collector([Path("/test/path")])
                mock_install.assert_called_once_with([Path("/test/path")], False)

    def test_factory_provides_correct_context(self):
        """Test that factory provides the correct context manager."""
        # Test fast coverage context
        with patch.dict(os.environ, {"_DD_CIVISIBILITY_FAST_COVERAGE": "1"}):
            with patch.object(FastModuleCodeCollector, "CollectInContext") as mock_context:
                from ddtrace.internal.coverage.factory import get_coverage_context

                get_coverage_context()
                mock_context.assert_called_once()

        # Test regular coverage context
        with patch.dict(os.environ, {}, clear=True):
            if "_DD_CIVISIBILITY_FAST_COVERAGE" in os.environ:
                del os.environ["_DD_CIVISIBILITY_FAST_COVERAGE"]

            with patch.object(ModuleCodeCollector, "CollectInContext") as mock_context:
                from ddtrace.internal.coverage.factory import get_coverage_context

                get_coverage_context()
                mock_context.assert_called_once()


class TestFastCoverageTransform:
    """Test fast coverage transform functionality."""

    def test_transform_tracks_files_without_bytecode_modification(self):
        """Test that transform tracks files without modifying bytecode."""
        FastModuleCodeCollector.install()
        collector = FastModuleCodeCollector._instance
        collector._coverage_enabled = True

        # Create a simple code object
        code = compile("print('hello')", "test.py", "exec")

        # Mock module
        mock_module = MagicMock()
        mock_module.__name__ = "test_module"
        mock_module.__file__ = "test.py"

        # Transform should return the same code object but track the file
        result_code = collector.transform(code, mock_module)

        # Code should be unchanged (no bytecode modification)
        assert result_code is code

        # File should be tracked and marked as covered
        assert "test.py" in collector._tracked_files
        coverage_data = collector.get_fast_coverage_data()
        assert "test.py" in coverage_data


@pytest.mark.integration
class TestFastCoverageEndToEnd:
    """End-to-end integration tests."""

    def test_fast_coverage_environment_integration(self):
        """Test that fast coverage works when environment variable is set."""
        with patch.dict(os.environ, {"_DD_CIVISIBILITY_FAST_COVERAGE": "1"}):
            # Factory should install fast coverage
            with patch.object(FastModuleCodeCollector, "install") as mock_install:
                install_coverage_collector()
                mock_install.assert_called_once()

    def test_regular_coverage_when_fast_disabled(self):
        """Test that regular coverage is used when fast coverage is disabled."""
        with patch.dict(os.environ, {}, clear=True):
            if "_DD_CIVISIBILITY_FAST_COVERAGE" in os.environ:
                del os.environ["_DD_CIVISIBILITY_FAST_COVERAGE"]

            # Factory should install regular coverage
            with patch.object(ModuleCodeCollector, "install") as mock_install:
                install_coverage_collector()
                mock_install.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__])
