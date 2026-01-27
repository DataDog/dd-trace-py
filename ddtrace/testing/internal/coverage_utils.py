"""
Coverage utilities for pytest-cov integration and coverage report uploads.

This module provides a clean interface for coverage functionality that can be used
by both the new and old pytest plugins.
"""

from __future__ import annotations

import logging
import typing as t

from ddtrace.testing.internal.coverage_report_uploader import CoverageReportUploader
from ddtrace.testing.internal.v2_plugin_coverage_telemetry import V2PluginCoverageTelemetryRecorder


log = logging.getLogger(__name__)


def is_pytest_cov_enabled(config) -> bool:
    """
    Check if pytest-cov is enabled (--cov flag is present).

    Args:
        config: pytest Config object

    Returns:
        True if pytest-cov is enabled, False otherwise
    """
    if not hasattr(config, "pluginmanager") or not hasattr(config, "getoption"):
        return False

    try:
        # Check if pytest-cov plugin is loaded
        if not config.pluginmanager.get_plugin("pytest_cov"):
            return False

        # Check command line options
        cov_option = config.getoption("--cov", default=False)
        nocov_option = config.getoption("--no-cov", default=False)

        if nocov_option is True:
            return False

        if isinstance(cov_option, list) and cov_option == [True] and not nocov_option:
            return True

        return bool(cov_option)
    except (AttributeError, ValueError):
        # --cov option not available or not used
        return False


def capture_pytest_cov_instance(session) -> t.Optional[t.Any]:
    """
    Capture the pytest-cov coverage instance from the pytest session.

    Args:
        session: pytest Session object

    Returns:
        coverage.Coverage instance if available, None otherwise
    """
    if not is_pytest_cov_enabled(session.config):
        log.debug("pytest-cov not enabled, cannot capture coverage instance")
        return None

    try:
        if session.config.pluginmanager.hasplugin("_cov"):
            cov_plugin = session.config.pluginmanager.getplugin("_cov")
            if cov_plugin and hasattr(cov_plugin, "cov_controller") and cov_plugin.cov_controller:
                coverage_instance = cov_plugin.cov_controller.cov
                log.debug("Successfully captured pytest-cov coverage instance")
                return coverage_instance
            else:
                log.debug("pytest-cov plugin found but no coverage controller available")
        else:
            log.debug("pytest-cov enabled but _cov plugin not found in plugin manager")
    except Exception:
        log.exception("Failed to capture pytest-cov coverage instance")

    return None


def upload_coverage_report_if_enabled(
    coverage_report_uploader: t.Optional[CoverageReportUploader], coverage_instance: t.Optional[t.Any], session_config
) -> bool:
    """
    Upload coverage report if conditions are met.

    Args:
        coverage_report_uploader: Coverage report uploader instance, or None if disabled
        coverage_instance: coverage.Coverage instance from pytest-cov, or None
        session_config: pytest Config object for additional validation

    Returns:
        True if upload was attempted, False if skipped
    """
    # Check if upload is enabled
    if coverage_report_uploader is None:
        log.debug("Coverage report uploader not configured, skipping upload")
        return False

    # Check if pytest-cov is being used
    if not is_pytest_cov_enabled(session_config):
        log.debug("Coverage report upload requires --cov flag, skipping upload")
        return False

    # Check if coverage instance is available
    if coverage_instance is None:
        log.debug("No coverage.py instance available, skipping upload")
        return False

    # All conditions met, trigger upload
    log.debug(
        "Starting coverage report upload with uploader: %s, instance: %s",
        coverage_report_uploader,
        type(coverage_instance),
    )
    try:
        coverage_report_uploader.upload_coverage_report(cov_instance=coverage_instance)
        log.debug("Coverage report upload completed successfully")
        return True
    except Exception as e:
        log.debug("Coverage report upload failed: %s", e)
        return False


