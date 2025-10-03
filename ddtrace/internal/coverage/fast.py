"""Minimal fast file-level coverage implementation."""

import logging
import typing as t
from collections import defaultdict
from pathlib import Path

from ddtrace.internal.test_visibility.coverage_lines import CoverageLines
from .code import ModuleCodeCollector

log = logging.getLogger(__name__)


class FastFileCoverage:
    """Minimal file-level coverage tracking."""
    
    def __init__(self):
        self._covered_files: t.Set[str] = set()
        self._current_test_id: t.Optional[str] = None

    def mark_file_covered(self, file_path: str) -> None:
        """Mark a file as covered."""
        self._covered_files.add(file_path)

    def start_test_session(self, test_id: str) -> None:
        """Start tracking coverage for a specific test."""
        self._current_test_id = test_id

    def end_test_session(self) -> None:
        """End current test session."""
        self._current_test_id = None

    def get_covered_files(self) -> t.Set[str]:
        """Get all covered files."""
        return self._covered_files.copy()

    def clear(self) -> None:
        """Clear all coverage data."""
        self._covered_files.clear()
        self._current_test_id = None

    def to_coverage_lines_format(self) -> t.Dict[str, CoverageLines]:
        """Convert to CoverageLines format using bitmaps like regular coverage."""
        result = {}
        for file_path in self._covered_files:
            # Create a CoverageLines object with just line 1 marked as covered
            coverage_lines = CoverageLines()
            coverage_lines.add(1)  # Mark line 1 as covered to indicate file execution
            result[file_path] = coverage_lines
        return result


class FastModuleCodeCollector(ModuleCodeCollector):
    """Fast coverage collector that inherits from ModuleCodeCollector but only tracks files."""
    
    def __init__(self):
        super().__init__()
        self._fast_coverage = FastFileCoverage()
        self._tracked_files: t.Set[str] = set()
    
    @classmethod
    def install(cls, include_paths: t.Optional[t.List[Path]] = None, collect_import_time_coverage: bool = False):
        """Install the fast coverage collector - override to prevent recursion."""
        if cls.is_installed():
            return

        # Call the parent's parent install method (ModuleWatchdog.install) to avoid recursion
        from ddtrace.internal.module import ModuleWatchdog
        ModuleWatchdog.install.__func__(cls)

        if cls._instance is None:
            # installation failed
            return

        if include_paths is None:
            include_paths = [Path.cwd()]

        cls._instance._include_paths = include_paths
        cls._instance._collect_import_coverage = collect_import_time_coverage

        if collect_import_time_coverage:
            cls.register_import_exception_hook(
                lambda x: True, cls._instance._exit_context_on_exception_hook
            )
    
    def hook(self, arg: t.Tuple[int, str, t.Optional[t.Tuple[str, t.Tuple[str, ...]]]]):
        """Override hook to only track files, not individual lines."""
        line: int
        path: str
        import_name: t.Optional[t.Tuple[str, t.Tuple[str, ...]]]
        line, path, import_name = arg
        
        # Only track each file once
        if self._coverage_enabled and path not in self._tracked_files:
            self._fast_coverage.mark_file_covered(path)
            self._tracked_files.add(path)
            log.debug(f"Fast coverage: marked file {path} as covered")
    
    def get_fast_coverage_data(self) -> t.Dict[str, CoverageLines]:
        """Get coverage data in CoverageLines format."""
        return self._fast_coverage.to_coverage_lines_format()
    
    def start_test_session(self, test_id: str):
        """Start a test session."""
        self._fast_coverage.start_test_session(test_id)
        # Reset per-test file tracking
        self._tracked_files.clear()
    
    def end_test_session(self):
        """End current test session."""
        self._fast_coverage.end_test_session()
    
    def clear_coverage(self):
        """Clear all coverage data."""
        self._fast_coverage.clear()
        self._tracked_files.clear()
