"""
Coverage upload telemetry interface.

This module provides the abstract interface for recording coverage upload telemetry
metrics without any concrete implementation dependencies. This allows each plugin
to implement their own telemetry recorders with top-level imports.
"""

from abc import ABC
from abc import abstractmethod


class CoverageTelemetryRecorder(ABC):
    """Abstract interface for recording coverage upload telemetry metrics."""

    @abstractmethod
    def record_upload_request(self, bytes_uploaded: int) -> None:
        """Record a coverage upload request (coverage_upload.request)."""
        pass

    @abstractmethod
    def record_upload_success(self, duration_ms: float) -> None:
        """Record a successful coverage upload with duration (coverage_upload.request_ms)."""
        pass

    @abstractmethod
    def record_upload_error(self, error_type: str, status_code: int) -> None:
        """Record a failed coverage upload (coverage_upload.request_errors)."""
        pass

    @abstractmethod
    def record_coverage_files_processed(self, file_count: int) -> None:
        """Record the number of files processed for coverage (code_coverage.files)."""
        pass


class NoOpCoverageTelemetryRecorder(CoverageTelemetryRecorder):
    """No-op telemetry recorder that does nothing."""

    def record_upload_request(self, bytes_uploaded: int) -> None:
        pass

    def record_upload_success(self, duration_ms: float) -> None:
        pass

    def record_upload_error(self, error_type: str, status_code: int) -> None:
        pass

    def record_coverage_files_processed(self, file_count: int) -> None:
        pass
