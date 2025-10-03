"""Fast file-level coverage collector."""

import logging
import typing as t
from pathlib import Path
from types import CodeType, ModuleType

from .fast_coverage import FastFileCoverage
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.packages import is_user_code

log = logging.getLogger(__name__)


class FastCoverageCollector(ModuleWatchdog):
    """Fast coverage collector that tracks files, not lines."""
    
    _instance: t.Optional["FastCoverageCollector"] = None
    
    def __init__(self):
        super().__init__()
        self._coverage = FastFileCoverage()
        self._instrumented_files: t.Set[str] = set()
        self._coverage_enabled = False
        self._include_paths: t.List[Path] = []
        
        # Track which files have already been hit to avoid redundant hooks
        self._files_already_hit: t.Set[str] = set()
    
    @classmethod
    def install(cls, include_paths: t.Optional[t.List[Path]] = None):
        """Install the fast coverage collector."""
        if cls.is_installed():
            return
        
        super().install()
        
        if cls._instance is None:
            return
        
        if include_paths is None:
            include_paths = [Path.cwd()]
        
        cls._instance._include_paths = include_paths
        log.info("Fast file-level coverage collector installed")
    
    def fast_hook(self, file_path: str) -> None:
        """Fast hook that only tracks file-level coverage.
        
        This is called once per file when the first instrumented line executes.
        """
        # Skip if coverage is disabled
        if not self._coverage_enabled:
            return
        
        # Skip if file already covered (optimization)
        if file_path in self._files_already_hit:
            return
        
        # Mark file as covered
        self._coverage.mark_file_covered(file_path)
        self._files_already_hit.add(file_path)
        
        log.debug(f"Fast coverage: marked file {file_path} as covered")
    
    def transform(self, code: CodeType, _module: ModuleType) -> CodeType:
        """Transform code to track file execution without modifying bytecode."""
        if _module is None:
            return code
        
        code_path = Path(code.co_filename).resolve()
        
        # Check if code path is within include paths
        if self._include_paths and not any(code_path.is_relative_to(include_path) for include_path in self._include_paths):
            return code
        
        # Skip if not user code
        if not is_user_code(code_path):
            return code
        
        # Skip if already tracked
        if code.co_filename in self._instrumented_files:
            return code
        
        # Track this file without modifying bytecode
        self._instrumented_files.add(code.co_filename)
        
        # Immediately mark as covered since we know it's being executed
        if self._coverage_enabled:
            self.fast_hook(code.co_filename)
        
        log.debug(f"Fast coverage: tracking file {code.co_filename}")
        return code
    
    @classmethod
    def start_coverage(cls):
        """Start coverage collection."""
        if cls._instance:
            cls._instance._coverage_enabled = True
            log.info("Fast coverage collection started")
    
    @classmethod
    def stop_coverage(cls):
        """Stop coverage collection."""
        if cls._instance:
            cls._instance._coverage_enabled = False
            log.info("Fast coverage collection stopped")
    
    @classmethod
    def start_test_session(cls, test_id: str):
        """Start a test session."""
        if cls._instance:
            cls._instance._coverage.start_test_session(test_id)
            # Reset per-test file tracking
            cls._instance._files_already_hit.clear()
    
    @classmethod
    def end_test_session(cls):
        """End current test session."""
        if cls._instance:
            cls._instance._coverage.end_test_session()
    
    @classmethod
    def get_coverage_data(cls) -> t.Dict[str, t.List[t.List[int]]]:
        """Get coverage data in compatible format."""
        if cls._instance:
            return cls._instance._coverage.to_line_coverage_format()
        return {}
    
    @classmethod
    def get_coverage_lines_data(cls) -> t.Dict[str, t.Any]:
        """Get coverage data in CoverageLines bitmap format (same as regular coverage)."""
        if cls._instance:
            return cls._instance._coverage.to_coverage_lines_format()
        return {}
    
    @classmethod
    def get_covered_files(cls) -> t.Set[str]:
        """Get set of covered files."""
        if cls._instance:
            return cls._instance._coverage.get_covered_files()
        return set()
    
    @classmethod
    def clear_coverage(cls):
        """Clear all coverage data."""
        if cls._instance:
            cls._instance._coverage.clear()
            cls._instance._files_already_hit.clear()

    def log_statistics(self):
        """Log coverage statistics for debugging."""
        covered_files = len(self._coverage.get_covered_files())
        instrumented_files = len(self._instrumented_files)
        
        log.info(f"Fast Coverage Statistics:")
        log.info(f"  - Instrumented files: {instrumented_files}")
        log.info(f"  - Covered files: {covered_files}")
        log.info(f"  - Coverage ratio: {covered_files/instrumented_files*100:.1f}%" if instrumented_files > 0 else "  - Coverage ratio: 0%")

    @classmethod
    def debug_info(cls) -> t.Dict[str, t.Any]:
        """Get debug information about fast coverage."""
        if not cls._instance:
            return {"error": "Fast coverage collector not installed"}
        
        return {
            "enabled": cls._instance._coverage_enabled,
            "instrumented_files": len(cls._instance._instrumented_files),
            "covered_files": len(cls._instance._coverage.get_covered_files()),
            "files_hit": len(cls._instance._files_already_hit),
            "current_test": cls._instance._coverage._current_test_id,
        }
