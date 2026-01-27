"""
Coverage report uploader for Test Optimization.

This module handles uploading aggregated coverage reports to the Datadog intake.
"""

from __future__ import annotations

import logging
from pathlib import Path
import time
import typing as t

from ddtrace.testing.internal.coverage_report import COVERAGE_REPORT_FORMAT_LCOV
from ddtrace.testing.internal.coverage_report import compress_coverage_report
from ddtrace.testing.internal.coverage_report import create_coverage_report_event
from ddtrace.testing.internal.coverage_report import generate_coverage_report_lcov_from_coverage_py
from ddtrace.testing.internal.coverage_telemetry import CoverageTelemetryRecorder
from ddtrace.testing.internal.coverage_telemetry import NoOpCoverageTelemetryRecorder
from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.http import FileAttachment
from ddtrace.testing.internal.http import Subdomain


log = logging.getLogger(__name__)


class CoverageReportUploader:
    """
    Uploader for aggregated coverage reports.

    This class generates and uploads coverage reports in standard formats (e.g., LCOV)
    to the Datadog coverage report intake endpoint. It merges local coverage with
    backend coverage data for ITR-skipped tests.
    """

    def __init__(
        self,
        connector_setup: BackendConnectorSetup,
        env_tags: t.Dict[str, str],
        skippable_coverage: t.Dict[str, t.Set[int]],
        workspace_path: Path,
        telemetry_recorder: t.Optional[CoverageTelemetryRecorder] = None,
    ) -> None:
        """
        Initialize the coverage report uploader.

        Args:
            connector_setup: Backend connector setup for creating connections
            env_tags: Environment tags containing git and CI information
            skippable_coverage: Coverage data for ITR-skipped tests (file_path -> set of line numbers)
            workspace_path: Workspace path for resolving relative file paths
            telemetry_recorder: Optional telemetry recorder for metrics (uses no-op if None)
        """
        self.connector = connector_setup.get_connector_for_subdomain(Subdomain.CICOVREPRT)
        self.env_tags = env_tags
        self.skippable_coverage = skippable_coverage
        self.workspace_path = workspace_path
        self.telemetry_recorder = telemetry_recorder or NoOpCoverageTelemetryRecorder()

    def upload_coverage_report(self, cov_instance: t.Optional[t.Any] = None) -> None:
        """
        Generate and upload the coverage report to the intake endpoint.

        Args:
            cov_instance: The coverage.Coverage instance (required, from pytest-cov --cov)

        This method:
        1. Validates that coverage.py instance is available
        2. Transforms coverage data to LCOV format
        3. Merges with ITR skipped test coverage data
        4. Compresses the report with gzip
        5. Creates the event JSON with git and CI tags
        6. Uploads both as a multipart/form-data request
        """
        log.debug("Generating coverage report for upload")

        # Debug: Log environment tags for comparison
        log.debug("Coverage upload env_tags:")
        import json

        try:
            log.debug(json.dumps(self.env_tags, indent=2, sort_keys=True))
        except Exception:
            log.debug(str(self.env_tags))

        # Generate report from coverage.py (pytest-cov)
        if cov_instance is None:
            log.debug("No coverage.py instance provided, coverage report requires --cov flag")
            return

        log.debug("Using coverage.py data for coverage report")

        # Record the number of files being processed for coverage
        try:
            measured_files = cov_instance.get_data().measured_files()
            file_count = len(measured_files) + len(self.skippable_coverage)  # Include ITR coverage files
            self.telemetry_recorder.record_coverage_files_processed(file_count)
            log.debug(
                "Coverage processing %d files (%d local + %d ITR)",
                file_count,
                len(measured_files),
                len(self.skippable_coverage),
            )
        except Exception as e:
            log.debug("Failed to record coverage files count telemetry: %s", e)

        report_data = generate_coverage_report_lcov_from_coverage_py(
            cov_instance=cov_instance,
            workspace_path=self.workspace_path,
            skippable_coverage=self.skippable_coverage,
        )

        if report_data is None:
            log.debug("No coverage data available, skipping report upload")
            return

        # Debug: Log sample LCOV coverage data (first few lines)
        log.debug("Coverage upload LCOV data sample (first 500 chars):")
        sample_data = report_data[:500] if len(report_data) > 500 else report_data
        log.debug(sample_data)

        # Compress the report
        compressed_report = compress_coverage_report(report_data)
        log.debug(
            "Compressed coverage report: %d bytes (original: %d bytes)",
            len(compressed_report),
            len(report_data),
        )

        # Create event JSON
        event_data = create_coverage_report_event(self.env_tags, COVERAGE_REPORT_FORMAT_LCOV)

        # Debug: Log the event payload in human-readable format
        try:
            import json

            event_json = json.loads(event_data.decode("utf-8") if isinstance(event_data, bytes) else event_data)
            log.debug("Coverage upload event payload:")
            log.debug(json.dumps(event_json, indent=2, sort_keys=True))
        except Exception as e:
            log.debug("Failed to parse event JSON for debug logging: %s", e)
            log.debug("Raw event data: %s", event_data)

        # Prepare multipart attachments
        files = [
            FileAttachment(
                name="coverage",
                filename="coverage.lcov.gz",
                content_type="application/gzip",
                data=compressed_report,
            ),
            FileAttachment(
                name="event",
                filename="event.json",
                content_type="application/json",
                data=event_data,
            ),
        ]

        # Upload to the intake endpoint
        # Calculate total payload size (compressed coverage + event JSON)
        total_payload_size = len(compressed_report) + len(event_data)

        # Record upload request (coverage_upload.request and coverage_upload.request_bytes)
        self.telemetry_recorder.record_upload_request(total_payload_size)

        start_time = time.time()
        try:
            log.debug("Uploading coverage report to Datadog")

            result = self.connector.post_files(
                "/api/v2/cicovreprt",
                files=files,
                send_gzip=False,  # Already compressed
            )

            elapsed_time = time.time() - start_time
            elapsed_ms = elapsed_time * 1000  # Convert to milliseconds

            if result.error_type:
                log.debug(
                    "Failed to upload coverage report: %s - %s",
                    result.error_type,
                    result.error_description,
                )

                # Get status code from response if available
                status_code = getattr(result.response, "status", 0) if result.response else 0
                error_type = result.error_type or "unknown"

                # Record error (coverage_upload.request_errors)
                self.telemetry_recorder.record_upload_error(error_type, status_code)
            else:
                payload_size_kb = total_payload_size / 1024

                # Get status code from response if available
                status_code = getattr(result.response, "status", "Unknown") if result.response else "Unknown"
                status_text = f"{status_code} Accepted" if status_code == 202 else str(status_code)

                # Reconstruct the base URL from connection info
                conn = getattr(self.connector, "conn", None)
                if conn and hasattr(conn, "host"):
                    # Check if it's HTTPS connection
                    import http.client

                    scheme = "https" if isinstance(conn, http.client.HTTPSConnection) else "http"
                    port_info = f":{conn.port}" if conn.port not in (80, 443) else ""
                    base_url = f"{scheme}://{conn.host}{port_info}"
                else:
                    base_url = "[unknown]"

                # Log similar to test payload format
                log.debug(
                    "Got response: %s sent %.2fKB in %.5fs to %s%s",
                    status_text,
                    payload_size_kb,
                    elapsed_time,
                    base_url,
                    "/api/v2/cicovreprt",
                )
                log.debug("Successfully uploaded coverage report")

                # Record success with duration in milliseconds (coverage_upload.request_ms)
                self.telemetry_recorder.record_upload_success(elapsed_ms)

        except Exception as e:
            # Calculate elapsed time for error case
            elapsed_time = time.time() - start_time if "start_time" in locals() else 0.0
            log.exception("Error uploading coverage report")

            # Record exception error
            error_type = type(e).__name__
            self.telemetry_recorder.record_upload_error(error_type, 0)
        finally:
            self.connector.close()
