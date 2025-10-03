"""Tests for fast file-level coverage."""

import os
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from ddtrace.internal.coverage.config import FAST_COVERAGE_CONFIG, FastCoverageConfig, is_fast_coverage_enabled
from ddtrace.internal.coverage.fast import FastFileCoverage, FastModuleCodeCollector
# Fast instrumentation is now handled by inheritance
from ddtrace.internal.coverage.code import ModuleCodeCollector


class TestFastCoverageConfig:
    """Test fast coverage configuration."""
    
    def test_fast_coverage_env_var_enabled(self):
        """Test that environment variable enables fast coverage."""
        test_cases = ['1', 'true', 'True', 'TRUE', 'yes', 'YES', 'on', 'ON']
        
        for value in test_cases:
            with patch.dict(os.environ, {'_DD_CIVISIBILITY_FAST_COVERAGE': value}):
                assert is_fast_coverage_enabled(), f"Failed for value: {value}"
                config = FastCoverageConfig()
                assert config.enabled, f"Config not enabled for value: {value}"
                assert bool(config), f"Config bool() failed for value: {value}"
    
    def test_fast_coverage_env_var_disabled(self):
        """Test that environment variable disables fast coverage."""
        test_cases = ['0', 'false', 'False', 'FALSE', 'no', 'NO', 'off', 'OFF', '']
        
        for value in test_cases:
            with patch.dict(os.environ, {'_DD_CIVISIBILITY_FAST_COVERAGE': value}):
                assert not is_fast_coverage_enabled(), f"Should be disabled for value: {value}"
                config = FastCoverageConfig()
                assert not config.enabled, f"Config should be disabled for value: {value}"
                assert not bool(config), f"Config bool() should be False for value: {value}"
    
    def test_fast_coverage_env_var_unset(self):
        """Test that unset environment variable disables fast coverage."""
        with patch.dict(os.environ, {}, clear=True):
            if '_DD_CIVISIBILITY_FAST_COVERAGE' in os.environ:
                del os.environ['_DD_CIVISIBILITY_FAST_COVERAGE']
            
            assert not is_fast_coverage_enabled()
            config = FastCoverageConfig()
            assert not config.enabled
            assert not bool(config)


class TestFastFileCoverage:
    """Test fast file coverage data structure."""
    
    def test_file_coverage_initialization(self):
        """Test that FastFileCoverage initializes correctly."""
        coverage = FastFileCoverage()
        
        assert len(coverage) == 0
        assert not bool(coverage)
        assert coverage.get_covered_files() == set()
        assert coverage._current_test_id is None
    
    def test_mark_file_covered(self):
        """Test marking files as covered."""
        coverage = FastFileCoverage()
        
        coverage.mark_file_covered("/path/to/file1.py")
        coverage.mark_file_covered("/path/to/file2.py")
        
        covered_files = coverage.get_covered_files()
        assert "/path/to/file1.py" in covered_files
        assert "/path/to/file2.py" in covered_files
        assert len(covered_files) == 2
        assert len(coverage) == 2
        assert bool(coverage)
    
    def test_test_session_management(self):
        """Test test session start/end functionality."""
        coverage = FastFileCoverage()
        
        # Start test session
        coverage.start_test_session("test_1")
        assert coverage._current_test_id == "test_1"
        
        # Mark files covered during test
        coverage.mark_file_covered("/path/to/file1.py")
        coverage.mark_file_covered("/path/to/file2.py")
        
        # Check test-specific coverage
        test_files = coverage.get_test_covered_files("test_1")
        assert "/path/to/file1.py" in test_files
        assert "/path/to/file2.py" in test_files
        assert len(test_files) == 2
        
        # End test session
        coverage.end_test_session()
        assert coverage._current_test_id is None
        
        # Files should still be in global coverage
        assert len(coverage.get_covered_files()) == 2
    
    def test_multiple_test_sessions(self):
        """Test multiple test sessions."""
        coverage = FastFileCoverage()
        
        # First test
        coverage.start_test_session("test_1")
        coverage.mark_file_covered("/path/to/file1.py")
        coverage.end_test_session()
        
        # Second test
        coverage.start_test_session("test_2")
        coverage.mark_file_covered("/path/to/file2.py")
        coverage.end_test_session()
        
        # Check per-test coverage
        test1_files = coverage.get_test_covered_files("test_1")
        test2_files = coverage.get_test_covered_files("test_2")
        
        assert test1_files == {"/path/to/file1.py"}
        assert test2_files == {"/path/to/file2.py"}
        
        # Check global coverage
        all_files = coverage.get_covered_files()
        assert all_files == {"/path/to/file1.py", "/path/to/file2.py"}
    
    def test_to_line_coverage_format(self):
        """Test conversion to line coverage format."""
        coverage = FastFileCoverage()
        
        coverage.mark_file_covered("/path/to/file1.py")
        coverage.mark_file_covered("/path/to/file2.py")
        
        line_format = coverage.to_line_coverage_format()
        
        expected = {
            "/path/to/file1.py": [[1, 0, 999999, 0, -1]],
            "/path/to/file2.py": [[1, 0, 999999, 0, -1]]
        }
        
        assert line_format == expected
    
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
        
        coverage.start_test_session("test_1")
        coverage.mark_file_covered("/path/to/file1.py")
        
        assert len(coverage) == 1
        assert coverage._current_test_id == "test_1"
        
        coverage.clear()
        
        assert len(coverage) == 0
        assert not bool(coverage)
        assert coverage._current_test_id is None
        assert coverage.get_covered_files() == set()


