"""Coverage collector factory - decides between fast and regular coverage."""

import os
import typing as t
from pathlib import Path

from ddtrace.internal.logger import get_logger

log = get_logger(__name__)


def _is_fast_coverage_enabled() -> bool:
    """Check if fast file-level coverage is enabled via environment variable."""
    env_value = os.environ.get('_DD_CIVISIBILITY_FAST_COVERAGE', '').lower()
    return env_value in ('1', 'true', 'yes', 'on')


def install_coverage_collector(
    include_paths: t.Optional[t.List[Path]] = None, 
    collect_import_time_coverage: bool = False
) -> None:
    """Install the appropriate coverage collector based on configuration.
    
    This is the single entry point for coverage collection. It decides whether
    to install fast file-level coverage or regular line-level coverage based
    on the _DD_CIVISIBILITY_FAST_COVERAGE environment variable.
    
    Args:
        include_paths: Paths to include in coverage collection
        collect_import_time_coverage: Whether to collect import-time coverage
    """
    if _is_fast_coverage_enabled():
        # Install fast file-level coverage
        from .fast import FastModuleCodeCollector
        FastModuleCodeCollector.install(include_paths, collect_import_time_coverage)
        log.info("Fast file-level coverage collector installed")
    else:
        # Install regular line-level coverage
        from .code import ModuleCodeCollector
        ModuleCodeCollector.install(include_paths, collect_import_time_coverage)
        log.info("Regular line-level coverage collector installed")


def get_coverage_collector():
    """Get the currently installed coverage collector instance.
    
    Returns:
        The active coverage collector instance, or None if none is installed.
    """
    if _is_fast_coverage_enabled():
        from .fast import FastModuleCodeCollector
        return FastModuleCodeCollector._instance
    else:
        from .code import ModuleCodeCollector
        return ModuleCodeCollector._instance


def is_coverage_collector_installed() -> bool:
    """Check if a coverage collector is currently installed."""
    collector = get_coverage_collector()
    return collector is not None


def start_coverage() -> None:
    """Start coverage collection."""
    if _is_fast_coverage_enabled():
        from .fast import FastModuleCodeCollector
        FastModuleCodeCollector.start_coverage()
    else:
        from .code import ModuleCodeCollector
        ModuleCodeCollector.start_coverage()


def stop_coverage() -> None:
    """Stop coverage collection."""
    if _is_fast_coverage_enabled():
        from .fast import FastModuleCodeCollector
        FastModuleCodeCollector.stop_coverage()
    else:
        from .code import ModuleCodeCollector
        ModuleCodeCollector.stop_coverage()


def uninstall_coverage_collector() -> None:
    """Uninstall the currently active coverage collector."""
    if _is_fast_coverage_enabled():
        from .fast import FastModuleCodeCollector
        if FastModuleCodeCollector.is_installed():
            FastModuleCodeCollector.uninstall()
    else:
        from .code import ModuleCodeCollector
        if ModuleCodeCollector.is_installed():
            ModuleCodeCollector.uninstall()


def get_coverage_context():
    """Get a coverage collection context manager.
    
    Returns:
        A context manager for collecting coverage data during execution.
    """
    if _is_fast_coverage_enabled():
        from .fast import FastModuleCodeCollector
        return FastModuleCodeCollector.CollectInContext()
    else:
        from .code import ModuleCodeCollector
        return ModuleCodeCollector.CollectInContext()


def report_coverage_data(workspace_path: Path, include_imported: bool = False):
    """Generate coverage report data.
    
    Args:
        workspace_path: The workspace root path for relative path calculation
        include_imported: Whether to include import-time coverage
        
    Returns:
        Coverage data in the format expected by CI Visibility
    """
    if _is_fast_coverage_enabled():
        from .fast import FastModuleCodeCollector
        return FastModuleCodeCollector.report_seen_lines(workspace_path, include_imported)
    else:
        from .code import ModuleCodeCollector
        return ModuleCodeCollector.report_seen_lines(workspace_path, include_imported)
