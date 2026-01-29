from typing import Any
from typing import Dict
from typing import Optional

import wrapt

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.wrappers import unwrap as _u


try:
    import coverage
    from coverage import Coverage
except ImportError:
    coverage = None  # type: ignore[misc,assignment]
    Coverage = None  # type: ignore[misc,assignment]


log = get_logger(__name__)

# Constant for backwards compatibility
PCT_COVERED_KEY = "pct_covered"

# Simple caches
# - _last_coverage_instance: Keep reference to last instance for generating reports after stop()
# - _cached_coverage_percentage: Cache the last reported coverage percentage
_last_coverage_instance: Optional[Any] = None
# Global to track external coverage instances (e.g., from pytest-cov)
_external_coverage_instance: Optional[Any] = None
_cached_coverage_percentage: Optional[float] = None


def get_version() -> str:
    return ""


def _supported_versions() -> Dict[str, str]:
    return {"coverage": "*"}


def patch():
    """Patch Coverage.py to capture coverage percentage when reports are generated."""
    if coverage is None or getattr(coverage, "_datadog_patch", False):
        return

    coverage._datadog_patch = True
    wrapt.wrap_function_wrapper(coverage, "Coverage.report", report_total_pct_covered_wrapper)


def unpatch():
    """Remove Coverage.py patching."""
    if coverage is None or not getattr(coverage, "_datadog_patch", False):
        return

    _u(coverage.Coverage, "report")
    coverage._datadog_patch = False


def report_total_pct_covered_wrapper(func: Any, instance: Any, args: tuple, kwargs: dict) -> Any:
    """Wrapper to cache the percentage when report() is called."""
    global _cached_coverage_percentage
    pct_covered = func(*args, **kwargs)
    _cached_coverage_percentage = pct_covered
    return pct_covered


def run_coverage_report(force: bool = False) -> None:
    """Run a coverage report on the current instance and cache the percentage."""
    global _cached_coverage_percentage
    if coverage is None and not force:
        return
    try:
        current_coverage_object = coverage.Coverage.current()
        _cached_coverage_percentage = current_coverage_object.report()
    except Exception:
        log.warning("An exception occurred when running a coverage report")


# ============================================================================
# Simple wrappers around Coverage.py API
# ============================================================================


def start_coverage(
    source: Any = None,
    omit: Any = None,
    include: Any = None,
    config_file: Any = True,
    auto_data: bool = False,
    data_suffix: Optional[str] = None,
    **kwargs: Any,
) -> Optional[Any]:
    """
    Start coverage collection.

    Args:
        source: List of file paths or package names to measure
        omit: List of file paths to omit
        include: List of file paths to include
        config_file: Path to .coveragerc, or True for default, or False to disable
        auto_data: If True, save data automatically at exit
        data_suffix: Suffix for the data file
        **kwargs: Additional arguments for Coverage()

    Returns:
        The Coverage instance, or None if coverage.py is not available
    """
    global _last_coverage_instance

    if Coverage is None:
        log.debug("coverage.py is not available")
        return None

    # Check if coverage is already running
    try:
        existing = Coverage.current()
        if existing is not None:
            log.debug("Coverage is already running")
            _last_coverage_instance = existing
            return existing
    except Exception:
        log.debug("Failed to access running coverage", exc_info=True)

    try:
        cov = Coverage(
            source=source,
            omit=omit,
            include=include,
            config_file=config_file,
            auto_data=auto_data,
            data_suffix=data_suffix,
            **kwargs,
        )
        cov.start()
        _last_coverage_instance = cov
        log.debug("Started coverage collection")
        return cov
    except Exception as e:
        log.warning("Failed to start coverage: %s", e, exc_info=True)
        return None


def stop_coverage(save: bool = True, erase: bool = False) -> Optional[Any]:
    """
    Stop coverage collection.

    Args:
        save: If True, save the collected data
        erase: If True, erase the data after stopping

    Returns:
        The Coverage instance, or None if not running
    """
    global _last_coverage_instance

    if coverage is None:
        return None

    cov = get_coverage_instance()
    if cov is None:
        log.debug("No coverage instance is running")
        return None

    try:
        cov.stop()
        if save:
            cov.save()
        if erase:
            cov.erase()
            # Clear reference when data is erased
            _last_coverage_instance = None
        log.debug("Stopped coverage (save=%s, erase=%s)", save, erase)
        return cov
    except Exception as e:
        log.warning("Failed to stop coverage: %s", e, exc_info=True)
        return None


def get_coverage_instance() -> Optional[Any]:
    """
    Get the current Coverage instance.

    Returns our tracked instance first (whether running or stopped),
    then registered external instances (e.g., pytest-cov),
    or falls back to any externally running instance.
    This allows generating reports after stopping coverage.

    Returns:
        The Coverage instance, or None if not available
    """
    if coverage is None:
        return None

    # First check if we have a tracked instance (our instance, may be stopped)
    if _last_coverage_instance is not None:
        return _last_coverage_instance

    # Check for registered external instance (e.g., pytest-cov)
    if _external_coverage_instance is not None:
        return _external_coverage_instance

    # Fall back to checking for an external running instance (e.g., pytest-cov)
    try:
        return Coverage.current()
    except Exception as e:
        log.debug("Failed to get coverage instance: %s", e)
        return None


def register_external_coverage_instance(cov_instance: Any) -> None:
    """
    Register an external coverage instance (e.g., from pytest-cov).

    This allows ddtrace to detect and use coverage instances that were
    started by external tools like pytest-cov.

    Args:
        cov_instance: The coverage instance to register
    """
    global _external_coverage_instance
    _external_coverage_instance = cov_instance
    log.debug("Registered external coverage instance")


def unregister_external_coverage_instance() -> None:
    """
    Unregister the external coverage instance.
    """
    global _external_coverage_instance, _cached_coverage_percentage
    _external_coverage_instance = None
    _cached_coverage_percentage = None
    log.debug("Unregistered external coverage instance and cleared cached percentage")


def is_coverage_running() -> bool:
    return get_coverage_instance() is not None


def generate_lcov_report(cov: Optional[Any] = None, **kwargs: Any) -> Optional[float]:
    """
    Generate an LCOV coverage report.

    Args:
        cov: Coverage instance (defaults to current instance)
        outfile: Path to write the report
        **kwargs: Additional arguments for lcov_report()

    Returns:
        Coverage percentage, or None if unavailable
    """
    global _cached_coverage_percentage

    if cov is None:
        cov = get_coverage_instance()

    if cov is None:
        log.debug("No coverage instance available")
        return None

    pct_covered = cov.lcov_report(**kwargs)
    if pct_covered is not None:
        _cached_coverage_percentage = pct_covered
    return pct_covered


def get_coverage_percentage() -> Optional[float]:
    """Get the cached coverage percentage from the last report."""
    return _cached_coverage_percentage


def get_coverage_data(cov: Optional[Any] = None) -> Dict[str, Any]:
    """
    Get coverage metadata dict (for backwards compatibility).

    Returns a dict with PCT_COVERED_KEY if percentage is cached.
    """
    if _cached_coverage_percentage is not None:
        return {PCT_COVERED_KEY: _cached_coverage_percentage}
    return {}


def erase_coverage() -> None:
    """Erase all coverage data and clear the cache."""
    global _last_coverage_instance, _cached_coverage_percentage

    cov = get_coverage_instance()
    if cov:
        try:
            cov.erase()
        except Exception as e:
            log.warning("Failed to erase coverage: %s", e)

    # Clear both caches since data is gone
    _last_coverage_instance = None
    _cached_coverage_percentage = None
