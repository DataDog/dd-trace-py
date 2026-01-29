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

# Coverage instance and percentage cache
_coverage_instance: Optional[Any] = None
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
    wrapt.wrap_function_wrapper(coverage, "Coverage.report", coverage_report_wrapper)


def unpatch():
    """Remove Coverage.py patching."""
    if coverage is None or not getattr(coverage, "_datadog_patch", False):
        return

    _u(coverage.Coverage, "report")
    coverage._datadog_patch = False


def coverage_report_wrapper(func: Any, instance: Any, args: tuple, kwargs: dict) -> Any:
    """Wrapper to cache percentage when report() is called."""
    global _cached_coverage_percentage
    pct_covered = func(*args, **kwargs)
    _cached_coverage_percentage = pct_covered
    return pct_covered


def generate_coverage_report(format_type: str = "text", cov: Optional[Any] = None, **kwargs: Any) -> Optional[float]:
    """
    Generate a coverage report in the specified format.

    Args:
        format_type: Type of report to generate ("text", "lcov")
        cov: Coverage instance (defaults to current instance)
        **kwargs: Additional arguments for the report method

    Returns:
        Coverage percentage, or None if unavailable
    """
    global _cached_coverage_percentage

    if coverage is None:
        return None

    if cov is None:
        cov = get_coverage_instance()

    if cov is None:
        log.debug("No coverage instance available")
        return None

    try:
        if format_type == "lcov":
            pct_covered = cov.lcov_report(**kwargs)
        else:  # Default to text report
            pct_covered = cov.report(**kwargs)

        if pct_covered is not None:
            _cached_coverage_percentage = pct_covered
        return pct_covered
    except Exception as e:
        log.warning("An exception occurred when running a coverage report: %s", e)
        return None


def run_coverage_report() -> None:
    """Run a coverage report on the current instance (backward compatibility)."""
    generate_coverage_report("text")


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
    global _coverage_instance

    if Coverage is None:
        log.debug("coverage.py is not available")
        return None

    # Check if coverage is already running
    try:
        existing = Coverage.current()
        if existing is not None:
            log.debug("Coverage is already running")
            set_coverage_instance(existing)
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
        set_coverage_instance(cov)
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
            reset_coverage_state()
        log.debug("Stopped coverage (save=%s, erase=%s)", save, erase)
        return cov
    except Exception as e:
        log.warning("Failed to stop coverage: %s", e, exc_info=True)
        return None


def get_coverage_instance() -> Optional[Any]:
    """
    Get the current Coverage instance.

    Returns:
        The Coverage instance, or None if not available
    """
    if coverage is None:
        return None

    # Return cached instance if available
    if _coverage_instance is not None:
        return _coverage_instance

    # Fall back to checking for an external running instance
    try:
        return Coverage.current()
    except Exception as e:
        log.debug("Failed to get coverage instance: %s", e)
        return None


def set_coverage_instance(cov_instance: Any) -> None:
    """
    Set the current coverage instance.

    Args:
        cov_instance: The coverage instance to set
    """
    global _coverage_instance
    _coverage_instance = cov_instance
    log.debug("Set coverage instance")


def reset_coverage_state() -> None:
    """
    Reset all coverage state.
    """
    global _coverage_instance, _cached_coverage_percentage
    _coverage_instance = None
    _cached_coverage_percentage = None
    log.debug("Reset coverage state")


def clear_coverage_instance() -> None:
    """
    Clear the coverage instance (alias for reset_coverage_state for backward compatibility).
    """
    reset_coverage_state()


def is_coverage_running() -> bool:
    return get_coverage_instance() is not None


def generate_lcov_report(cov: Optional[Any] = None, **kwargs: Any) -> Optional[float]:
    return generate_coverage_report("lcov", cov, **kwargs)


def get_coverage_percentage() -> Optional[float]:
    """Get the cached coverage percentage from the last report."""
    return _cached_coverage_percentage


def get_coverage_data() -> Dict[str, Any]:
    """
    Get coverage metadata dict (for backwards compatibility).

    Returns a dict with PCT_COVERED_KEY if percentage is cached.
    """
    if _cached_coverage_percentage is not None:
        return {PCT_COVERED_KEY: _cached_coverage_percentage}
    return {}


def erase_coverage() -> None:
    """Erase all coverage data and clear the cache."""
    cov = get_coverage_instance()
    if cov:
        try:
            cov.erase()
        except Exception as e:
            log.warning("Failed to erase coverage: %s", e)

    # Clear cache since data is gone
    reset_coverage_state()
