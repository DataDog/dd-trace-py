"""Tests for CIVisibility recorder coverage upload functionality."""

from unittest.mock import Mock
from unittest.mock import patch

from ddtrace.internal.ci_visibility.encoder import CIVisibilityCoverageReportEncoder
from ddtrace.internal.ci_visibility.git_data import GitData
from ddtrace.internal.ci_visibility.recorder import CIVisibility
from ddtrace.internal.ci_visibility.writer import CIVisibilityCoverageReportClient
from ddtrace.internal.ci_visibility.writer import CIVisibilityWriter


class TestCIVisibilityRecorderCoverageUpload:
    """Test suite for CIVisibility.upload_coverage_report method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.recorder = CIVisibility()

        # Mock API settings
        self.mock_api_settings = Mock()
        self.mock_api_settings.coverage_report_upload_enabled = True
        self.recorder._api_settings = self.mock_api_settings

        # Set up service and env
        self.recorder._service = "test-service"
        self.recorder._dd_env = "test-env"

        # Set up git data
        self.recorder._git_data = GitData(
            repository_url="https://github.com/DataDog/dd-trace-py",
            branch="main",
            commit_sha="abc123def456",
            commit_message="Test commit",
        )

        # Set up mock writer and client
        self.mock_writer = Mock(spec=CIVisibilityWriter)
        self.mock_coverage_client = Mock(spec=CIVisibilityCoverageReportClient)
        self.mock_encoder = CIVisibilityCoverageReportEncoder()
        self.mock_coverage_client.coverage_encoder = self.mock_encoder
        self.mock_writer._clients = [self.mock_coverage_client]

        # Mock tracer
        self.recorder.tracer = Mock()
        self.recorder.tracer._span_aggregator = Mock()
        self.recorder.tracer._span_aggregator.writer = self.mock_writer

    def test_upload_coverage_report_success(self):
        """Test successful coverage report upload."""
        # Mock successful HTTP response
        mock_response = Mock(status=200)
        self.mock_writer._put.return_value = mock_response

        test_report = b"SF:test_file.py\nDA:1,1\nDA:2,1\nend_of_record"

        with patch("time.time", return_value=1234567890.123):
            result = self.recorder.upload_coverage_report(test_report, "lcov")

        assert result is True

        # Verify _put was called with correct parameters
        self.mock_writer._put.assert_called_once()
        call_args = self.mock_writer._put.call_args

        assert call_args.kwargs["client"] == self.mock_coverage_client
        assert call_args.kwargs["no_trace"] is True

        # Verify headers
        headers = call_args.kwargs["headers"]
        assert "Content-Type" in headers
        assert "multipart/form-data" in headers["Content-Type"]

        # Verify encoded data is bytes
        data = call_args.kwargs["data"]
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_upload_coverage_report_disabled_in_api_settings(self):
        """Test that upload is skipped when disabled in API settings."""
        self.mock_api_settings.coverage_report_upload_enabled = False

        test_report = b"SF:test_file.py\nDA:1,1\nend_of_record"
        result = self.recorder.upload_coverage_report(test_report, "lcov")

        assert result is False
        self.mock_writer._put.assert_not_called()

    def test_upload_coverage_report_no_api_settings(self):
        """Test that upload is skipped when no API settings are available."""
        self.recorder._api_settings = None

        test_report = b"SF:test_file.py\nDA:1,1\nend_of_record"
        result = self.recorder.upload_coverage_report(test_report, "lcov")

        assert result is False
        self.mock_writer._put.assert_not_called()

    def test_upload_coverage_report_wrong_writer_type(self):
        """Test that upload is skipped when writer is not CIVisibilityWriter."""
        # Mock different writer type
        mock_other_writer = Mock()
        self.recorder.tracer._span_aggregator.writer = mock_other_writer

        test_report = b"SF:test_file.py\nDA:1,1\nend_of_record"
        result = self.recorder.upload_coverage_report(test_report, "lcov")

        assert result is False

    def test_upload_coverage_report_no_coverage_client(self):
        """Test graceful handling when no coverage report client is available."""
        # Set up writer with no coverage clients
        self.mock_writer._clients = [Mock()]  # Different type of client

        test_report = b"SF:test_file.py\nDA:1,1\nend_of_record"
        result = self.recorder.upload_coverage_report(test_report, "lcov")

        assert result is False
        self.mock_writer._put.assert_not_called()

    def test_upload_coverage_report_http_error(self):
        """Test handling of HTTP error responses."""
        # Mock HTTP error response
        mock_response = Mock(status=500)
        self.mock_writer._put.return_value = mock_response

        test_report = b"SF:test_file.py\nDA:1,1\nend_of_record"
        result = self.recorder.upload_coverage_report(test_report, "lcov")

        assert result is False
        self.mock_writer._put.assert_called_once()

    def test_upload_coverage_report_exception_handling(self):
        """Test graceful handling of exceptions during upload."""
        # Mock exception during _put
        self.mock_writer._put.side_effect = Exception("Network error")

        test_report = b"SF:test_file.py\nDA:1,1\nend_of_record"
        result = self.recorder.upload_coverage_report(test_report, "lcov")

        assert result is False

    def test_upload_coverage_report_event_data_structure(self):
        """Test that event data is structured correctly."""
        mock_response = Mock(status=200)
        self.mock_writer._put.return_value = mock_response

        test_report = b"SF:test_file.py\nDA:1,1\nend_of_record"

        with patch("time.time", return_value=1234567890.123):
            self.recorder.upload_coverage_report(test_report, "lcov")

        # Get the encoded data and verify the event structure
        call_args = self.mock_writer._put.call_args
        encoded_data = call_args.kwargs["data"]

        # The event data should contain our structured information
        # We can't easily parse multipart data in tests, but we can verify
        # it contains the expected strings
        encoded_str = encoded_data.decode("utf-8", errors="ignore")

        # Check for expected event data fields
        assert '"type": "coverage_report"' in encoded_str
        assert '"format": "lcov"' in encoded_str
        assert '"timestamp": 1234567890123' in encoded_str  # milliseconds
        assert '"service": "test-service"' in encoded_str
        assert '"env": "test-env"' in encoded_str

        # Git data
        assert '"git.repository_url": "https://github.com/DataDog/dd-trace-py"' in encoded_str
        assert '"git.commit.sha": "abc123def456"' in encoded_str
        assert '"git.branch": "main"' in encoded_str

    def test_upload_coverage_report_without_git_data(self):
        """Test upload works without git data."""
        self.recorder._git_data = None

        mock_response = Mock(status=200)
        self.mock_writer._put.return_value = mock_response

        test_report = b"SF:test_file.py\nDA:1,1\nend_of_record"
        result = self.recorder.upload_coverage_report(test_report, "lcov")

        assert result is True

        # Verify call was still made
        self.mock_writer._put.assert_called_once()

    def test_upload_coverage_report_without_service_env(self):
        """Test upload works without service and env data."""
        self.recorder._service = None
        self.recorder._dd_env = None

        mock_response = Mock(status=200)
        self.mock_writer._put.return_value = mock_response

        test_report = b"SF:test_file.py\nDA:1,1\nend_of_record"
        result = self.recorder.upload_coverage_report(test_report, "lcov")

        assert result is True
        self.mock_writer._put.assert_called_once()

    def test_upload_coverage_report_different_formats(self):
        """Test upload with different coverage formats."""
        mock_response = Mock(status=201)  # Different success status
        self.mock_writer._put.return_value = mock_response

        test_cases = [
            ("lcov", b"SF:test.py\nDA:1,1\nend_of_record"),
            ("cobertura", b"<coverage><line hits='1'/></coverage>"),
            ("jacoco", b"<report><counter type='LINE'/></report>"),
        ]

        for format_type, test_data in test_cases:
            self.mock_writer._put.reset_mock()

            result = self.recorder.upload_coverage_report(test_data, format_type)

            assert result is True
            self.mock_writer._put.assert_called_once()

            # Verify format is included in the data
            call_args = self.mock_writer._put.call_args
            encoded_data = call_args.kwargs["data"]
            encoded_str = encoded_data.decode("utf-8", errors="ignore")
            assert f'"format": "{format_type}"' in encoded_str

    def test_upload_coverage_report_empty_data(self):
        """Test upload with empty coverage data."""
        mock_response = Mock(status=200)
        self.mock_writer._put.return_value = mock_response

        result = self.recorder.upload_coverage_report(b"", "lcov")

        # Should still attempt upload even with empty data
        assert result is True
        self.mock_writer._put.assert_called_once()

    def test_upload_coverage_report_large_data(self):
        """Test upload with large coverage data."""
        mock_response = Mock(status=200)
        self.mock_writer._put.return_value = mock_response

        # Create large test data
        large_report = b""
        for i in range(1000):
            large_report += f"SF:file{i}.py\nDA:1,1\nDA:2,1\nend_of_record\n".encode()

        result = self.recorder.upload_coverage_report(large_report, "lcov")

        assert result is True
        self.mock_writer._put.assert_called_once()

    def test_upload_coverage_report_multiple_clients(self):
        """Test that upload finds the correct client among multiple clients."""
        # Add another client to the writer
        mock_other_client = Mock()
        self.mock_writer._clients = [mock_other_client, self.mock_coverage_client]

        mock_response = Mock(status=200)
        self.mock_writer._put.return_value = mock_response

        test_report = b"SF:test_file.py\nDA:1,1\nend_of_record"
        result = self.recorder.upload_coverage_report(test_report, "lcov")

        assert result is True

        # Verify the correct client was used
        call_args = self.mock_writer._put.call_args
        assert call_args.kwargs["client"] == self.mock_coverage_client

    @patch("ddtrace.internal.ci_visibility.recorder.log")
    def test_upload_coverage_report_logging(self, mock_log):
        """Test that appropriate log messages are generated."""
        # Test success case
        mock_response = Mock(status=200)
        self.mock_writer._put.return_value = mock_response

        test_report = b"SF:test_file.py\nDA:1,1\nend_of_record"
        result = self.recorder.upload_coverage_report(test_report, "lcov")

        assert result is True
        mock_log.info.assert_called_with("Successfully uploaded coverage report")

        # Test failure case
        mock_log.reset_mock()
        mock_response = Mock(status=500)
        self.mock_writer._put.return_value = mock_response

        result = self.recorder.upload_coverage_report(test_report, "lcov")

        assert result is False
        mock_log.error.assert_called_with("Coverage report upload failed: HTTP %d", 500)

    @patch("ddtrace.internal.ci_visibility.recorder.log")
    def test_upload_coverage_report_exception_logging(self, mock_log):
        """Test that exceptions are properly logged."""
        self.mock_writer._put.side_effect = RuntimeError("Test error")

        test_report = b"SF:test_file.py\nDA:1,1\nend_of_record"
        result = self.recorder.upload_coverage_report(test_report, "lcov")

        assert result is False
        mock_log.exception.assert_called_with(
            "Error uploading coverage report through recorder: %s", mock_log.exception.call_args[0][1]
        )
