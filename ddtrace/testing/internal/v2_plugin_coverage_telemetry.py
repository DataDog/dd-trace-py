"""
Coverage upload telemetry implementation for the v2 pytest plugin.

This module provides telemetry recording using CI Visibility telemetry with top-level imports
for better performance and clarity.
"""

import logging

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.testing.internal.coverage_telemetry import CoverageTelemetryRecorder


log = logging.getLogger(__name__)


class V2PluginCoverageTelemetryRecorder(CoverageTelemetryRecorder):
    """Telemetry recorder for the v2 pytest plugin using internal CI visibility telemetry."""

    def record_upload_request(self, bytes_uploaded: int) -> None:
        try:
            # coverage_upload.request metric
            telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.CIVISIBILITY, "coverage_upload.request", 1, (("library", "coveragepy"),)
            )

            # coverage_upload.request_bytes metric
            telemetry_writer.add_distribution_metric(
                TELEMETRY_NAMESPACE.CIVISIBILITY, "coverage_upload.request_bytes", bytes_uploaded
            )

            log.debug("V2 plugin: Recorded coverage upload request telemetry: %d bytes", bytes_uploaded)
        except Exception:
            log.debug("Failed to record v2 plugin telemetry for upload request", exc_info=True)

    def record_upload_success(self, duration_ms: float) -> None:
        try:
            # coverage_upload.request_ms metric
            telemetry_writer.add_distribution_metric(
                TELEMETRY_NAMESPACE.CIVISIBILITY, "coverage_upload.request_ms", duration_ms
            )

            log.debug("V2 plugin: Recorded coverage upload success telemetry: %.1fms", duration_ms)
        except Exception:
            log.debug("Failed to record v2 plugin telemetry for upload success", exc_info=True)

    def record_upload_error(self, error_type: str, status_code: int) -> None:
        try:
            # coverage_upload.request_errors metric
            # Include tags for error_type and status_code
            telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.CIVISIBILITY,
                "coverage_upload.request_errors",
                1,
                (("library", "coveragepy"), ("error_type", error_type), ("status_code", str(status_code))),
            )

            log.debug("V2 plugin: Recorded coverage upload error telemetry: %s, %d", error_type, status_code)
        except Exception:
            log.debug("Failed to record v2 plugin telemetry for upload error", exc_info=True)

    def record_coverage_files_processed(self, file_count: int) -> None:
        try:
            # code_coverage.files metric
            telemetry_writer.add_distribution_metric(
                TELEMETRY_NAMESPACE.CIVISIBILITY, "code_coverage.files", file_count
            )

            log.debug("V2 plugin: Recorded coverage files processed telemetry: %d files", file_count)
        except Exception:
            log.debug("Failed to record v2 plugin telemetry for files processed", exc_info=True)
