"""Fast file-level coverage implementation using hybrid approach."""

from pathlib import Path
from types import CodeType
from types import ModuleType
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


log = get_logger(__name__)


class FastFileCoverage:
    """Minimal file-level coverage tracking."""

    def __init__(self) -> None:
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


class FastModuleCodeCollector(ModuleWatchdog):
    """Fast coverage collector that tracks file-level coverage with zero instrumentation.

    This inherits from ModuleWatchdog to get the transform() method called
    during module loading, which is when we mark files as covered.
    No bytecode instrumentation is performed for maximum performance.
    """

    _instance: t.Optional["FastModuleCodeCollector"] = None

    def __init__(self) -> None:
        # Initialize parent class
        super().__init__()

        # Fast coverage specific attributes
        self._coverage_enabled: bool = False
        self._fast_coverage = FastFileCoverage()
        self._tracked_files: t.Set[str] = set()

    @classmethod
    def install(
        cls, include_paths: t.Optional[t.List[Path]] = None, collect_import_time_coverage: bool = False
    ) -> None:
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

    @classmethod
    def start_coverage(cls) -> None:
        """Start coverage collection."""
        if cls._instance is None:
            return
        cls._instance._coverage_enabled = True

    @classmethod
    def stop_coverage(cls) -> None:
        """Stop coverage collection."""
        if cls._instance is None:
            return
        cls._instance._coverage_enabled = False

    def transform(self, code: CodeType, module: ModuleType) -> CodeType:
        """Transform code with NO instrumentation - just track at import time."""
        # Only track user code in our include paths
        if not hasattr(module, "__file__") or not module.__file__:
            return code

        file_path = Path(module.__file__).resolve()

        # Check if this file should be tracked
        if hasattr(self, "_include_paths"):
            should_track = any(file_path.is_relative_to(include_path) for include_path in self._include_paths)
            if not should_track:
                return code

        # FAST COVERAGE: No bytecode instrumentation at all!
        # Just mark the file as covered when the transform is called
        # This happens when the module is loaded and about to be executed
        if self._coverage_enabled:
            # Always mark file as covered (FastFileCoverage handles deduplication)
            self._fast_coverage.mark_file_covered(code.co_filename)
            self._tracked_files.add(code.co_filename)
            log.debug("Fast coverage: marked file %s as covered (transform-time)", code.co_filename)

        # Return original code unchanged - no instrumentation overhead!
        return code

    def get_fast_coverage_data(self) -> t.Dict[str, CoverageLines]:
        """Get coverage data in CoverageLines format."""
        return self._fast_coverage.to_coverage_lines_format()

    def start_test_session(self, test_id: str) -> None:
        """Start a test session."""
        self._tracked_files.clear()

    def end_test_session(self) -> None:
        """End current test session."""
        pass

    def clear_coverage(self) -> None:
        """Clear all coverage data."""
        self._fast_coverage.clear()
        self._tracked_files.clear()

    def _add_import_time_lines(self, covered_lines: t.Any) -> None:
        """Add import-time coverage lines - matches ModuleCodeCollector interface."""
        # For fast coverage, we don't need to do anything special with import-time lines
        # since we already track files at transform time
        pass

    # Additional methods to match ModuleCodeCollector interface
    def get_covered_lines(self) -> t.Dict[str, CoverageLines]:
        """Get covered lines in the same format as ModuleCodeCollector."""
        return self.get_fast_coverage_data()

    @classmethod
    def coverage_enabled(cls) -> bool:
        """Check if coverage is enabled - matches ModuleCodeCollector interface."""
        return cls._instance is not None and cls._instance._coverage_enabled

    @classmethod
    def coverage_enabled_in_context(cls) -> bool:
        """Check if coverage is enabled in context - matches ModuleCodeCollector interface."""
        return cls._instance is not None and cls._instance._coverage_enabled

    @classmethod
    def report_seen_lines(
        cls, workspace_path: Path, include_imported: bool = False
    ) -> t.List[t.Dict[str, t.List[int]]]:
        """Generate coverage report - matches ModuleCodeCollector interface."""
        if cls._instance is None:
            return []

        coverage_data = cls._instance.get_fast_coverage_data()

        list_results = []
        # Convert to the format expected by existing code
        result = {}
        for file_path, coverage_lines in coverage_data.items():
            # Make paths relative to workspace if possible
            try:
                abs_path = Path(file_path).resolve()
                try:
                    rel_path = abs_path.relative_to(workspace_path.resolve())
                    result[str(rel_path)] = coverage_lines.to_sorted_list()
                except ValueError:
                    # File is outside workspace, use absolute path
                    result[str(abs_path)] = coverage_lines.to_sorted_list()
            except (OSError, ValueError):
                # Fallback to original path
                result[file_path] = coverage_lines.to_sorted_list()
            list_results.append(result)

        return list_results

    class CollectInContext:
        """Context manager for coverage collection - matches ModuleCodeCollector interface."""

        def __init__(self) -> None:
            self._collector = FastModuleCodeCollector._instance
            self._was_enabled = False
            self._initial_files: t.Set[str] = set()

        def __enter__(self) -> "FastModuleCodeCollector.CollectInContext":
            if self._collector:
                self._was_enabled = self._collector._coverage_enabled
                self._collector._coverage_enabled = True
                # Remember files that were already covered before this context
                self._initial_files = set(self._collector._tracked_files)
            return self

        def __exit__(self, exc_type: t.Any, exc_val: t.Any, exc_tb: t.Any) -> None:
            if self._collector:
                self._collector._coverage_enabled = self._was_enabled

        def get_covered_lines(self) -> t.Dict[str, CoverageLines]:
            """Get covered lines - matches ModuleCodeCollector interface."""
            if self._collector:
                # Return all covered files (not just the ones from this context)
                # This matches the behavior of the regular ModuleCodeCollector
                return self._collector.get_fast_coverage_data()
            return {}
