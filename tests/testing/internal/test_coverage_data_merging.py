"""
Unit tests for coverage data collection and merging logic.

This file provides detailed testing of the actual coverage data processing,
including local coverage collection, backend coverage merging, and LCOV generation.
"""

from pathlib import Path
import tempfile
from unittest.mock import Mock
from unittest.mock import patch

from ddtrace.testing.internal.coverage_report import generate_coverage_report_lcov_from_coverage_py
from ddtrace.testing.internal.coverage_report import generate_coverage_report_lcov_from_module_collector


class TestCoverageDataMerging:
    """Test coverage data collection and merging functionality."""

    def test_coverage_py_with_backend_merging_detailed(self):
        """Test detailed coverage.py data merging with backend coverage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_path = Path(tmpdir)

            # Create mock coverage.py instance with specific coverage data
            mock_cov = Mock()
            mock_cov_data = Mock()
            mock_cov.stop = Mock()
            mock_cov.get_data.return_value = mock_cov_data

            # Simulate a Python file with multiple functions
            test_file_abs = str(workspace_path / "calculator.py")
            mock_cov_data.measured_files.return_value = [test_file_abs]

            # Local coverage: only add() function was executed (lines 2,3)
            # Missing: multiply() and divide() functions (lines 6,7,10,11,12)
            mock_cov_data.lines.return_value = [2, 3]  # add function covered locally
            mock_cov_data.missing.return_value = [6, 7, 10, 11, 12]  # other functions not covered locally

            # Backend coverage: skipped tests covered multiply() and divide() functions
            backend_coverage = {"calculator.py": {6, 7, 10, 11}}  # multiply and divide (excluding error case line 12)

            # Generate merged coverage report
            result = generate_coverage_report_lcov_from_coverage_py(mock_cov, workspace_path, backend_coverage)

            assert result is not None
            lcov_content = result.decode("utf-8")

            # Verify file is included
            assert "SF:calculator.py" in lcov_content

            # Verify local coverage is preserved
            assert "DA:2,1" in lcov_content  # add function line 1 - covered
            assert "DA:3,1" in lcov_content  # add function line 2 - covered

            # Verify backend coverage is merged
            assert "DA:6,1" in lcov_content  # multiply function - covered by backend
            assert "DA:7,1" in lcov_content  # multiply function - covered by backend
            assert "DA:10,1" in lcov_content  # divide function - covered by backend
            assert "DA:11,1" in lcov_content  # divide function - covered by backend

            # Verify uncovered lines are reported as uncovered
            assert "DA:12,0" in lcov_content  # error case not covered by either

            # Verify summary statistics are correct
            # Total executable lines: 6 (2,3,6,7,10,11,12)
            # Covered lines: 6 (2,3,6,7,10,11) - line 12 is uncovered
            assert "LF:6" in lcov_content or "LF:7" in lcov_content  # lines found
            assert "LH:6" in lcov_content  # lines hit (all but line 12)

    def test_backend_only_coverage_detailed(self):
        """Test coverage reporting when only backend coverage exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_path = Path(tmpdir)

            # Mock coverage.py with no local coverage
            mock_cov = Mock()
            mock_cov_data = Mock()
            mock_cov.stop = Mock()
            mock_cov.get_data.return_value = mock_cov_data
            mock_cov_data.measured_files.return_value = []  # no local files measured

            # Only backend coverage exists
            backend_coverage = {
                "utils.py": {1, 3, 5, 7},  # utility functions covered by skipped tests
                "helpers.py": {2, 4, 6},  # helper functions covered by skipped tests
            }

            # Generate coverage report
            result = generate_coverage_report_lcov_from_coverage_py(mock_cov, workspace_path, backend_coverage)

            assert result is not None
            lcov_content = result.decode("utf-8")

            # Both files should be included
            assert "SF:utils.py" in lcov_content
            assert "SF:helpers.py" in lcov_content

            # All backend lines should be reported as covered
            for line in [1, 3, 5, 7]:
                assert f"DA:{line},1" in lcov_content
            for line in [2, 4, 6]:
                assert f"DA:{line},1" in lcov_content

            # Should have correct counts (backend-only files assume all covered lines are executable)
            utils_lf = lcov_content.count("LF:4")  # utils.py has 4 lines
            utils_lh = lcov_content.count("LH:4")  # all 4 covered
            helpers_lf = lcov_content.count("LF:3")  # helpers.py has 3 lines
            helpers_lh = lcov_content.count("LH:3")  # all 3 covered

            assert utils_lf >= 1 and helpers_lf >= 1, "Should report lines found for each file"
            assert utils_lh >= 1 and helpers_lh >= 1, "Should report lines hit for each file"

    @patch("ddtrace.internal.coverage.code.ModuleCodeCollector.is_installed", return_value=True)
    def test_module_code_collector_merging_detailed(self, mock_is_installed):
        """Test ModuleCodeCollector coverage merging with backend coverage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_path = Path(tmpdir)

            # Mock ModuleCodeCollector instance and coverage data
            mock_instance = Mock()
            mock_coverage_lines = Mock()
            mock_executable_lines = Mock()

            # Mock the coverage data
            test_file_abs = str(workspace_path / "service.py")

            # Local coverage: lines 1,2,3 covered, lines 4,5 executable but not covered
            mock_coverage_lines.to_sorted_list.return_value = [1, 2, 3]
            mock_executable_lines.to_sorted_list.return_value = [1, 2, 3, 4, 5]

            mock_instance.covered = {test_file_abs: mock_coverage_lines}
            mock_instance.lines = {test_file_abs: mock_executable_lines}

            # Backend coverage covers the previously uncovered lines
            backend_coverage = {"service.py": {4, 5}}

            with patch("ddtrace.internal.coverage.code.ModuleCodeCollector._instance", mock_instance):
                result = generate_coverage_report_lcov_from_module_collector(workspace_path, backend_coverage)

                assert result is not None
                lcov_content = result.decode("utf-8")

                # Verify file is included
                assert "SF:service.py" in lcov_content

                # Verify all lines are now covered (local + backend)
                for line in [1, 2, 3, 4, 5]:
                    assert f"DA:{line},1" in lcov_content

                # Should report complete coverage
                assert "LF:5" in lcov_content  # 5 executable lines
                assert "LH:5" in lcov_content  # all 5 covered after merge

    def test_overlapping_coverage_deduplication(self):
        """Test that overlapping coverage between local and backend is handled correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_path = Path(tmpdir)

            mock_cov = Mock()
            mock_cov_data = Mock()
            mock_cov.stop = Mock()
            mock_cov.get_data.return_value = mock_cov_data

            test_file_abs = str(workspace_path / "overlap.py")
            mock_cov_data.measured_files.return_value = [test_file_abs]

            # Local coverage includes lines 1,2,3,4
            mock_cov_data.lines.return_value = [1, 2, 3, 4]
            mock_cov_data.missing.return_value = [5, 6]  # lines 5,6 not covered locally

            # Backend coverage overlaps with local (lines 2,3) and adds new (lines 5,7)
            backend_coverage = {"overlap.py": {2, 3, 5, 7}}

            result = generate_coverage_report_lcov_from_coverage_py(mock_cov, workspace_path, backend_coverage)

            assert result is not None
            lcov_content = result.decode("utf-8")

            # All lines should be reported as covered exactly once
            assert lcov_content.count("DA:1,1") == 1  # local only
            assert lcov_content.count("DA:2,1") == 1  # both local and backend (deduplicated)
            assert lcov_content.count("DA:3,1") == 1  # both local and backend (deduplicated)
            assert lcov_content.count("DA:4,1") == 1  # local only
            assert lcov_content.count("DA:5,1") == 1  # backend only
            assert lcov_content.count("DA:6,0") == 1  # uncovered (was missing locally, not in backend)
            assert lcov_content.count("DA:7,1") == 1  # backend only

            # Should report accurate statistics
            assert "LF:7" in lcov_content  # 7 executable lines total
            assert "LH:6" in lcov_content  # 6 lines covered (all but line 6)

    def test_empty_coverage_scenarios(self):
        """Test edge cases with empty or missing coverage data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_path = Path(tmpdir)

            # Test 1: No coverage at all
            mock_cov = Mock()
            mock_cov_data = Mock()
            mock_cov.stop = Mock()
            mock_cov.get_data.return_value = mock_cov_data
            mock_cov_data.measured_files.return_value = []

            result = generate_coverage_report_lcov_from_coverage_py(mock_cov, workspace_path, {})
            assert result is None, "Should return None when no coverage data exists"

            # Test 2: Empty local coverage but backend coverage exists
            result = generate_coverage_report_lcov_from_coverage_py(
                mock_cov, workspace_path, {"backend_only.py": {1, 2}}
            )
            assert result is not None
            lcov_content = result.decode("utf-8")
            assert "SF:backend_only.py" in lcov_content
            assert "DA:1,1" in lcov_content
            assert "DA:2,1" in lcov_content
