from typing import Any
from typing import Dict
from typing import Optional

import wrapt

from ddtrace.contrib.internal.coverage.constants import PCT_COVERED_KEY
from ddtrace.contrib.internal.coverage.data import _coverage_data
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.wrappers import unwrap as _u


try:
    import coverage
    from coverage import Coverage
except ImportError:
    coverage = None  # type: ignore[misc,assignment]
    Coverage = None  # type: ignore[misc,assignment]


log = get_logger(__name__)

# AIDEV-NOTE: Module-level variable to store the managed coverage instance
# This allows us to track coverage instances we start on demand
_managed_coverage_instance: Optional[Any] = None


def get_version():
    # type: () -> str
    return ""


def _supported_versions() -> Dict[str, str]:
    return {"coverage": "*"}


def patch():
    """
    Patch the instrumented methods from Coverage.py
    """
    if coverage is None or getattr(coverage, "_datadog_patch", False):
        return

    coverage._datadog_patch = True

    _w = wrapt.wrap_function_wrapper

    _w(coverage, "Coverage.report", report_total_pct_covered_wrapper)


def unpatch():
    """
    Undo patched instrumented methods from Coverage.py
    """
    if coverage is None or not getattr(coverage, "_datadog_patch", False):
        return

    _u(coverage.Coverage, "report")

    coverage._datadog_patch = False


def report_total_pct_covered_wrapper(func, instance, args: tuple, kwargs: dict):
    pct_covered = func(*args, **kwargs)
    _coverage_data[PCT_COVERED_KEY] = pct_covered
    return pct_covered


def run_coverage_report(force=False):
    if coverage is None and not force:
        return
    try:
        current_coverage_object = coverage.Coverage.current()
        _coverage_data[PCT_COVERED_KEY] = current_coverage_object.report()
    except Exception:
        log.warning("An exception occurred when running a coverage report")


# ============================================================================
# Enhanced Coverage.py Integration API
# ============================================================================


def start_coverage(
    source=None,
    omit=None,
    include=None,
    config_file=True,
    auto_data=False,
    data_suffix=None,
    **kwargs,
):
    # type: (Optional[Any], Optional[Any], Optional[Any], bool, bool, Optional[str], Any) -> Optional[Any]
    """
    Start coverage collection on demand.

    This creates and starts a new Coverage instance that is managed by ddtrace.
    If coverage.py is already running (either started externally or by us),
    we return the existing instance instead.

    Args:
        source: List of file paths or package names to measure
        omit: List of file paths to omit from measurement
        include: List of file paths to include in measurement
        config_file: Path to .coveragerc file, or True to use default, or False to disable
        auto_data: If True, save data automatically at exit
        data_suffix: Suffix for the data file
        **kwargs: Additional arguments to pass to Coverage()

    Returns:
        The Coverage instance, or None if coverage.py is not available

    Example:
        >>> cov = start_coverage(source=["myapp"], omit=["*/tests/*"])
        >>> # ... run code to measure ...
        >>> stop_coverage()
    """
    global _managed_coverage_instance

    if Coverage is None:
        log.debug("coverage.py is not loaded, cannot start coverage collection")
        return None

    # Check if there's already a running instance
    existing_instance = get_coverage_instance()
    if existing_instance is not None:
        log.debug("Coverage is already running, returning existing instance")
        return existing_instance

    try:
        # Create a new Coverage instance with the provided configuration
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
        _managed_coverage_instance = cov
        log.debug("Started new coverage collection")
        return cov
    except Exception as e:
        log.warning("Failed to start coverage collection: %s", e, exc_info=True)
        return None


def stop_coverage(save=True, erase=False):
    # type: (bool, bool) -> Optional[Any]
    """
    Stop coverage collection.

    This stops the coverage instance we're managing. If coverage was started
    externally, this will still attempt to stop it.

    Args:
        save: If True, save the collected data to disk
        erase: If True, erase the data after stopping

    Returns:
        The Coverage instance that was stopped, or None if no instance was running

    Example:
        >>> stop_coverage(save=True)
    """
    global _managed_coverage_instance

    if coverage is None:
        log.debug("coverage.py is not loaded, cannot stop coverage collection")
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
        log.debug("Stopped coverage collection (save=%s, erase=%s)", save, erase)

        # Clear our managed instance reference
        if _managed_coverage_instance is not None:
            _managed_coverage_instance = None

        return cov
    except Exception as e:
        log.warning("Failed to stop coverage collection: %s", e, exc_info=True)
        return None


def get_coverage_instance():
    # type: () -> Optional[Any]
    """
    Get the currently running Coverage instance.

    This returns either:
    1. The instance we started via start_coverage()
    2. An externally started instance (e.g., via pytest-cov or `coverage run`)
    3. None if no coverage is running

    Returns:
        The Coverage instance, or None if no instance is running

    Example:
        >>> cov = get_coverage_instance()
        >>> if cov:
        ...     print(f"Coverage is running with data file: {cov.config.data_file}")
    """
    global _managed_coverage_instance

    if coverage is None:
        return None

    # First, check if we have a managed instance
    if _managed_coverage_instance is not None:
        return _managed_coverage_instance

    # Otherwise, try to get the current instance (may have been started externally)
    try:
        return Coverage.current()
    except Exception as e:
        log.debug("Failed to get current coverage instance: %s", e)
        return None


