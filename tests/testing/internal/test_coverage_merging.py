"""
Unit tests for ITR coverage merging functionality.

Tests the bitset decoding and coverage merging logic.
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import Mock

from ddtrace.testing.internal.api_client import APIClient
from ddtrace.testing.internal.coverage_report import _generate_merged_lcov_from_coverage_py
from ddtrace.testing.internal.test_data import ITRSkippingLevel


class TestBitsetDecoding:
    """Test bitset to line numbers conversion."""

    def test_empty_bitset(self):
        """Test that an empty bitset returns an empty set."""
        client = self._create_api_client()
        result = client._bitset_to_line_numbers(b"")
        assert result == set()

    def test_single_byte_single_bit(self):
        """Test a bitset with a single bit set."""
        client = self._create_api_client()
        # Bit 0 set in byte 0 -> line 0 (should be filtered out)
        # Bit 1 set in byte 0 -> line 1
        bitset = bytes([0b00000010])  # Bit 1 set
        result = client._bitset_to_line_numbers(bitset)
        assert result == {1}

    def test_single_byte_multiple_bits(self):
        """Test a bitset with multiple bits set in one byte."""
        client = self._create_api_client()
        # Bits 1, 3, 5 set in byte 0 -> lines 1, 3, 5
        bitset = bytes([0b00101010])
        result = client._bitset_to_line_numbers(bitset)
        assert result == {1, 3, 5}

    def test_multiple_bytes(self):
        """Test a bitset spanning multiple bytes."""
        client = self._create_api_client()
        # Byte 0: bits 1, 2 -> lines 1, 2
        # Byte 1: bits 0, 3 -> lines 8, 11
        bitset = bytes([0b00000110, 0b00001001])
        result = client._bitset_to_line_numbers(bitset)
        assert result == {1, 2, 8, 11}

    def test_real_world_bitset(self):
        """Test with a base64-encoded bitset like the backend would send."""
        client = self._create_api_client()
        # Create a bitset with lines 5, 10, 15, 20, 100
        bitset = bytearray(13)  # Need at least 13 bytes for line 100
        bitset[0] |= 1 << 5  # Line 5
        bitset[1] |= 1 << 2  # Line 10
        bitset[1] |= 1 << 7  # Line 15
        bitset[2] |= 1 << 4  # Line 20
        bitset[12] |= 1 << 4  # Line 100

        result = client._bitset_to_line_numbers(bytes(bitset))
        assert result == {5, 10, 15, 20, 100}

    def test_filters_out_line_zero(self):
        """Test that line 0 is filtered out."""
        client = self._create_api_client()
        # Bit 0 in byte 0 -> line 0 (invalid)
        bitset = bytes([0b00000001])
        result = client._bitset_to_line_numbers(bitset)
        assert result == set()

    def _create_api_client(self) -> APIClient:
        """Helper to create an APIClient instance for testing."""
        mock_connector_setup = Mock()
        mock_connector = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector
        mock_telemetry = Mock()

        return APIClient(
            service="test-service",
            env="test",
            env_tags={
                "git.repository_url": "https://github.com/test/repo",
                "git.commit.sha": "abc123",
                "git.branch": "main",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={},
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )


class TestCoverageMerging:
    """Test coverage merging logic."""

    def test_merge_with_empty_backend_coverage(self):
        """Test that merging with empty backend coverage returns local coverage."""
        # This would be tested in the full integration flow
        # For now, we test the helper functions that do the merging
        pass

    def test_merge_overlapping_coverage(self):
        """Test merging when local and backend coverage overlap."""
        # Create mock coverage.py instance
        mock_cov = Mock()
        mock_cov.get_data.return_value = Mock(
            measured_files=lambda: ["/workspace/src/file1.py"],
            lines=lambda path: [1, 2, 3, 4] if "file1" in path else [],
            missing=lambda path: [5, 6] if "file1" in path else [],  # Add missing lines mock
        )

        workspace_path = Path("/workspace")
        skippable_coverage = {
            "src/file1.py": {3, 4, 5, 6},  # Lines 3, 4 overlap with local
        }

        result = _generate_merged_lcov_from_coverage_py(mock_cov, workspace_path, skippable_coverage)

        assert result is not None
        lcov_str = result.decode("utf-8")

        # Should contain the merged lines (1, 2, 3, 4, 5, 6)
        assert "SF:src/file1.py" in lcov_str
        assert "DA:1,1" in lcov_str
        assert "DA:2,1" in lcov_str
        assert "DA:3,1" in lcov_str
        assert "DA:4,1" in lcov_str
        assert "DA:5,1" in lcov_str
        assert "DA:6,1" in lcov_str

    def test_merge_non_overlapping_files(self):
        """Test merging when backend has different files than local."""
        # Create mock coverage.py instance
        mock_cov = Mock()
        mock_cov.get_data.return_value = Mock(
            measured_files=lambda: ["/workspace/src/file1.py"],
            lines=lambda path: [1, 2] if "file1" in path else [],
            missing=lambda path: [3, 4] if "file1" in path else [],  # Add missing lines mock
        )

        workspace_path = Path("/workspace")
        skippable_coverage = {
            "src/file2.py": {10, 20, 30},  # Different file
        }

        result = _generate_merged_lcov_from_coverage_py(mock_cov, workspace_path, skippable_coverage)

        assert result is not None
        lcov_str = result.decode("utf-8")

        # Should contain both files
        assert "SF:src/file1.py" in lcov_str
        assert "SF:src/file2.py" in lcov_str
        assert "DA:1,1" in lcov_str
        assert "DA:2,1" in lcov_str
        assert "DA:10,1" in lcov_str
        assert "DA:20,1" in lcov_str
        assert "DA:30,1" in lcov_str

    def test_module_collector_merge_with_backend_coverage(self):
        """Test ModuleCodeCollector merging with backend coverage."""
        # This requires a more complex setup with ModuleCodeCollector
        # For now, we'll test the logic through integration tests
        pass


class TestBackendResponseParsing:
    """Test parsing of coverage data from backend API responses."""

    def test_parse_coverage_from_meta(self):
        """Test extracting coverage from the meta.coverage field."""
        client = self._create_api_client()

        # Create a mock response with coverage data
        # Line 5 in file1.py
        bitset1 = bytearray(2)
        bitset1[0] |= 1 << 5
        encoded1 = base64.b64encode(bytes(bitset1)).decode("ascii")

        # Line 10 in file2.py
        bitset2 = bytearray(2)
        bitset2[1] |= 1 << 2
        encoded2 = base64.b64encode(bytes(bitset2)).decode("ascii")

        mock_response = {
            "data": [],
            "meta": {
                "correlation_id": "test-correlation-id",
                "coverage": {
                    "/src/file1.py": encoded1,
                    "src/file2.py": encoded2,
                },
            },
        }

        client.connector.post_json.return_value = Mock(
            parsed_response=mock_response,
            on_error_raise_exception=Mock(),
            error_type=None,
        )

        skippable_items, correlation_id, coverage = client.get_skippable_tests()

        assert correlation_id == "test-correlation-id"
        assert "src/file1.py" in coverage
        assert "src/file2.py" in coverage
        assert 5 in coverage["src/file1.py"]
        assert 10 in coverage["src/file2.py"]

    def test_parse_empty_coverage(self):
        """Test handling when no coverage data is present."""
        client = self._create_api_client()

        mock_response = {
            "data": [],
            "meta": {
                "correlation_id": "test-correlation-id",
            },
        }

        client.connector.post_json.return_value = Mock(
            parsed_response=mock_response,
            on_error_raise_exception=Mock(),
            error_type=None,
        )

        skippable_items, correlation_id, coverage = client.get_skippable_tests()

        assert correlation_id == "test-correlation-id"
        assert coverage == {}

    def test_parse_invalid_base64_coverage(self):
        """Test handling of invalid base64 in coverage data."""
        client = self._create_api_client()

        mock_response = {
            "data": [],
            "meta": {
                "correlation_id": "test-correlation-id",
                "coverage": {
                    "src/file1.py": "invalid-base64!!!",
                },
            },
        }

        client.connector.post_json.return_value = Mock(
            parsed_response=mock_response,
            on_error_raise_exception=Mock(),
            error_type=None,
        )

        # Should handle gracefully and skip the invalid file
        skippable_items, correlation_id, coverage = client.get_skippable_tests()

        assert correlation_id == "test-correlation-id"
        # Invalid file should not be in coverage
        assert "src/file1.py" not in coverage

    def _create_api_client(self) -> APIClient:
        """Helper to create an APIClient instance for testing."""
        mock_connector_setup = Mock()
        mock_connector = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector
        mock_telemetry = Mock()
        mock_telemetry.with_request_metric_names.return_value = mock_telemetry
        mock_telemetry.record_error = Mock()
        mock_telemetry.record_skippable_count = Mock()

        return APIClient(
            service="test-service",
            env="test",
            env_tags={
                "git.repository_url": "https://github.com/test/repo",
                "git.commit.sha": "abc123",
                "git.branch": "main",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={},
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )
