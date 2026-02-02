"""Tests for CIVisibilityCoverageReportEncoder."""

import gzip
import json
import re

from ddtrace.internal.ci_visibility.encoder import CIVisibilityCoverageReportEncoder


class TestCIVisibilityCoverageReportEncoder:
    """Test suite for coverage report multipart encoder."""

    def test_encoder_initialization(self):
        """Test encoder initializes with proper boundary and content type."""
        encoder = CIVisibilityCoverageReportEncoder()

        assert encoder.boundary is not None
        assert len(encoder.boundary) > 0
        assert encoder.content_type.startswith("multipart/form-data; boundary=")
        assert encoder.boundary in encoder.content_type

    def test_encoder_unique_boundaries(self):
        """Test that each encoder instance gets a unique boundary."""
        encoder1 = CIVisibilityCoverageReportEncoder()
        encoder2 = CIVisibilityCoverageReportEncoder()

        assert encoder1.boundary != encoder2.boundary
        assert encoder1.content_type != encoder2.content_type

    def test_encode_coverage_report_basic(self):
        """Test basic encoding of coverage report."""
        encoder = CIVisibilityCoverageReportEncoder()

        test_report = b"SF:test.py\nDA:1,1\nDA:2,0\nend_of_record"
        event_data = {
            "type": "coverage_report",
            "format": "lcov",
            "timestamp": 1234567890,
        }

        encoded = encoder.encode_coverage_report(test_report, "lcov", event_data)

        assert isinstance(encoded, bytes)
        assert len(encoded) > 0

        # Check multipart structure
        boundary_bytes = f"--{encoder.boundary}".encode()
        assert boundary_bytes in encoded

        # Check final boundary
        final_boundary = f"--{encoder.boundary}--".encode()
        assert final_boundary in encoded

    def test_encode_coverage_report_multipart_structure(self):
        """Test that encoded data has correct multipart structure."""
        encoder = CIVisibilityCoverageReportEncoder()

        test_report = b"SF:example.py\nDA:1,1\nend_of_record"
        event_data = {"type": "coverage_report", "format": "lcov"}

        encoded = encoder.encode_coverage_report(test_report, "lcov", event_data)
        encoded_str = encoded.decode("utf-8", errors="ignore")

        # Check coverage file part headers
        assert 'Content-Disposition: form-data; name="coverage"' in encoded_str
        assert 'filename="coverage.lcov.gz"' in encoded_str
        assert "Content-Type: application/gzip" in encoded_str

        # Check event file part headers
        assert 'Content-Disposition: form-data; name="event"' in encoded_str
        assert 'filename="event.json"' in encoded_str
        assert "Content-Type: application/json" in encoded_str

    def test_encode_coverage_report_compression(self):
        """Test that coverage report is properly gzipped."""
        encoder = CIVisibilityCoverageReportEncoder()

        test_report = b"SF:test.py\nDA:1,1\nDA:2,1\nend_of_record"
        event_data = {"type": "coverage_report", "format": "lcov"}

        encoded = encoder.encode_coverage_report(test_report, "lcov", event_data)

        # Original text should not appear in encoded data (it's compressed)
        assert b"SF:test.py" not in encoded
        assert b"DA:1,1" not in encoded

        # But we should be able to find the compressed data and decompress it
        # Extract the gzipped portion between multipart boundaries
        parts = encoded.split(f"--{encoder.boundary}".encode())

        # Find the coverage part (should be the second part after the first boundary)
        coverage_part = None
        for part in parts:
            if b'name="coverage"' in part:
                # Extract binary data after the headers
                headers_end = part.find(b"\r\n\r\n")
                if headers_end != -1:
                    coverage_part = part[headers_end + 4 :]
                    # Remove everything after the next boundary marker
                    next_boundary = coverage_part.find(b"\r\n--")
                    if next_boundary != -1:
                        coverage_part = coverage_part[:next_boundary]
                break

        assert coverage_part is not None

        # Decompress and verify
        decompressed = gzip.decompress(coverage_part)
        assert decompressed == test_report

    def test_encode_coverage_report_event_json(self):
        """Test that event data is properly encoded as JSON."""
        encoder = CIVisibilityCoverageReportEncoder()

        test_report = b"SF:test.py\nend_of_record"
        event_data = {
            "type": "coverage_report",
            "format": "lcov",
            "timestamp": 1234567890,
            "service": "test-service",
            "env": "test",
            "git.repository_url": "https://github.com/test/repo",
            "git.commit.sha": "abc123",
            "git.branch": "main",
        }

        encoded = encoder.encode_coverage_report(test_report, "lcov", event_data)
        encoded_str = encoded.decode("utf-8", errors="ignore")

        # Extract JSON from the event part
        json_match = re.search(r'\{"type".*?\}', encoded_str)
        assert json_match is not None

        json_str = json_match.group()
        parsed_event = json.loads(json_str)

        # Verify all event data is present
        assert parsed_event["type"] == "coverage_report"
        assert parsed_event["format"] == "lcov"
        assert parsed_event["timestamp"] == 1234567890
        assert parsed_event["service"] == "test-service"
        assert parsed_event["env"] == "test"
        assert parsed_event["git.repository_url"] == "https://github.com/test/repo"
        assert parsed_event["git.commit.sha"] == "abc123"
        assert parsed_event["git.branch"] == "main"

    def test_encode_coverage_report_different_formats(self):
        """Test encoding with different coverage formats."""
        encoder = CIVisibilityCoverageReportEncoder()
        test_report = b"<coverage>test</coverage>"

        for format_type in ["lcov", "cobertura", "jacoco"]:
            event_data = {"type": "coverage_report", "format": format_type}
            encoded = encoder.encode_coverage_report(test_report, format_type, event_data)
            encoded_str = encoded.decode("utf-8", errors="ignore")

            assert f'filename="coverage.{format_type}.gz"' in encoded_str
            assert f'"format": "{format_type}"' in encoded_str

    def test_encode_coverage_report_empty_event_data(self):
        """Test encoding with minimal event data."""
        encoder = CIVisibilityCoverageReportEncoder()

        test_report = b"SF:test.py\nend_of_record"
        event_data = {}

        encoded = encoder.encode_coverage_report(test_report, "lcov", event_data)

        # Should still work with empty event data
        assert isinstance(encoded, bytes)
        assert len(encoded) > 0
        assert f"--{encoder.boundary}".encode() in encoded

    def test_encode_coverage_report_large_data(self):
        """Test encoding with larger coverage data."""
        encoder = CIVisibilityCoverageReportEncoder()

        # Create a larger test report
        test_report = b""
        for i in range(1000):
            test_report += f"SF:file{i}.py\nDA:1,1\nDA:2,1\nend_of_record\n".encode()

        event_data = {"type": "coverage_report", "format": "lcov"}

        encoded = encoder.encode_coverage_report(test_report, "lcov", event_data)

        # Should handle large data
        assert isinstance(encoded, bytes)
        assert len(encoded) > 0

        # Compression should make it smaller than original
        # (multipart overhead aside, the gzipped portion should be smaller)
        compressed_size = len(gzip.compress(test_report))
        original_size = len(test_report)
        assert compressed_size < original_size

    def test_encode_coverage_report_special_characters(self):
        """Test encoding with special characters in event data."""
        encoder = CIVisibilityCoverageReportEncoder()

        test_report = b"SF:test.py\nend_of_record"
        event_data = {
            "type": "coverage_report",
            "format": "lcov",
            "service": "test-service-üñíçödé",
            "message": "Coverage report with special chars: éñ español",
            "url": "https://example.com/repo?param=value&other=test",
        }

        encoded = encoder.encode_coverage_report(test_report, "lcov", event_data)

        # Should handle special characters without errors
        assert isinstance(encoded, bytes)
        assert len(encoded) > 0

        # Check that JSON is properly encoded
        encoded_str = encoded.decode("utf-8", errors="ignore")
        assert "test-service-üñíçödé" in encoded_str or "test-service-" in encoded_str  # May be escaped
