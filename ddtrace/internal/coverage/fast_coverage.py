"""Fast file-level coverage tracking."""

import typing as t
from collections import defaultdict
from pathlib import Path


class FastFileCoverage:
    """Tracks file-level coverage only (not individual lines)."""
    
    def __init__(self):
        self._covered_files: t.Set[str] = set()
        self._test_covered_files: t.DefaultDict[str, t.Set[str]] = defaultdict(set)
        self._current_test_id: t.Optional[str] = None
    
    def mark_file_covered(self, file_path: str) -> None:
        """Mark a file as covered."""
        self._covered_files.add(file_path)
        
        # Also track per-test if we're in a test session
        if self._current_test_id:
            self._test_covered_files[self._current_test_id].add(file_path)
    
    def start_test_session(self, test_id: str) -> None:
        """Start tracking coverage for a specific test."""
        self._current_test_id = test_id
    
    def end_test_session(self) -> None:
        """End current test session."""
        self._current_test_id = None
    
    def get_covered_files(self) -> t.Set[str]:
        """Get all covered files."""
        return self._covered_files.copy()
    
    def get_test_covered_files(self, test_id: str) -> t.Set[str]:
        """Get files covered by a specific test."""
        return self._test_covered_files.get(test_id, set()).copy()
    
    def clear(self) -> None:
        """Clear all coverage data."""
        self._covered_files.clear()
        self._test_covered_files.clear()
        self._current_test_id = None
    
    def to_line_coverage_format(self) -> t.Dict[str, t.List[t.List[int]]]:
        """Convert to line coverage format for compatibility.
        
        Returns a format compatible with existing coverage reporting:
        {
            "filename": [[1, 0, 999999, 0, -1]]  # Covers entire file
        }
        """
        result = {}
        for file_path in self._covered_files:
            # Create a segment that covers the entire file
            # [start_line, start_col, end_line, end_col, count]
            result[file_path] = [[1, 0, 999999, 0, -1]]
        return result
    
    def to_coverage_lines_format(self) -> t.Dict[str, "CoverageLines"]:
        """Convert to CoverageLines format using bitmaps like regular coverage.
        
        For fast coverage, we just mark line 1 as covered to indicate the file was executed.
        This uses the same bitmap format as regular coverage but with minimal data.
        """
        from ddtrace.internal.test_visibility.coverage_lines import CoverageLines
        
        result = {}
        for file_path in self._covered_files:
            # Create a CoverageLines object with just line 1 marked as covered
            coverage_lines = CoverageLines()
            coverage_lines.add(1)  # Mark line 1 as covered to indicate file execution
            result[file_path] = coverage_lines
        return result
    
    def __len__(self) -> int:
        return len(self._covered_files)
    
    def __bool__(self) -> bool:
        return bool(self._covered_files)
