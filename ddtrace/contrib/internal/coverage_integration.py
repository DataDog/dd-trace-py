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
        pytest_cov_plugin = session.config.pluginmanager.get_plugin("pytest_cov")
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

    def __init__(self, telemetry_writer=None, session_manager=None):
        self.telemetry_writer = telemetry_writer
        self.session_manager = session_manager
        self._coverage_instance = None
        self._initialized = False

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
                self._upload_coverage_reports()

        self._record_telemetry("coverage.finished", 1, {"library": "coveragepy", "framework": "pytest"})

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
            log.debug("Generated LCOV report: %d bytes", len(content))
            return content.encode("utf-8")

        except Exception:
            log.exception("Failed to generate LCOV report")
            return None

    def _upload_report(self, report_data: bytes, report_format: str):
        """Upload coverage report to intake."""
        try:
            # Get connector
            connector = self._get_connector()
            if not connector:
                log.debug("No connector available for upload")
                return

            # Compress report
            compressed = self._compress_data(report_data)

            # Create event JSON
            event = {
                "type": "coverage_report",
                "format": report_format,
                "timestamp": int(time.time() * 1000),
            }
            event_json = json.dumps(event, separators=(",", ":")).encode("utf-8")

            # Upload files
            files = [
                self._create_file_attachment(
                    "coverage", f"coverage.{report_format}.gz", "application/gzip", compressed
                ),
                self._create_file_attachment("event", "event.json", "application/json", event_json),
            ]

            self._record_telemetry("coverage.upload.attempt", 1, {"format": report_format})
            self._record_telemetry("coverage.upload.request_size", len(compressed) + len(event_json))

            log.debug("Uploading %d bytes to %s", len(compressed) + len(event_json), COVERAGE_INTAKE_ENDPOINT)

            result = connector.post_files(COVERAGE_INTAKE_ENDPOINT, files=files, send_gzip=False)

            if hasattr(result, "error_type") and result.error_type:
                log.error("Coverage upload failed: %s", result.error_type)
                self._record_telemetry("coverage.upload.error", 1, {"error": result.error_type})
            else:
                log.debug("Coverage upload successful")
                self._record_telemetry("coverage.upload.success", 1, {"format": report_format})

        except Exception:
            log.exception("Exception during coverage upload")
            self._record_telemetry("coverage.upload.error", 1, {"error": "exception"})

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
