"""
Coverage report uploader for Test Optimization.

This module handles uploading aggregated coverage reports to the Datadog intake.
"""

from __future__ import annotations

import logging
from pathlib import Path
import typing as t

from ddtrace.testing.internal.coverage_report import COVERAGE_REPORT_FORMAT_LCOV
from ddtrace.testing.internal.coverage_report import compress_coverage_report
from ddtrace.testing.internal.coverage_report import create_coverage_report_event
from ddtrace.testing.internal.coverage_report import generate_coverage_report_lcov_from_coverage_py
from ddtrace.testing.internal.coverage_report import generate_coverage_report_lcov_from_module_collector
from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.http import FileAttachment
from ddtrace.testing.internal.http import Subdomain
from ddtrace.testing.internal.telemetry import TelemetryAPI


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
    ) -> None:
        """
        Initialize the coverage report uploader.

        Args:
            connector_setup: Backend connector setup for creating connections
            env_tags: Environment tags containing git and CI information
            skippable_coverage: Coverage data for ITR-skipped tests (file_path -> set of line numbers)
            workspace_path: Workspace path for resolving relative file paths
        """
        self.connector = connector_setup.get_connector_for_subdomain(Subdomain.CICOVREPRT)
        self.env_tags = env_tags
        self.skippable_coverage = skippable_coverage
        self.workspace_path = workspace_path

    def upload_coverage_report(self, cov_instance: t.Optional[t.Any] = None) -> None:
        """
        Generate and upload the coverage report to the intake endpoint.

        Args:
            cov_instance: The coverage.Coverage instance (if using coverage.py with --cov)

        This method:
        1. Auto-detects coverage source (coverage.py or ModuleCodeCollector)
        2. Transforms coverage data to LCOV format
        3. Merges with ITR skipped test coverage data
        4. Compresses the report with gzip
        5. Creates the event JSON with git and CI tags
        6. Uploads both as a multipart/form-data request
        """
        log.debug("Generating coverage report for upload")

        # Auto-detect coverage source and generate report
        if cov_instance is not None:
            log.debug("Using coverage.py data for coverage report")
            report_data = generate_coverage_report_lcov_from_coverage_py(
                cov_instance=cov_instance,
                workspace_path=self.workspace_path,
                skippable_coverage=self.skippable_coverage,
            )
        else:
            log.debug("Using ModuleCodeCollector data for coverage report")
            report_data = generate_coverage_report_lcov_from_module_collector(
                workspace_path=self.workspace_path,
                skippable_coverage=self.skippable_coverage,
            )

        if report_data is None:
            log.debug("No coverage data available, skipping report upload")
            return

        # Compress the report
        compressed_report = compress_coverage_report(report_data)
        log.debug(
            "Compressed coverage report: %d bytes (original: %d bytes)",
            len(compressed_report),
            len(report_data),
        )

        # Create event JSON
        event_data = create_coverage_report_event(self.env_tags, COVERAGE_REPORT_FORMAT_LCOV)

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
        try:
            log.info("Uploading coverage report to Datadog")
            result = self.connector.post_files(
                "/api/v2/cicovreprt",
                files=files,
                send_gzip=False,  # Already compressed
            )

            if result.error_type:
                log.warning(
                    "Failed to upload coverage report: %s - %s",
                    result.error_type,
                    result.error_description,
                )
                TelemetryAPI.get().add_count_metric("coverage_report_upload.errors", 1)
            else:
                log.info("Successfully uploaded coverage report")
                TelemetryAPI.get().add_count_metric("coverage_report_upload.success", 1)
                TelemetryAPI.get().add_distribution_metric(
                    "coverage_report_upload.bytes",
                    len(compressed_report),
                )

        except Exception:
            log.exception("Error uploading coverage report")
            TelemetryAPI.get().add_count_metric("coverage_report_upload.errors", 1)
        finally:
            self.connector.close()