class CoverageManager:
    """
    Encapsulates all coverage-related functionality for pytest plugins.

    This class provides a simple interface for both old and new plugins to:
    1. Detect pytest-cov usage
    2. Capture coverage instances
    3. Upload coverage reports
    """

    def __init__(self):
        self.coverage_instance: t.Optional[t.Any] = None
        self._is_cov_enabled: t.Optional[bool] = None

    def setup_coverage_for_session(self, session) -> None:
        """
        Set up coverage for the pytest session.

        This should be called during pytest_sessionstart.

        Args:
            session: pytest Session object
        """
        self._is_cov_enabled = is_pytest_cov_enabled(session.config)

        if self._is_cov_enabled:
            self.coverage_instance = capture_pytest_cov_instance(session)
            if self.coverage_instance:
                log.debug("Coverage manager: pytest-cov instance captured successfully")
            else:
                log.debug("Coverage manager: pytest-cov enabled but instance capture failed")
        else:
            log.debug("Coverage manager: pytest-cov not enabled")

    def is_coverage_enabled(self) -> bool:
        """Check if coverage collection is enabled."""
        return self._is_cov_enabled or False

    def has_coverage_instance(self) -> bool:
        """Check if we have a valid coverage instance."""
        return self.coverage_instance is not None

    def upload_coverage_report(
        self, coverage_report_uploader: t.Optional[CoverageReportUploader], session_config
    ) -> bool:
        """
        Upload coverage report if conditions are met.

        This should be called during pytest_sessionfinish.

        Args:
            coverage_report_uploader: Coverage report uploader instance
            session_config: pytest Config object

        Returns:
            True if upload was attempted, False if skipped
        """
        return upload_coverage_report_if_enabled(
            coverage_report_uploader=coverage_report_uploader,
            coverage_instance=self.coverage_instance,
            session_config=session_config,
        )

    def cleanup(self) -> None:
        """Clean up coverage resources."""
        self.coverage_instance = None
        self._is_cov_enabled = None


# Simple functions for legacy plugin compatibility
def setup_coverage_for_session(session) -> t.Optional[t.Any]:
    """
    Simple function to set up coverage for a pytest session.

    This is designed to be called from any pytest plugin.

    Args:
        session: pytest Session object

    Returns:
        coverage.Coverage instance if available, None otherwise
    """
    if is_pytest_cov_enabled(session.config):
        return capture_pytest_cov_instance(session)
    return None


def upload_coverage_if_available(
    session,
    coverage_instance: t.Optional[t.Any] = None,
    coverage_report_uploader: t.Optional[CoverageReportUploader] = None,
    env_tags: t.Optional[t.Dict[str, str]] = None,
) -> bool:
    """
    Simple function to upload coverage if all conditions are met.

    This is designed to be called from any pytest plugin.

    Args:
        session: pytest Session object
        coverage_instance: Optional coverage instance (will auto-detect if None)
        coverage_report_uploader: Optional uploader instance (will create if None)
        env_tags: Optional environment tags dict (each plugin should pass their own)

    Returns:
        True if upload was attempted, False if skipped
    """
    # Auto-detect coverage instance if not provided
    if coverage_instance is None:
        coverage_instance = setup_coverage_for_session(session)

    # Create coverage report uploader if not provided (backward compatibility)
    if coverage_report_uploader is None and coverage_instance is not None:
        try:
            import os

            from ddtrace.testing.internal.git import get_workspace_path
            from ddtrace.testing.internal.http import BackendConnectorSetup

            # Create a connector setup
            connector_setup = BackendConnectorSetup.detect_setup()
            log.debug("Created connector setup for coverage upload")

            workspace_path = get_workspace_path()

            # Use the provided env_tags, or create minimal fallback
            if env_tags is None:
                log.debug("No env_tags provided to upload_coverage_if_available, using minimal fallback")
                env_tags = {"env": os.environ.get("DD_ENV", "none")}
            else:
                log.debug(
                    "Using provided env_tags with %d total tags (%d git tags)",
                    len(env_tags),
                    len([k for k in env_tags if k.startswith("git.")]),
                )

            coverage_report_uploader = CoverageReportUploader(
                connector_setup=connector_setup,
                env_tags=env_tags,
                skippable_coverage={},  # Backward compatibility: no ITR coverage data
                workspace_path=workspace_path,
                telemetry_recorder=V2PluginCoverageTelemetryRecorder(),
            )
            log.debug("Created coverage report uploader: %s", coverage_report_uploader)
        except Exception as e:
            log.exception("Failed to create coverage report uploader: %s", e)
            return False

    return upload_coverage_report_if_enabled(
        coverage_report_uploader=coverage_report_uploader,
        coverage_instance=coverage_instance,
        session_config=session.config,
    )