def get_coverage_data(cov=None):
    # type: (Optional[Any]) -> Optional[Any]
    """
    Get the coverage data object from a Coverage instance.

    This provides access to the raw coverage data, which can be used to
    inspect what lines were covered.

    Args:
        cov: Coverage instance, or None to use the current instance

    Returns:
        The CoverageData object, or None if not available

    Example:
        >>> data = get_coverage_data()
        >>> if data:
        ...     measured_files = data.measured_files()
        ...     for filename in measured_files:
        ...         lines = data.lines(filename)
        ...         print(f"{filename}: {len(lines)} lines covered")
    """
    if cov is None:
        cov = get_coverage_instance()

    if cov is None:
        log.debug("No coverage instance available")
        return None

    try:
        return cov.get_data()
    except Exception as e:
        log.warning("Failed to get coverage data: %s", e, exc_info=True)
        return None


def generate_coverage_report(
    cov=None,
    morfs=None,
    show_missing=None,
    ignore_errors=None,
    file=None,
    omit=None,
    include=None,
    skip_covered=None,
    contexts=None,
    skip_empty=None,
    precision=None,
    sort=None,
):
    # type: (...) -> Optional[float]
    """
    Generate a text coverage report.

    Args:
        cov: Coverage instance, or None to use the current instance
        morfs: List of file paths or modules to report on
        show_missing: Show line numbers of statements that weren't executed
        ignore_errors: Ignore errors while reporting
        file: File object to write the report to (default: stdout)
        omit: List of file paths to omit from the report
        include: List of file paths to include in the report
        skip_covered: Skip files with 100% coverage
        contexts: List of contexts to include in the report
        skip_empty: Skip files with no executable code
        precision: Number of decimal places to display for percentages
        sort: Sort order for the report (default: name)

    Returns:
        The total percentage covered, or None on error

    Example:
        >>> pct = generate_coverage_report(show_missing=True, skip_covered=True)
        >>> print(f"Coverage: {pct:.1f}%")
    """
    if cov is None:
        cov = get_coverage_instance()

    if cov is None:
        log.debug("No coverage instance available for report generation")
        return None

    try:
        # Build kwargs dict, only including non-None values
        kwargs = {}
        if morfs is not None:
            kwargs["morfs"] = morfs
        if show_missing is not None:
            kwargs["show_missing"] = show_missing
        if ignore_errors is not None:
            kwargs["ignore_errors"] = ignore_errors
        if file is not None:
            kwargs["file"] = file
        if omit is not None:
            kwargs["omit"] = omit
        if include is not None:
            kwargs["include"] = include
        if skip_covered is not None:
            kwargs["skip_covered"] = skip_covered
        if contexts is not None:
            kwargs["contexts"] = contexts
        if skip_empty is not None:
            kwargs["skip_empty"] = skip_empty
        if precision is not None:
            kwargs["precision"] = precision
        if sort is not None:
            kwargs["sort"] = sort

        pct_covered = cov.report(**kwargs)
        _coverage_data[PCT_COVERED_KEY] = pct_covered
        return pct_covered
    except Exception as e:
        log.warning("Failed to generate coverage report: %s", e, exc_info=True)
        return None


def generate_lcov_report(cov=None, outfile=None, **kwargs):
    # type: (Optional[Any], Optional[str], Any) -> Optional[float]
    """
    Generate an LCOV coverage report.

    Args:
        cov: Coverage instance, or None to use the current instance
        outfile: Path to write the LCOV report to (default: coverage.lcov)
        **kwargs: Additional arguments to pass to cov.lcov_report()

    Returns:
        The total percentage covered, or None on error

    Example:
        >>> pct = generate_lcov_report(outfile="coverage.lcov")
        >>> print(f"Coverage: {pct:.1f}%")
    """
    if cov is None:
        cov = get_coverage_instance()

    if cov is None:
        log.debug("No coverage instance available for LCOV report generation")
        return None

    try:
        if outfile is not None:
            kwargs["outfile"] = outfile

        pct_covered = cov.lcov_report(**kwargs)
        # AIDEV-NOTE: Store percentage in _coverage_data for get_coverage_percentage() to retrieve
        if pct_covered is not None:
            _coverage_data[PCT_COVERED_KEY] = pct_covered
        return pct_covered
    except Exception as e:
        log.warning("Failed to generate LCOV coverage report: %s", e, exc_info=True)
        return None


def is_coverage_running():
    # type: () -> bool
    """
    Check if coverage collection is currently running.

    Returns:
        True if coverage is running, False otherwise

    Example:
        >>> if is_coverage_running():
        ...     print("Coverage is active")
    """
    return get_coverage_instance() is not None
