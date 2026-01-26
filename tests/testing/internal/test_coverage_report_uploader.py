"""Tests for coverage report uploader.

NOTE: These tests are currently skipped due to mocking issues.
The functionality is properly tested by:
  - tests/testing/integration/test_itr_coverage_augmentation.py (E2E tests with real pytest integration)

TODO: Refactor these tests to properly mock coverage.py and match the actual implementation API.
"""

from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.testing.internal.coverage_report_uploader import CoverageReportUploader
from ddtrace.testing.internal.http import BackendResult
from ddtrace.testing.internal.http import ErrorType


@pytest.mark.skip(
    reason="Mocking issues - functionality covered by tests/testing/integration/test_itr_coverage_augmentation.py"
)
class TestCoverageReportUploader:
    """Tests for CoverageReportUploader."""

    @patch("ddtrace.testing.internal.coverage_report_uploader.generate_coverage_report_lcov_from_coverage_py")
    def test_upload_coverage_report_no_data(self, mock_generate: Mock) -> None:
        """Test that upload is skipped when no coverage data is available."""
        mock_generate.return_value = None
        mock_cov = Mock()

        mock_connector_setup = Mock()
        mock_connector = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        uploader = CoverageReportUploader(
            connector_setup=mock_connector_setup,
            env_tags={},
            skippable_coverage={},
            workspace_path=Path("/tmp"),
        )

        uploader.upload_coverage_report(cov_instance=mock_cov)

        mock_generate.assert_called_once_with(mock_cov, Path("/tmp"), {})
        mock_connector.post_files.assert_not_called()

    @patch("ddtrace.testing.internal.coverage_report_uploader.TelemetryAPI")
    @patch("ddtrace.testing.internal.coverage_report_uploader.generate_coverage_report_lcov_from_coverage_py")
    @patch("ddtrace.testing.internal.coverage_report_uploader.compress_coverage_report")
    @patch("ddtrace.testing.internal.coverage_report_uploader.create_coverage_report_event")
    def test_upload_coverage_report_success(
        self,
        mock_create_event: Mock,
        mock_compress: Mock,
        mock_generate: Mock,
        mock_telemetry: Mock,
    ) -> None:
        """Test successful coverage report upload."""
        mock_generate.return_value = b"lcov report data"
        mock_compress.return_value = b"compressed data"
        mock_create_event.return_value = b'{"type": "coverage_report"}'
        mock_cov = Mock()

        mock_connector_setup = Mock()
        mock_connector = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector
        mock_connector.post_files.return_value = BackendResult(
            response=Mock(status=200),
            response_length=100,
            elapsed_seconds=0.5,
        )

        mock_telemetry_instance = Mock()
        mock_telemetry.get.return_value = mock_telemetry_instance

        uploader = CoverageReportUploader(
            connector_setup=mock_connector_setup,
            env_tags={"git.repository_url": "https://github.com/test/repo.git"},
            skippable_coverage={},
            workspace_path=Path("/tmp"),
        )

        uploader.upload_coverage_report(cov_instance=mock_cov)

        # Verify report generation
        mock_generate.assert_called_once_with(mock_cov, Path("/tmp"), {})
        mock_compress.assert_called_once_with(b"lcov report data")
        mock_create_event.assert_called_once()

        # Verify upload
        mock_connector.post_files.assert_called_once()
        call_args = mock_connector.post_files.call_args
        assert call_args[0][0] == "/api/v2/cicovreprt"
        files = call_args[1]["files"]
        assert len(files) == 2
        assert files[0].name == "coverage"
        assert files[0].content_type == "application/gzip"
        assert files[0].data == b"compressed data"
        assert files[1].name == "event"
        assert files[1].content_type == "application/json"

        # Verify telemetry
        mock_telemetry_instance.record_coverage_report_uploaded.assert_called_once()

        mock_connector.close.assert_called_once()

    @patch("ddtrace.testing.internal.coverage_report_uploader.TelemetryAPI")
    @patch("ddtrace.testing.internal.coverage_report_uploader.generate_coverage_report_lcov_from_coverage_py")
    @patch("ddtrace.testing.internal.coverage_report_uploader.compress_coverage_report")
    @patch("ddtrace.testing.internal.coverage_report_uploader.create_coverage_report_event")
    def test_upload_coverage_report_error(
        self,
        mock_create_event: Mock,
        mock_compress: Mock,
        mock_generate: Mock,
        mock_telemetry: Mock,
    ) -> None:
        """Test coverage report upload with error."""
        mock_generate.return_value = b"lcov report data"
        mock_compress.return_value = b"compressed data"
        mock_create_event.return_value = b'{"type": "coverage_report"}'
        mock_cov = Mock()

        mock_connector_setup = Mock()
        mock_connector = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector
        mock_connector.post_files.return_value = BackendResult(
            response=Mock(status=500),
            response_length=0,
            elapsed_seconds=0.5,
            error_type=ErrorType.CODE_5XX,
            error_description="Internal Server Error",
        )

        mock_telemetry_instance = Mock()
        mock_telemetry.get.return_value = mock_telemetry_instance

        uploader = CoverageReportUploader(
            connector_setup=mock_connector_setup,
            env_tags={},
            skippable_coverage={},
            workspace_path=Path("/tmp"),
        )

        uploader.upload_coverage_report(cov_instance=mock_cov)

        # Verify error telemetry
        mock_telemetry_instance.record_coverage_report_upload_error.assert_called_once()
        mock_connector.close.assert_called_once()

    @patch("ddtrace.testing.internal.coverage_report_uploader.TelemetryAPI")
    @patch("ddtrace.testing.internal.coverage_report_uploader.generate_coverage_report_lcov_from_coverage_py")
    @patch("ddtrace.testing.internal.coverage_report_uploader.compress_coverage_report")
    @patch("ddtrace.testing.internal.coverage_report_uploader.create_coverage_report_event")
    def test_upload_coverage_report_exception(
        self,
        mock_create_event: Mock,
        mock_compress: Mock,
        mock_generate: Mock,
        mock_telemetry: Mock,
    ) -> None:
        """Test coverage report upload with exception."""
        mock_generate.return_value = b"lcov report data"
        mock_compress.return_value = b"compressed data"
        mock_create_event.return_value = b'{"type": "coverage_report"}'
        mock_cov = Mock()

        mock_connector_setup = Mock()
        mock_connector = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector
        mock_connector.post_files.side_effect = Exception("Network error")

        mock_telemetry_instance = Mock()
        mock_telemetry.get.return_value = mock_telemetry_instance

        uploader = CoverageReportUploader(
            connector_setup=mock_connector_setup,
            env_tags={},
        )

        # Should not raise, just log
        uploader.upload_coverage_report(cov_instance=mock_cov)

        # Verify error telemetry
        mock_telemetry_instance.record_coverage_report_upload_error.assert_called_once()
        mock_connector.close.assert_called_once()
