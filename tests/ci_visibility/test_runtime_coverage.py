"""
Unit tests for runtime request coverage functionality.

Tests the runtime_coverage module which provides coverage collection and sending
for individual runtime requests (e.g., HTTP requests in WSGI/ASGI applications).
"""

from pathlib import Path
from unittest import mock

import pytest

from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.runtime_coverage import build_runtime_coverage_payload
from ddtrace.internal.ci_visibility.runtime_coverage import initialize_runtime_coverage
from ddtrace.internal.ci_visibility.runtime_coverage import send_runtime_coverage
from ddtrace.internal.compat import PYTHON_VERSION_INFO


class TestInitializeRuntimeCoverage:
    """Test runtime coverage initialization."""

    @pytest.mark.skipif(PYTHON_VERSION_INFO < (3, 12), reason="Requires Python 3.12+")
    @mock.patch("ddtrace.internal.coverage.installer.install")
    @mock.patch("ddtrace.internal.ci_visibility.runtime_coverage.ModuleCodeCollector")
    def test_initialize_runtime_coverage_success(self, mock_collector_class, mock_install):
        """Test successful initialization of runtime coverage."""
        # Mock successful installation
        mock_instance = mock.Mock()
        mock_collector_class._instance = mock_instance

        result = initialize_runtime_coverage()

        assert result is True
        mock_install.assert_called_once()
        # Verify install was called with correct parameters
        call_args = mock_install.call_args
        assert "include_paths" in call_args[1]
        assert "collect_import_time_coverage" in call_args[1]
        assert call_args[1]["collect_import_time_coverage"] is True

    @pytest.mark.skipif(PYTHON_VERSION_INFO < (3, 12), reason="Requires Python 3.12+")
    @mock.patch("ddtrace.internal.coverage.installer.install")
    @mock.patch("ddtrace.internal.ci_visibility.runtime_coverage.ModuleCodeCollector")
    def test_initialize_runtime_coverage_instance_not_created(self, mock_collector_class, mock_install):
        """Test initialization fails when collector instance is not created."""
        # Mock failed installation - instance is None
        mock_collector_class._instance = None

        result = initialize_runtime_coverage()

        assert result is False
        mock_install.assert_called_once()

    @pytest.mark.skipif(PYTHON_VERSION_INFO < (3, 12), reason="Requires Python 3.12+")
    @mock.patch("ddtrace.internal.coverage.installer.install")
    def test_initialize_runtime_coverage_install_raises_exception(self, mock_install):
        """Test initialization handles exceptions gracefully."""
        # Mock install raising an exception
        mock_install.side_effect = Exception("Installation failed")

        result = initialize_runtime_coverage()

        assert result is False

    @mock.patch("ddtrace.internal.ci_visibility.runtime_coverage.PYTHON_VERSION_INFO", (3, 11))
    def test_initialize_runtime_coverage_unsupported_python(self):
        """Test initialization fails gracefully on unsupported Python version."""
        result = initialize_runtime_coverage()

        assert result is False


class TestBuildRuntimeCoveragePayload:
    """Test building runtime coverage payloads."""

    @mock.patch("ddtrace.internal.ci_visibility.runtime_coverage.ModuleCodeCollector")
    def test_build_runtime_coverage_payload_success(self, mock_collector_class):
        """Test successfully building a coverage payload."""
        # Mock coverage data
        mock_files = [
            {"filename": "/app/views.py", "segments": [[1, 0, 10, 0, -1]]},
            {"filename": "/app/models.py", "segments": [[5, 0, 15, 0, -1]]},
        ]
        mock_collector_class.report_seen_lines.return_value = mock_files

        root_dir = Path("/app")
        trace_id = 12345
        span_id = 67890

        payload = build_runtime_coverage_payload(root_dir, trace_id, span_id)

        assert payload is not None
        assert payload["trace_id"] == trace_id
        assert payload["span_id"] == span_id
        assert payload["files"] == mock_files
        assert len(payload["files"]) == 2

        # Verify ModuleCodeCollector was called correctly
        mock_collector_class.report_seen_lines.assert_called_once_with(root_dir, include_imported=True)

    @mock.patch("ddtrace.internal.ci_visibility.runtime_coverage.ModuleCodeCollector")
    def test_build_runtime_coverage_payload_no_coverage(self, mock_collector_class):
        """Test building payload when no coverage data is available."""
        # Mock no coverage
        mock_collector_class.report_seen_lines.return_value = None

        root_dir = Path("/app")
        trace_id = 12345
        span_id = 67890

        payload = build_runtime_coverage_payload(root_dir, trace_id, span_id)

        assert payload is None

    @mock.patch("ddtrace.internal.ci_visibility.runtime_coverage.ModuleCodeCollector")
    def test_build_runtime_coverage_payload_empty_coverage(self, mock_collector_class):
        """Test building payload when coverage is empty."""
        # Mock empty coverage
        mock_collector_class.report_seen_lines.return_value = []

        root_dir = Path("/app")
        trace_id = 12345
        span_id = 67890

        payload = build_runtime_coverage_payload(root_dir, trace_id, span_id)

        assert payload is None

    @mock.patch("ddtrace.internal.ci_visibility.runtime_coverage.ModuleCodeCollector")
    def test_build_runtime_coverage_payload_exception(self, mock_collector_class):
        """Test building payload handles exceptions gracefully."""
        # Mock exception
        mock_collector_class.report_seen_lines.side_effect = Exception("Coverage collection failed")

        root_dir = Path("/app")
        trace_id = 12345
        span_id = 67890

        payload = build_runtime_coverage_payload(root_dir, trace_id, span_id)

        assert payload is None


