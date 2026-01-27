"""
Consolidated coverage integration for pytest plugins.

This module provides a simplified interface for coverage collection, report generation,
and upload functionality that can be shared between V2 and V3 pytest plugins.
"""

from __future__ import annotations

import gzip
import io
import json
import os
from pathlib import Path
import time
import typing as t

from ddtrace.contrib.internal.coverage.constants import COVERAGE_INTAKE_ENDPOINT
from ddtrace.contrib.internal.coverage.constants import PCT_COVERED_KEY
from ddtrace.contrib.internal.coverage.data import _coverage_data
from ddtrace.contrib.internal.coverage.patch import run_coverage_report
from ddtrace.contrib.internal.coverage.utils import _is_coverage_invoked_by_coverage_run
from ddtrace.contrib.internal.coverage.utils import _is_coverage_patched
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool
from ddtrace.testing.internal.coverage_telemetry_constants import CODE_COVERAGE_FILES
from ddtrace.testing.internal.coverage_telemetry_constants import COVERAGE_UPLOAD_REQUEST
from ddtrace.testing.internal.coverage_telemetry_constants import COVERAGE_UPLOAD_REQUEST_BYTES
from ddtrace.testing.internal.coverage_telemetry_constants import COVERAGE_UPLOAD_REQUEST_ERRORS
from ddtrace.testing.internal.coverage_telemetry_constants import COVERAGE_UPLOAD_REQUEST_MS


log = get_logger(__name__)

# Supported coverage report formats
COVERAGE_FORMATS = {
    "lcov": "application/text",
    "cobertura": "application/xml",
    "jacoco": "application/xml",
    "clover": "application/xml",
    "opencover": "application/xml",
    "simplecov": "application/json",
}


