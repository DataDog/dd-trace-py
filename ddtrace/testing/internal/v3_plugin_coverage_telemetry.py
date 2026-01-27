"""
Coverage upload telemetry implementation for the v3 pytest plugin.

This module provides telemetry recording using TelemetryAPI with top-level imports
for better performance and clarity.
"""

import logging

from ddtrace.testing.internal.coverage_telemetry import CoverageTelemetryRecorder
from ddtrace.testing.internal.telemetry import TelemetryAPI


log = logging.getLogger(__name__)


class V3PluginCoverageTelemetryRecorder(CoverageTelemetryRecorder):
    """Telemetry recorder for the v3 pytest plugin using TelemetryAPI."""

    def record_upload_request(self, bytes_uploaded: int) -> None:
        try:
            # coverage_upload.request metric
            TelemetryAPI.get().add_count_metric("coverage_upload.request", 1)
            # coverage_upload.request_bytes metric
            TelemetryAPI.get().add_distribution_metric("coverage_upload.request_bytes", bytes_uploaded)
        except Exception:
            log.debug("Failed to record v3 plugin telemetry for upload request", exc_info=True)

    def record_upload_success(self, duration_ms: float) -> None:
        try:
            # coverage_upload.request_ms metric
            TelemetryAPI.get().add_distribution_metric("coverage_upload.request_ms", duration_ms)
        except Exception:
            log.debug("Failed to record v3 plugin telemetry for upload success", exc_info=True)

    def record_upload_error(self, error_type: str, status_code: int) -> None:
        try:
            # coverage_upload.request_errors metric
            TelemetryAPI.get().add_count_metric("coverage_upload.request_errors", 1)
        except Exception:
            log.debug("Failed to record v3 plugin telemetry for upload error", exc_info=True)

    def record_coverage_files_processed(self, file_count: int) -> None:
        try:
            # code_coverage.files metric
            TelemetryAPI.get().add_distribution_metric("code_coverage.files", file_count)
        except Exception:
            log.debug("Failed to record v3 plugin telemetry for files processed", exc_info=True)