class TestSendRuntimeCoverage:
    """Test sending runtime coverage data."""

    @mock.patch("ddtrace.internal.ci_visibility.runtime_coverage.get_runtime_coverage_writer")
    def test_send_runtime_coverage_success(self, mock_get_writer):
        """Test successfully sending runtime coverage."""
        # Mock writer
        mock_writer = mock.Mock()
        mock_get_writer.return_value = mock_writer

        # Mock span
        mock_span = mock.Mock()
        mock_span.trace_id = 12345
        mock_span.span_id = 67890
        mock_span._set_struct_tag = mock.Mock()

        # Coverage files
        files = [
            {"filename": "/app/views.py", "segments": [[1, 0, 10, 0, -1]]},
        ]

        result = send_runtime_coverage(mock_span, files)

        assert result is True
        # Verify struct tag was set
        mock_span._set_struct_tag.assert_called_once_with(COVERAGE_TAG_NAME, {"files": files})
        # Verify span was written to writer
        mock_writer.write.assert_called_once_with([mock_span])

    @mock.patch("ddtrace.internal.ci_visibility.runtime_coverage.get_runtime_coverage_writer")
    def test_send_runtime_coverage_writer_not_initialized(self, mock_get_writer):
        """Test sending when writer is not initialized."""
        # Mock writer not available
        mock_get_writer.return_value = None

        mock_span = mock.Mock()
        files = [{"filename": "/app/views.py", "segments": [[1, 0, 10, 0, -1]]}]

        result = send_runtime_coverage(mock_span, files)

        assert result is False

    @mock.patch("ddtrace.internal.ci_visibility.runtime_coverage.get_runtime_coverage_writer")
    def test_send_runtime_coverage_exception(self, mock_get_writer):
        """Test sending handles exceptions gracefully."""
        # Mock writer raising exception
        mock_writer = mock.Mock()
        mock_writer.write.side_effect = Exception("Write failed")
        mock_get_writer.return_value = mock_writer

        mock_span = mock.Mock()
        mock_span._set_struct_tag = mock.Mock()
        files = [{"filename": "/app/views.py", "segments": [[1, 0, 10, 0, -1]]}]

        result = send_runtime_coverage(mock_span, files)

        assert result is False

    @mock.patch("ddtrace.internal.ci_visibility.runtime_coverage.get_runtime_coverage_writer")
    def test_send_runtime_coverage_multiple_files(self, mock_get_writer):
        """Test sending coverage for multiple files."""
        mock_writer = mock.Mock()
        mock_get_writer.return_value = mock_writer

        mock_span = mock.Mock()
        mock_span.trace_id = 12345
        mock_span.span_id = 67890
        mock_span._set_struct_tag = mock.Mock()

        # Multiple files
        files = [
            {"filename": "/app/views.py", "segments": [[1, 0, 10, 0, -1]]},
            {"filename": "/app/models.py", "segments": [[5, 0, 15, 0, -1]]},
            {"filename": "/app/utils.py", "segments": [[20, 0, 30, 0, -1]]},
        ]

        result = send_runtime_coverage(mock_span, files)

        assert result is True
        mock_span._set_struct_tag.assert_called_once()
        # Verify all files are included
        call_args = mock_span._set_struct_tag.call_args[0]
        assert call_args[0] == COVERAGE_TAG_NAME
        assert call_args[1]["files"] == files
        assert len(call_args[1]["files"]) == 3
