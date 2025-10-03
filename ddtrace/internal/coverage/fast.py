"""Fast file-level coverage implementation using hybrid approach."""

import logging
import os
import typing as t
from pathlib import Path
from types import CodeType, ModuleType

from ddtrace.internal.test_visibility.coverage_lines import CoverageLines

log = logging.getLogger(__name__)


def _is_fast_coverage_enabled() -> bool:
    """Check if fast file-level coverage is enabled via environment variable."""
    env_value = os.environ.get('_DD_CIVISIBILITY_FAST_COVERAGE', '').lower()
    return env_value in ('1', 'true', 'yes', 'on')


class FastFileCoverage:
    """Minimal file-level coverage tracking."""
    
    def __init__(self):
        self._covered_files: t.Set[str] = set()

    def mark_file_covered(self, file_path: str) -> None:
        """Mark a file as covered."""
        self._covered_files.add(file_path)

    def clear(self) -> None:
        """Clear all coverage data."""
        self._covered_files.clear()

    def to_coverage_lines_format(self) -> t.Dict[str, CoverageLines]:
        """Convert to CoverageLines format using bitmaps like regular coverage."""
        result = {}
        for file_path in self._covered_files:
            # Create a CoverageLines object with just line 1 marked as covered
            coverage_lines = CoverageLines()
            coverage_lines.add(1)  # Mark line 1 as covered to indicate file execution
            result[file_path] = coverage_lines
        return result


# Import at module level to enable proper inheritance
from ddtrace.internal.module import ModuleWatchdog

class FastModuleCodeCollector(ModuleWatchdog):
    """Fast coverage collector that inherits from ModuleWatchdog to get instrumentation.
    
    This inherits from ModuleWatchdog to get the transform() method called
    during module loading, which enables the bytecode instrumentation.
    """
    
    _instance: t.Optional["FastModuleCodeCollector"] = None
    
    def __init__(self):
        # Initialize parent class
        super().__init__()
        
        # Fast coverage specific attributes
        self._coverage_enabled: bool = False
        self._fast_coverage = FastFileCoverage()
        self._tracked_files: t.Set[str] = set()
        
        # Minimal attributes needed for instrumentation compatibility
        self.seen: t.Set[t.Tuple[CodeType, str]] = set()
        from ddtrace.internal.test_visibility.coverage_lines import CoverageLines
        from collections import defaultdict
        self.lines: t.DefaultDict[str, CoverageLines] = defaultdict(CoverageLines)
    
    @classmethod
    def install(cls, include_paths: t.Optional[t.List[Path]] = None, collect_import_time_coverage: bool = False):
        """Install using ModuleWatchdog infrastructure."""
        if cls._instance is not None:
            return
        
        # Use parent's install mechanism
        super().install()
        
        if cls._instance is None:
            return
        
        # Store include paths for filtering
        if include_paths is None:
            include_paths = [Path.cwd()]
        cls._instance._include_paths = include_paths
    
    @classmethod
    def is_installed(cls) -> bool:
        """Check if installed."""
        return cls._instance is not None
    
    def start_coverage(self):
        """Start coverage collection."""
        self._coverage_enabled = True
    
    def stop_coverage(self):
        """Stop coverage collection."""
        self._coverage_enabled = False
    
    def hook(self, arg: t.Tuple[int, str, t.Optional[t.Tuple[str, t.Tuple[str, ...]]]]):
        """Hook method - not used in zero-instrumentation approach but kept for compatibility."""
        # This method is not called in our zero-instrumentation approach
        # but we keep it for compatibility with the ModuleCodeCollector interface
        pass
    
    def transform(self, code: CodeType, module: ModuleType) -> CodeType:
        """Transform code with NO instrumentation - just track at import time."""
        # Only track user code in our include paths
        if not hasattr(module, '__file__') or not module.__file__:
            return code
            
        file_path = Path(module.__file__).resolve()
        
        # Check if this file should be tracked
        if hasattr(self, '_include_paths'):
            should_track = any(file_path.is_relative_to(include_path) for include_path in self._include_paths)
            if not should_track:
                return code
        
        # FAST COVERAGE: No bytecode instrumentation at all!
        # Just mark the file as covered when the transform is called
        # This happens when the module is loaded and about to be executed
        if self._coverage_enabled and code.co_filename not in self._tracked_files:
            self._fast_coverage.mark_file_covered(code.co_filename)
            self._tracked_files.add(code.co_filename)
            log.debug(f"Fast coverage: marked file {code.co_filename} as covered (transform-time)")
        
        # Return original code unchanged - no instrumentation overhead!
        return code
    
    def after_import(self, module: ModuleType) -> None:
        """Called when a module is imported - track import-time coverage."""
        if not self._coverage_enabled:
            return
            
        if not hasattr(module, '__file__') or not module.__file__:
            return
            
        file_path = module.__file__
        
        # Apply same filtering as transform
        if hasattr(self, '_include_paths'):
            file_path_obj = Path(file_path).resolve()
            if not any(file_path_obj.is_relative_to(include_path) for include_path in self._include_paths):
                return
        
        # Mark file as covered at import time
        if file_path not in self._tracked_files:
            self._fast_coverage.mark_file_covered(file_path)
            self._tracked_files.add(file_path)
            log.debug(f"Fast coverage: marked file {file_path} as covered (import-time)")
    
    def get_fast_coverage_data(self) -> t.Dict[str, CoverageLines]:
        """Get coverage data in CoverageLines format."""
        return self._fast_coverage.to_coverage_lines_format()
    
    def start_test_session(self, test_id: str):
        """Start a test session."""
        self._tracked_files.clear()
    
    def end_test_session(self):
        """End current test session."""
        pass
    
    def clear_coverage(self):
        """Clear all coverage data."""
        self._fast_coverage.clear()
        self._tracked_files.clear()
    