class TestFastCoverageCollector:
    """Test fast coverage collector."""
    
    def setup_method(self):
        """Setup for each test method."""
        # Ensure no collector is installed
        if FastCoverageCollector._instance:
            FastCoverageCollector.uninstall()
    
    def teardown_method(self):
        """Cleanup after each test method."""
        # Clean up any installed collector
        if FastCoverageCollector._instance:
            FastCoverageCollector.uninstall()
    
    def test_collector_installation(self):
        """Test collector installation."""
        assert not FastCoverageCollector.is_installed()
        
        FastCoverageCollector.install()
        
        assert FastCoverageCollector.is_installed()
        assert FastCoverageCollector._instance is not None
    
    def test_coverage_control(self):
        """Test starting and stopping coverage."""
        FastCoverageCollector.install()
        
        # Initially disabled
        assert not FastCoverageCollector._instance._coverage_enabled
        
        # Start coverage
        FastCoverageCollector.start_coverage()
        assert FastCoverageCollector._instance._coverage_enabled
        
        # Stop coverage
        FastCoverageCollector.stop_coverage()
        assert not FastCoverageCollector._instance._coverage_enabled
    
    def test_fast_hook(self):
        """Test the fast hook functionality."""
        FastCoverageCollector.install()
        collector = FastCoverageCollector._instance
        
        # Enable coverage
        collector._coverage_enabled = True
        
        # Call hook
        collector.fast_hook("/path/to/file1.py")
        collector.fast_hook("/path/to/file2.py")
        
        # Check coverage
        covered_files = collector.get_covered_files()
        assert "/path/to/file1.py" in covered_files
        assert "/path/to/file2.py" in covered_files
        assert len(covered_files) == 2
    
    def test_fast_hook_disabled(self):
        """Test that hook does nothing when coverage is disabled."""
        FastCoverageCollector.install()
        collector = FastCoverageCollector._instance
        
        # Coverage disabled by default
        assert not collector._coverage_enabled
        
        # Call hook
        collector.fast_hook("/path/to/file1.py")
        
        # Should have no coverage
        covered_files = collector.get_covered_files()
        assert len(covered_files) == 0
    
    def test_fast_hook_deduplication(self):
        """Test that hook deduplicates file hits."""
        FastCoverageCollector.install()
        collector = FastCoverageCollector._instance
        collector._coverage_enabled = True
        
        # Call hook multiple times for same file
        collector.fast_hook("/path/to/file1.py")
        collector.fast_hook("/path/to/file1.py")
        collector.fast_hook("/path/to/file1.py")
        
        # Should only be recorded once
        covered_files = collector.get_covered_files()
        assert len(covered_files) == 1
        assert "/path/to/file1.py" in covered_files
    
    def test_test_session_management(self):
        """Test test session management."""
        FastCoverageCollector.install()
        
        # Start test session
        FastCoverageCollector.start_test_session("test_1")
        assert FastCoverageCollector._instance._coverage._current_test_id == "test_1"
        
        # End test session
        FastCoverageCollector.end_test_session()
        assert FastCoverageCollector._instance._coverage._current_test_id is None
    
    def test_debug_info(self):
        """Test debug information."""
        # No collector installed
        debug_info = FastCoverageCollector.debug_info()
        assert "error" in debug_info
        
        # Install collector
        FastCoverageCollector.install()
        FastCoverageCollector.start_coverage()
        
        debug_info = FastCoverageCollector.debug_info()
        assert debug_info["enabled"] is True
        assert debug_info["instrumented_files"] == 0
        assert debug_info["covered_files"] == 0
        assert debug_info["files_hit"] == 0
        assert debug_info["current_test"] is None