def is_coverage_upload_enabled() -> bool:
    """Check if coverage upload is enabled via environment variable."""
    env_var_value = os.environ.get("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "true")
    enabled = asbool(env_var_value)
    log.debug("Coverage upload enabled: %s (from env var: %s)", enabled, env_var_value)
    return enabled


def capture_coverage_instance_from_pytest_cov(session) -> t.Optional[t.Any]:
    """Capture coverage.py instance from pytest-cov plugin."""
    try:
        # Handle both V2 (config object) and V3 (session object) plugin interfaces
        if hasattr(session, "config") and hasattr(session.config, "pluginmanager"):
            # V3 plugin: session.config.pluginmanager
            pluginmanager = session.config.pluginmanager
        elif hasattr(session, "pluginmanager"):
            # V2 plugin: session.pluginmanager (session is actually config)
            pluginmanager = session.pluginmanager
        else:
            log.debug("Could not find pluginmanager in session object")
            return None

        pytest_cov_plugin = pluginmanager.get_plugin("pytest_cov")
        if not pytest_cov_plugin:
            return None

        # Try different ways to access coverage instance
        if hasattr(pytest_cov_plugin, "cov_controller") and pytest_cov_plugin.cov_controller:
            return pytest_cov_plugin.cov_controller.cov
        elif hasattr(pytest_cov_plugin, "cov"):
            return pytest_cov_plugin.cov
        elif hasattr(pytest_cov_plugin, "_cov"):
            return pytest_cov_plugin._cov

        log.debug("Could not find coverage instance in pytest-cov plugin")
        return None

    except Exception:
        log.debug("Failed to capture coverage instance", exc_info=True)
        return None


class CoverageIntegration:
    """Simplified coverage integration for pytest plugins."""

    def __init__(self, telemetry_writer=None, session_manager=None, env_tags=None):
        self.telemetry_writer = telemetry_writer
        self.session_manager = session_manager
        self.env_tags = env_tags or {}
        self._coverage_instance = None
        self._initialized = False
        self._coverage_report_uploader = None

    def initialize(self):
        """Initialize the coverage integration."""
        if self._initialized:
            return

        self._record_telemetry("coverage.started", 1, {"library": "coveragepy", "framework": "pytest"})
        self._initialized = True

    def handle_session_finish(self, config, session_span=None):
        """Handle pytest session finish - extract and set coverage percentage."""
        if not self._initialized:
            return

        # Handle existing percentage logic
        invoked_by_coverage_run = _is_coverage_invoked_by_coverage_run()
        pytest_cov_enabled = self._is_pytest_cov_enabled(config)
        coverage_patched = _is_coverage_patched()

        log.debug(
            "Coverage session finish - patched: %s, pytest-cov: %s, coverage run: %s",
            coverage_patched,
            pytest_cov_enabled,
            invoked_by_coverage_run,
        )

        if coverage_patched and (pytest_cov_enabled or invoked_by_coverage_run):
            if invoked_by_coverage_run and not pytest_cov_enabled:
                run_coverage_report()

            lines_pct_value = _coverage_data.get(PCT_COVERED_KEY, None)

            if lines_pct_value is None:
                log.debug("No coverage data available")
                self._record_telemetry("coverage.empty", 1)
            elif isinstance(lines_pct_value, (float, int)):
                log.debug("Setting coverage percentage: %.2f%%", lines_pct_value)
                if session_span:
                    session_span.set_tag("test.code_coverage.lines_pct", lines_pct_value)
                elif self.session_manager and hasattr(self.session_manager, "set_covered_lines_pct"):
                    self.session_manager.set_covered_lines_pct(lines_pct_value)
            else:
                log.warning("Unexpected coverage percentage format: %r", lines_pct_value)

        # Handle coverage report upload if enabled
        if is_coverage_upload_enabled():
            self._coverage_instance = capture_coverage_instance_from_pytest_cov(config)
            if self._coverage_instance:
                self._setup_and_upload_coverage_report()

        self._record_telemetry("coverage.finished", 1, {"library": "coveragepy", "framework": "pytest"})

    def _setup_and_upload_coverage_report(self):
        """Upload coverage report using existing plugin infrastructure."""
        try:
            if self.session_manager:
                # V3 plugin: Use existing coverage_writer infrastructure
                self._upload_via_v3_infrastructure()
            else:
                # V2 plugin: Use existing telemetry infrastructure
                self._upload_via_v2_infrastructure()

        except Exception:
            log.exception("Failed to upload coverage report")

    def _upload_via_v3_infrastructure(self):
        """Upload coverage report using V3 plugin's existing infrastructure."""
        try:
            # Use the existing coverage_writer from session manager
            if not hasattr(self.session_manager, "coverage_writer"):
                log.debug("V3 plugin: No coverage_writer available")
                return

            coverage_writer = self.session_manager.coverage_writer
            connector = coverage_writer.connector

            # Generate LCOV report
            lcov_data = self._generate_lcov_report_v3()
            if not lcov_data:
                log.debug("V3 plugin: No LCOV data to upload")
                return

            # Upload using existing connector infrastructure
            self._upload_coverage_report_via_connector(connector, lcov_data, "lcov")
            log.debug("V3 plugin: Coverage report uploaded successfully")

        except Exception:
            log.exception("V3 plugin: Failed to upload coverage report")

    def _upload_via_v2_infrastructure(self):
        """Upload coverage report using V2 plugin's existing infrastructure."""
        try:
            # For V2 plugin, we need to use the CI Visibility service
            from ddtrace.internal.ci_visibility.service_registry import require_ci_visibility_service

            ci_service = require_ci_visibility_service()
            if not hasattr(ci_service, "_agent_connector"):
                log.debug("V2 plugin: No agent connector available")
                return

            connector = ci_service._agent_connector

            # Generate LCOV report
            lcov_data = self._generate_lcov_report_v2()
            if not lcov_data:
                log.debug("V2 plugin: No LCOV data to upload")
                return

            # Upload using existing connector infrastructure
            self._upload_coverage_report_via_connector(connector, lcov_data, "lcov")
            log.debug("V2 plugin: Coverage report uploaded successfully")

        except Exception:
            log.exception("V2 plugin: Failed to upload coverage report")

    def _upload_coverage_report_via_connector(self, connector, lcov_data: bytes, report_format: str):
        """Upload coverage report using the provided connector."""
        try:
            import gzip
            import io
            import json

            from ddtrace.testing.internal.http import FileAttachment

            # Compress the LCOV data
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
                gz.write(lcov_data)
            compressed_data = buf.getvalue()

            # Create event JSON with git and CI tags
            event = self._create_coverage_report_event(report_format)
            event_json = json.dumps(event, separators=(",", ":")).encode("utf-8")

            # Create file attachments
            files = [
                FileAttachment(
                    name="coverage",
                    filename=f"coverage.{report_format}.gz",
                    content_type="application/gzip",
                    data=compressed_data,
                ),
                FileAttachment(
                    name="event",
                    filename="event.json",
                    content_type="application/json",
                    data=event_json,
                ),
            ]

            # Record telemetry
            total_size = len(compressed_data) + len(event_json)
            self._record_telemetry(COVERAGE_UPLOAD_REQUEST, 1, {"format": report_format})
            self._record_telemetry(COVERAGE_UPLOAD_REQUEST_BYTES, total_size)

            # Upload to coverage report intake endpoint
            log.debug("Uploading %d bytes coverage report to /api/v2/cicovreprt", total_size)
            start_time = time.time()

            result = connector.post_files(
                "/api/v2/cicovreprt",
                files=files,
                send_gzip=False,  # Already compressed
            )

            duration_ms = (time.time() - start_time) * 1000

            if hasattr(result, "error_type") and result.error_type:
                log.error("Coverage upload failed: %s", result.error_type)
                self._record_telemetry(COVERAGE_UPLOAD_REQUEST_ERRORS, 1, {"error": result.error_type})
            else:
                log.debug("Coverage upload successful in %.2fms", duration_ms)
                self._record_telemetry(COVERAGE_UPLOAD_REQUEST_MS, duration_ms)

        except Exception:
            log.exception("Exception during coverage report upload")
            self._record_telemetry(COVERAGE_UPLOAD_REQUEST_ERRORS, 1, {"error": "exception"})

    def _generate_lcov_report_v3(self) -> t.Optional[bytes]:
        """Generate LCOV report for V3 plugin."""
        return self._generate_lcov_report_common()

    def _generate_lcov_report_v2(self) -> t.Optional[bytes]:
        """Generate LCOV report for V2 plugin."""
        return self._generate_lcov_report_common()

    def _generate_lcov_report_common(self) -> t.Optional[bytes]:
        """Generate LCOV coverage report from coverage instance."""
        try:
            if not self._coverage_instance:
                return None

            # Stop coverage collection
            self._coverage_instance.stop()
            cov_data = self._coverage_instance.get_data()
            measured_files = cov_data.measured_files()

            if not measured_files:
                log.debug("No measured files found in coverage data")
                return None

            lcov_lines = ["TN:"]  # Test name (empty)

            for abs_path_str in sorted(measured_files):
                executed_lines = set(cov_data.lines(abs_path_str) or [])
                if not executed_lines:
                    continue

                # Use relative path if possible
                try:
                    from pathlib import Path

                    abs_path = Path(abs_path_str)
                    workspace = Path.cwd()
                    if abs_path.is_relative_to(workspace):
                        file_path = str(abs_path.relative_to(workspace))
                    else:
                        file_path = abs_path_str
                except (ValueError, AttributeError):
                    file_path = abs_path_str

                lcov_lines.append(f"SF:{file_path}")

                # Add line data
                valid_lines = [ln for ln in executed_lines if ln > 0]
                for line_num in sorted(valid_lines):
                    lcov_lines.append(f"DA:{line_num},1")

                # Summary
                lines_hit = len(valid_lines)
                lcov_lines.extend([f"LF:{lines_hit}", f"LH:{lines_hit}", "end_of_record"])

            if len(lcov_lines) <= 1:
                log.debug("No coverage data to report")
                return None

            content = "\n".join(lcov_lines) + "\n"

            # Record files processed telemetry
            files_with_coverage = len([line for line in lcov_lines if line.startswith("SF:")])
            self._record_telemetry(CODE_COVERAGE_FILES, files_with_coverage)

            log.debug("Generated LCOV report: %d bytes for %d files", len(content), files_with_coverage)
            return content.encode("utf-8")

        except Exception:
            log.exception("Failed to generate LCOV report")
            return None

    def _is_pytest_cov_enabled(self, config) -> bool:
        """Check if pytest-cov is enabled."""
        if not config.pluginmanager.get_plugin("pytest_cov"):
            return False
        cov_option = config.getoption("--cov", default=False)
        nocov_option = config.getoption("--no-cov", default=False)
        if nocov_option:
            return False
        return bool(cov_option)

    def _upload_coverage_reports(self):
        """Generate and upload coverage reports."""
        if not self._coverage_instance:
            log.debug("No coverage instance available for upload")
            return

        try:
            # Generate LCOV report
            lcov_data = self._generate_lcov_report()
            if lcov_data:
                self._upload_report(lcov_data, "lcov")
        except Exception:
            log.exception("Failed to upload coverage reports")

    def _generate_lcov_report(self) -> t.Optional[bytes]:
        """Generate LCOV coverage report."""
        try:
            self._coverage_instance.stop()
            cov_data = self._coverage_instance.get_data()
            measured_files = cov_data.measured_files()

            if not measured_files:
                return None

            lcov_lines = ["TN:"]  # Test name (empty)

            for abs_path_str in sorted(measured_files):
                executed_lines = set(cov_data.lines(abs_path_str) or [])
                if not executed_lines:
                    continue

                # Use relative path if possible
                try:
                    abs_path = Path(abs_path_str)
                    workspace = Path.cwd()
                    if abs_path.is_relative_to(workspace):
                        file_path = str(abs_path.relative_to(workspace))
                    else:
                        file_path = abs_path_str
                except (ValueError, AttributeError):
                    file_path = abs_path_str

                lcov_lines.append(f"SF:{file_path}")

                # Add line data
                valid_lines = [ln for ln in executed_lines if ln > 0]
                for line_num in sorted(valid_lines):
                    lcov_lines.append(f"DA:{line_num},1")

                # Summary
                lines_hit = len(valid_lines)
                lcov_lines.extend([f"LF:{lines_hit}", f"LH:{lines_hit}", "end_of_record"])

            if len(lcov_lines) <= 1:
                return None

            content = "\n".join(lcov_lines) + "\n"
            # Count files that had valid coverage data
            files_with_coverage = len([line for line in lcov_lines if line.startswith("SF:")])
            self._record_telemetry(CODE_COVERAGE_FILES, files_with_coverage)
            log.debug("Generated LCOV report: %d bytes for %d files", len(content), files_with_coverage)
            return content.encode("utf-8")

        except Exception:
            log.exception("Failed to generate LCOV report")
            return None

    def _upload_report(self, report_data: bytes, report_format: str):
        """Upload coverage report to intake."""
        try:
            start_time = time.time()
            # Get connector
            connector = self._get_connector()
            if not connector:
                log.debug("No connector available for upload")
                return

            # Compress report
            compressed = self._compress_data(report_data)

            # Create event JSON with git and CI tags
            event = self._create_coverage_report_event(report_format)
            event_json = json.dumps(event, separators=(",", ":")).encode("utf-8")

            # Upload files
            files = [
                self._create_file_attachment(
                    "coverage", f"coverage.{report_format}.gz", "application/gzip", compressed
                ),
                self._create_file_attachment("event", "event.json", "application/json", event_json),
            ]

            self._record_telemetry(COVERAGE_UPLOAD_REQUEST, 1, {"format": report_format})
            self._record_telemetry(COVERAGE_UPLOAD_REQUEST_BYTES, len(compressed) + len(event_json))

            log.debug("Uploading %d bytes to %s", len(compressed) + len(event_json), COVERAGE_INTAKE_ENDPOINT)

            result = connector.post_files(COVERAGE_INTAKE_ENDPOINT, files=files, send_gzip=False)

            if hasattr(result, "error_type") and result.error_type:
                log.error("Coverage upload failed: %s", result.error_type)
                self._record_telemetry(COVERAGE_UPLOAD_REQUEST_ERRORS, 1, {"error": result.error_type})
            else:
                log.debug("Coverage upload successful")
                duration_ms = (time.time() - start_time) * 1000
                self._record_telemetry(COVERAGE_UPLOAD_REQUEST_MS, duration_ms)

        except Exception:
            log.exception("Exception during coverage upload")
            self._record_telemetry(COVERAGE_UPLOAD_REQUEST_ERRORS, 1, {"error": "exception"})

    def _compress_data(self, data: bytes) -> bytes:
        """Compress data using gzip."""
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(data)
        return buf.getvalue()

    def _create_file_attachment(self, name: str, filename: str, content_type: str, data: bytes):
        """Create file attachment for upload."""
        try:
            from ddtrace.testing.internal.http import FileAttachment

            return FileAttachment(name=name, filename=filename, content_type=content_type, data=data)
        except ImportError:
            return {"name": name, "filename": filename, "content_type": content_type, "data": data}

    def _get_connector(self):
        """Get HTTP connector for uploads."""
        # Try V3 session manager first
        if self.session_manager and hasattr(self.session_manager, "_http_connector"):
            return self.session_manager._http_connector

        # Try V2 CI visibility connector
        try:
            from ddtrace.contrib.internal.ci_visibility import CIVisibility

            service = CIVisibility._instance
            if service and hasattr(service, "_agent_connector"):
                return service._agent_connector
        except (ImportError, AttributeError):
            pass

        return None

    def _record_telemetry(self, metric_name: str, value: t.Union[int, float], tags: t.Optional[t.Dict] = None):
        """Record telemetry metric."""
        try:
            if self.telemetry_writer:
                # V2 plugin telemetry
                from ddtrace.internal.telemetry import telemetry_writer
                from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE

                tag_pairs = tuple(tags.items()) if tags else ()
                telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, metric_name, value, tag_pairs)

            elif self.session_manager and hasattr(self.session_manager, "telemetry_api"):
                # V3 plugin telemetry
                telemetry_api = self.session_manager.telemetry_api
                if hasattr(telemetry_api, "add_count_metric"):
                    telemetry_api.add_count_metric(metric_name, value, tags or {})

        except Exception:
            log.debug("Failed to record telemetry", exc_info=True)

    def _create_coverage_report_event(self, report_format: str) -> dict:
        """Create the event JSON for the coverage report upload with git and CI tags."""
        event = {
            "type": "coverage_report",
            "format": report_format,
            "timestamp": int(time.time() * 1000),
        }

        # Import git and CI tag constants
        try:
            from ddtrace.testing.internal.ci import CITag
            from ddtrace.testing.internal.git import GitTag
        except ImportError:
            log.debug("Could not import GitTag/CITag constants")
            return event

        # Add git tags
        git_tags = [
            GitTag.REPOSITORY_URL,
            GitTag.COMMIT_SHA,
            GitTag.BRANCH,
            GitTag.TAG,
            GitTag.COMMIT_MESSAGE,
            GitTag.COMMIT_AUTHOR_NAME,
            GitTag.COMMIT_AUTHOR_EMAIL,
            GitTag.COMMIT_AUTHOR_DATE,
            GitTag.COMMIT_COMMITTER_NAME,
            GitTag.COMMIT_COMMITTER_EMAIL,
            GitTag.COMMIT_COMMITTER_DATE,
        ]

        for git_tag in git_tags:
            if git_tag in self.env_tags:
                event[git_tag] = self.env_tags[git_tag]

        # Add CI tags
        ci_tags = [
            CITag.PROVIDER_NAME,
            CITag.PIPELINE_ID,
            CITag.PIPELINE_NAME,
            CITag.PIPELINE_NUMBER,
            CITag.PIPELINE_URL,
            CITag.JOB_NAME,
            CITag.JOB_URL,
            CITag.STAGE_NAME,
            CITag.WORKSPACE_PATH,
            CITag.NODE_NAME,
            CITag.NODE_LABELS,
        ]

        for ci_tag in ci_tags:
            if ci_tag in self.env_tags:
                event[ci_tag] = self.env_tags[ci_tag]

        # Add PR number if available
        if "git.pull_request.number" in self.env_tags:
            event["pr.number"] = self.env_tags["git.pull_request.number"]

        return event
