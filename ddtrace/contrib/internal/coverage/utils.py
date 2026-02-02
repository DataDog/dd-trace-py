from pathlib import Path
import sys
import tempfile
from typing import Callable
from typing import List
from typing import Optional

from ddtrace.contrib.internal.coverage.data import _original_sys_argv_command
from ddtrace.contrib.internal.coverage.patch import is_coverage_running
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._config import _get_config
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)


def is_coverage_loaded() -> bool:
    return "coverage" in sys.modules


def _is_coverage_patched():
    if not is_coverage_loaded():
        return False

    return getattr(sys.modules["coverage"], "_datadog_patch", False)


def _command_invokes_coverage_run(sys_argv_command: List[str]) -> bool:
    return "coverage run -m" in " ".join(sys_argv_command)


def _is_coverage_invoked_by_coverage_run() -> bool:
    if _get_config("COVERAGE_RUN", False, asbool):
        return True
    return _command_invokes_coverage_run(_original_sys_argv_command)


def _find_pytest_cov_instance(session):
    """Find and return pytest-cov coverage instance if available."""
    for plugin in session.config.pluginmanager.list_name_plugin():
        _, plugin_instance = plugin
        if hasattr(plugin_instance, "cov_controller") and plugin_instance.cov_controller:
            if hasattr(plugin_instance.cov_controller, "cov") and plugin_instance.cov_controller.cov:
                return plugin_instance.cov_controller.cov
    return None


def _register_pytest_cov_instance(session):
    """Register pytest-cov instance with ddtrace if available."""
    from ddtrace.contrib.internal.coverage.patch import set_coverage_instance

    cov_instance = _find_pytest_cov_instance(session)
    if cov_instance:
        set_coverage_instance(cov_instance)
        log.debug("Registered pytest-cov coverage instance with ddtrace: %s", type(cov_instance))
        return True

    log.debug("No pytest-cov controller found in plugin manager")
    return False


def _save_pytest_cov_data(session):
    """Save pytest-cov data before report generation."""
    cov_instance = _find_pytest_cov_instance(session)
    if not cov_instance:
        return

    try:
        # Save coverage data
        if hasattr(cov_instance, "save"):
            cov_instance.save()
            log.debug("Saved pytest-cov coverage data before report generation")

        # Stop collection if still running
        if hasattr(cov_instance, "_started") and cov_instance._started:
            if hasattr(cov_instance, "stop"):
                cov_instance.stop()
                log.debug("Stopped pytest-cov coverage collection")

    except Exception as save_error:
        log.debug("Could not save pytest-cov data: %s", save_error)


def _generate_lcov_report(session, tmp_path, is_pytest_cov_enabled_func):
    """Generate LCOV report using either pytest-cov or ddtrace."""
    from ddtrace.contrib.internal.coverage.patch import generate_lcov_report

    log.debug("Generating LCOV report to file: %s", tmp_path)

    if is_pytest_cov_enabled_func(session.config):
        # Try pytest-cov instance directly first
        pytest_cov_instance = _find_pytest_cov_instance(session)
        if pytest_cov_instance:
            log.debug("Using pytest-cov instance directly to generate LCOV report")

            # Check if coverage has data in memory
            has_data = False
            try:
                data = pytest_cov_instance.get_data()
                has_data = bool(data) and len(data) > 0
                log.debug("pytest-cov has data in memory: %s", has_data)
            except Exception as e:
                log.debug("Could not check pytest-cov data: %s", e)

            # If no data in memory, try loading from .coverage file
            if not has_data:
                try:
                    log.debug("No data in memory, attempting to load from .coverage file")
                    from coverage import Coverage

                    fresh_cov = Coverage(data_file=".coverage")
                    fresh_cov.load()
                    # Use this instance instead
                    pytest_cov_instance = fresh_cov
                    log.debug("Loaded data from .coverage file")
                except Exception as load_error:
                    log.debug("Could not load .coverage file: %s", load_error)

            try:
                pct_covered = pytest_cov_instance.lcov_report(outfile=str(tmp_path))
                log.debug("Generated LCOV report directly from pytest-cov instance")
                return pct_covered
            except Exception as direct_error:
                log.debug("Direct pytest-cov report generation failed: %s", direct_error)
                # Fall back to ddtrace method

    # Use ddtrace method
    return generate_lcov_report(outfile=str(tmp_path))