class TestModuleCodeCollectorIntegration:
    """Test integration with existing ModuleCodeCollector."""
    
    def test_fast_coverage_detection(self):
        """Test detection of fast coverage mode."""
        with patch.dict(os.environ, {'_DD_CIVISIBILITY_FAST_COVERAGE': '1'}):
            # Create a new config instance to pick up the env var
            from ddtrace.internal.coverage.config import FastCoverageConfig
            config = FastCoverageConfig()
            
            with patch('ddtrace.internal.coverage.code.FAST_COVERAGE_CONFIG', config):
                with patch.object(FastCoverageCollector, '_instance', MagicMock()):
                    assert ModuleCodeCollector.is_fast_coverage_active()
        
        # Test with fast coverage disabled
        with patch.dict(os.environ, {}, clear=True):
            config = FastCoverageConfig()
            with patch('ddtrace.internal.coverage.code.FAST_COVERAGE_CONFIG', config):
                assert not ModuleCodeCollector.is_fast_coverage_active()
    
    def test_install_delegates_to_fast_coverage(self):
        """Test that install delegates to fast coverage when enabled."""
        with patch.dict(os.environ, {'_DD_CIVISIBILITY_FAST_COVERAGE': '1'}):
            from ddtrace.internal.coverage.config import FastCoverageConfig
            config = FastCoverageConfig()
            
            with patch('ddtrace.internal.coverage.code.FAST_COVERAGE_CONFIG', config):
                with patch.object(FastCoverageCollector, 'install') as mock_install:
                    ModuleCodeCollector.install([Path("/test/path")])
                    mock_install.assert_called_once_with([Path("/test/path")])
    
    def test_coverage_control_delegates_to_fast_coverage(self):
        """Test that coverage control delegates to fast coverage when active."""
        with patch.object(ModuleCodeCollector, 'is_fast_coverage_active', return_value=True):
            with patch.object(FastCoverageCollector, 'start_coverage') as mock_start:
                ModuleCodeCollector.start_coverage()
                mock_start.assert_called_once()
            
            with patch.object(FastCoverageCollector, 'stop_coverage') as mock_stop:
                ModuleCodeCollector.stop_coverage()
                mock_stop.assert_called_once()


class TestFastCoverageTransform:
    """Test fast coverage transform functionality."""
    
    def test_transform_tracks_files_without_bytecode_modification(self):
        """Test that transform tracks files without modifying bytecode."""
        FastCoverageCollector.install()
        collector = FastCoverageCollector._instance
        collector._coverage_enabled = True
        
        # Create a simple code object
        code = compile("print('hello')", "test.py", "exec")
        
        # Mock module
        mock_module = MagicMock()
        mock_module.__name__ = "test_module"
        
        # Transform should return the same code object but track the file
        result_code = collector.transform(code, mock_module)
        
        # Code should be unchanged (no bytecode modification)
        assert result_code is code
        
        # File should be tracked and marked as covered
        assert "test.py" in collector._instrumented_files
        covered_files = collector.get_covered_files()
        assert "test.py" in covered_files


@pytest.mark.integration
class TestFastCoverageEndToEnd:
    """End-to-end integration tests."""
    
    def test_fast_coverage_environment_integration(self):
        """Test that fast coverage works when environment variable is set."""
        with patch.dict(os.environ, {'_DD_CIVISIBILITY_FAST_COVERAGE': '1'}):
            # Reload config to pick up environment variable
            from ddtrace.internal.coverage.config import FastCoverageConfig
            config = FastCoverageConfig()
            assert config.enabled
            
            # Install should use fast coverage
            with patch('ddtrace.internal.coverage.code.FAST_COVERAGE_CONFIG', config):
                with patch.object(FastCoverageCollector, 'install') as mock_install:
                    ModuleCodeCollector.install()
                    mock_install.assert_called_once()
    
    def test_regular_coverage_when_fast_disabled(self):
        """Test that regular coverage is used when fast coverage is disabled."""
        with patch.dict(os.environ, {'_DD_CIVISIBILITY_FAST_COVERAGE': '0'}):
            config = FastCoverageConfig()
            assert not config.enabled
            
            # Should not delegate to fast coverage
            with patch.object(FastCoverageCollector, 'install') as mock_install:
                # This would normally install regular coverage, but we'll just check
                # that fast coverage install is not called
                assert not ModuleCodeCollector.is_fast_coverage_active()
                mock_install.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__])
