from pathlib import Path
import sys
import tempfile
from typing import Callable
from typing import List
from typing import Optional

from ddtrace.contrib.internal.coverage.data import _original_sys_argv_command
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
        from ddtrace.contrib.internal.coverage.patch import generate_lcov_report
        from ddtrace.contrib.internal.coverage.patch import is_coverage_running
        from ddtrace.contrib.internal.coverage.patch import set_coverage_instance

        log.debug("Coverage report upload is enabled, checking for coverage data")

        # If pytest-cov is enabled but coverage detection fails, register the pytest-cov instance
        if is_pytest_cov_enabled_func(session.config) and not is_coverage_running():
            # Try to get coverage from pytest-cov plugin and register it
            for plugin in session.config.pluginmanager.list_name_plugin():
                _, plugin_instance = plugin
                if hasattr(plugin_instance, "cov_controller") and plugin_instance.cov_controller:
                    set_coverage_instance(plugin_instance.cov_controller.cov)
                    log.debug("Registered pytest-cov coverage instance with ddtrace")
                    break

        # Now check if coverage is available (either ddtrace started or pytest-cov registered)
        if is_coverage_running():
            try:
                coverage_format = "lcov"  # Default to LCOV
                # Generate the report in a temporary file
                with tempfile.NamedTemporaryFile(mode="wb", suffix=".lcov", delete=False) as tmp_file:
                    tmp_path = Path(tmp_file.name)

                # Generate LCOV report. This returns the percentage and also stores it
                # in _coverage_data, so we don't need to generate a second report just for the percentage.
                pct_covered = generate_lcov_report(outfile=str(tmp_path))
                log.debug("Generated LCOV coverage report: %s (%.1f%% coverage)", tmp_path, pct_covered)

                # Read the report file
                coverage_report_bytes = tmp_path.read_bytes()
                log.debug("Read coverage report: %d bytes", len(coverage_report_bytes))

                # Upload the report using provided upload function
                upload_success = upload_func(coverage_report_bytes, coverage_format)

                if upload_success:
                    log.info("Successfully uploaded coverage report")
                else:
                    log.warning("Failed to upload coverage report")

                # Clean up temporary file
                try:
                    tmp_path.unlink()
                except Exception as e:
                    log.debug("Failed to clean up temporary coverage report file: %s", e)

                # Stop coverage AFTER generating and uploading the report (if we started it ourselves)
                if stop_coverage_func and not is_pytest_cov_enabled_func(session.config):
                    log.debug("Stopping coverage.py collection")
                    stop_coverage_func(save=True)

            except Exception as e:
                log.exception("Error generating or uploading coverage report: %s", e)
                # Still try to stop coverage even if report generation failed
                if stop_coverage_func and not is_pytest_cov_enabled_func(session.config):
                    try:
                        stop_coverage_func(save=True)
                    except Exception:
                        log.debug("Could not stop coverage after error", exc_info=True)
        else:
            log.debug("Coverage is not running, skipping coverage report upload")

    except Exception as e:
        log.exception("Error in coverage report upload handling: %s", e)