def _validate_and_read_report(tmp_path):
    """Validate report file exists and read its contents."""
    if not tmp_path.exists():
        log.warning("LCOV report file was not created: %s", tmp_path)
        return None

    coverage_report_bytes = tmp_path.read_bytes()
    if not coverage_report_bytes:
        log.warning("LCOV report file is empty: %s", tmp_path)
        # Clean up empty file
        _cleanup_temp_file(tmp_path)
        return None

    log.debug("Read coverage report: %d bytes", len(coverage_report_bytes))
    return coverage_report_bytes


def _cleanup_temp_file(tmp_path):
    """Clean up temporary coverage report file."""
    try:
        tmp_path.unlink()
    except Exception as e:
        log.debug("Failed to clean up temporary coverage report file: %s", e)


def _stop_coverage_if_needed(stop_coverage_func, session, is_pytest_cov_enabled_func):
    """Stop coverage collection if we started it ourselves (not pytest-cov)."""
    if stop_coverage_func and not is_pytest_cov_enabled_func(session.config):
        log.debug("Stopping coverage.py collection")
        stop_coverage_func(save=True)


def handle_coverage_report(
    session,
    upload_func: Callable[[bytes, str], bool],
    is_pytest_cov_enabled_func: Callable,
    stop_coverage_func: Optional[Callable] = None,
) -> None:
    """
    Shared coverage report upload handling for pytest plugins.

    Args:
        session: pytest session object
        upload_func: Function to call for uploading (signature: upload_func(bytes, format) -> bool)
        is_pytest_cov_enabled_func: Function to check if pytest-cov is enabled
        stop_coverage_func: Optional function to stop coverage collection
    """
    try:
        log.debug("Coverage report upload is enabled, checking for coverage data")

        # Register pytest-cov instance if needed
        if is_pytest_cov_enabled_func(session.config) and not is_coverage_running():
            log.debug("pytest-cov is enabled but coverage not running, trying to register pytest-cov instance")
            _register_pytest_cov_instance(session)

        # Check if coverage is available
        if not is_coverage_running():
            log.debug("Coverage is not running, skipping coverage report upload")
            return

        log.debug("Coverage is running, attempting to generate coverage report")

        # Save pytest-cov data if using pytest-cov
        if is_pytest_cov_enabled_func(session.config):
            _save_pytest_cov_data(session)

        # Generate and upload report
        coverage_format = "lcov"
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".lcov", delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)

        try:
            # Generate LCOV report
            pct_covered = _generate_lcov_report(session, tmp_path, is_pytest_cov_enabled_func)
            if pct_covered is not None:
                log.debug("Generated LCOV coverage report: %s (%.1f%% coverage)", tmp_path, pct_covered)
            else:
                log.debug("Generated LCOV coverage report: %s (coverage percentage unavailable)", tmp_path)

        except Exception as report_error:
            # Handle "No data to report" and other coverage errors
            if "No data to report" in str(report_error):
                log.debug("No coverage data available to generate LCOV report: %s", report_error)
                _cleanup_temp_file(tmp_path)
                return
            else:
                # Re-raise unexpected errors
                raise

        try:
            # Read and validate report
            coverage_report_bytes = _validate_and_read_report(tmp_path)
            if not coverage_report_bytes:
                return

            # Upload the report
            upload_success = upload_func(coverage_report_bytes, coverage_format)
            if upload_success:
                log.debug("Successfully uploaded coverage report")
            else:
                log.debug("Failed to upload coverage report")

        finally:
            # Always clean up temp file
            _cleanup_temp_file(tmp_path)
            # Stop coverage after upload (if we started it)
            _stop_coverage_if_needed(stop_coverage_func, session, is_pytest_cov_enabled_func)

    except Exception as e:
        log.debug("Error in coverage report upload handling: %s", e)
        # Still try to stop coverage even if report generation failed
        if stop_coverage_func and not is_pytest_cov_enabled_func(session.config):
            try:
                stop_coverage_func(save=True)
            except Exception:
                log.debug("Could not stop coverage after error", exc_info=True)
