"""
Unit tests for coverage data processing functionality.

This module tests the coverage data parsing, bitset decoding, and processing utilities.
"""

from __future__ import annotations

import base64

from ddtrace.testing.internal.coverage_data import CoverageDataProcessor
from ddtrace.testing.internal.coverage_data import bitset_to_line_numbers
from ddtrace.testing.internal.coverage_data import parse_coverage_data


class TestBitsetDecoding:
    """Test bitset to line numbers conversion."""

    def test_empty_bitset(self):
        """Test that an empty bitset returns an empty set."""
        result = bitset_to_line_numbers(b"")
        assert result == set()

    def test_single_byte_single_bit(self):
        """Test a bitset with a single bit set."""
        # Bit 1 set in byte 0 -> line 1
        bitset = bytes([0b00000010])
        result = bitset_to_line_numbers(bitset)
        assert result == {1}

    def test_single_byte_multiple_bits(self):
        """Test multiple bits set in a single byte."""
        # Bits 1, 3, 5 set in byte 0 -> lines 1, 3, 5
        bitset = bytes([0b00101010])
        result = bitset_to_line_numbers(bitset)
        assert result == {1, 3, 5}

    def test_multiple_bytes(self):
        """Test bitset spanning multiple bytes."""
        # Bit 1 in byte 0 -> line 1
        # Bit 0 in byte 1 -> line 8
        # Bit 7 in byte 1 -> line 15
        bitset = bytes([0b00000010, 0b10000001])
        result = bitset_to_line_numbers(bitset)
        assert result == {1, 8, 15}

    def test_real_world_bitset(self):
        """Test with a more complex, real-world-like bitset."""
        # Create a bitset with lines 5, 10, 15, 20, 100
        bitset = bytearray(13)  # Need at least 13 bytes for line 100
        bitset[0] |= 1 << 5  # Line 5
        bitset[1] |= 1 << 2  # Line 10
        bitset[1] |= 1 << 7  # Line 15
        bitset[2] |= 1 << 4  # Line 20
        bitset[12] |= 1 << 4  # Line 100

        result = bitset_to_line_numbers(bytes(bitset))
        assert result == {5, 10, 15, 20, 100}

    def test_filters_out_line_zero(self):
        """Test that line 0 is filtered out."""
        # Bit 0 in byte 0 -> line 0 (invalid)
        bitset = bytes([0b00000001])
        result = bitset_to_line_numbers(bitset)
        assert result == set()


class TestCoverageDataParsing:
    """Test coverage data parsing functionality."""

    def test_parse_empty_coverage_data(self):
        """Test parsing empty coverage data."""
        result = parse_coverage_data({})
        assert result == {}

    def test_parse_coverage_data_single_file(self):
        """Test parsing coverage data for a single file."""
        # Create base64-encoded bitset for lines 1, 3, 5
        bitset = bytes([0b00101010])
        encoded_bitset = base64.b64encode(bitset).decode()

        coverage_data = {"src/file1.py": encoded_bitset}

        result = parse_coverage_data(coverage_data)

        expected = {"src/file1.py": {1, 3, 5}}
        assert result == expected

    def test_parse_coverage_data_multiple_files(self):
        """Test parsing coverage data for multiple files."""
        # Different bitsets for different files
        bitset1 = bytes([0b00000010])  # Line 1
        bitset2 = bytes([0b00001000])  # Line 3

        encoded_bitset1 = base64.b64encode(bitset1).decode()
        encoded_bitset2 = base64.b64encode(bitset2).decode()

        coverage_data = {
            "/src/file1.py": encoded_bitset1,
            "src/file2.py": encoded_bitset2,
        }

        result = parse_coverage_data(coverage_data)

        expected = {
            "src/file1.py": {1},  # Leading slash removed
            "src/file2.py": {3},
        }
        assert result == expected

    def test_parse_invalid_base64(self):
        """Test handling of invalid base64 data."""
        coverage_data = {
            "valid_file.py": base64.b64encode(bytes([0b00000010])).decode(),
            "invalid_file.py": "invalid-base64!!!",
        }

        result = parse_coverage_data(coverage_data)

        # Should only include the valid file
        expected = {"valid_file.py": {1}}
        assert result == expected


class TestCoverageDataProcessor:
    """Test CoverageDataProcessor class functionality."""

    def test_parse_backend_coverage_empty_meta(self):
        """Test parsing empty metadata."""
        processor = CoverageDataProcessor()
        result = processor.parse_backend_coverage({})
        assert result == {}

    def test_parse_backend_coverage_no_coverage(self):
        """Test parsing metadata with no coverage field."""
        processor = CoverageDataProcessor()
        meta_data = {"correlation_id": "abc123"}
        result = processor.parse_backend_coverage(meta_data)
        assert result == {}

    def test_parse_backend_coverage_with_data(self):
        """Test parsing coverage data from backend response."""
        processor = CoverageDataProcessor()

        # Create base64-encoded bitset for testing
        bitset = bytes([0b00000110])  # Lines 1, 2 set
        encoded_bitset = base64.b64encode(bitset).decode()

        meta_data = {
            "coverage": {
                "/src/file1.py": encoded_bitset,
                "src/file2.py": encoded_bitset,
            },
            "correlation_id": "test-123",
        }

        result = processor.parse_backend_coverage(meta_data)

        expected = {
            "src/file1.py": {1, 2},
            "src/file2.py": {1, 2},
        }
        assert result == expected

    def test_merge_coverage_sources_no_overlap(self):
        """Test merging coverage with no overlapping files."""
        processor = CoverageDataProcessor()

        local_coverage = {"file1.py": {1, 2, 3}}
        backend_coverage = {"file2.py": {4, 5, 6}}

        result = processor.merge_coverage_sources(local_coverage, backend_coverage)

        expected = {
            "file1.py": {1, 2, 3},
            "file2.py": {4, 5, 6},
        }
        assert result == expected

    def test_merge_coverage_sources_with_overlap(self):
        """Test merging coverage with overlapping files and lines."""
        processor = CoverageDataProcessor()

        local_coverage = {
            "file1.py": {1, 2, 3},
            "file2.py": {5, 6},
        }

        backend_coverage = {
            "file1.py": {3, 4, 5},  # Overlaps with local on line 3
            "file3.py": {10, 11},  # New file
        }

        result = processor.merge_coverage_sources(local_coverage, backend_coverage)

        expected = {
            "file1.py": {1, 2, 3, 4, 5},  # Merged (union)
            "file2.py": {5, 6},  # Local only
            "file3.py": {10, 11},  # Backend only
        }
        assert result == expected

    def test_merge_coverage_sources_empty_local(self):
        """Test merging with empty local coverage."""
        processor = CoverageDataProcessor()

        local_coverage = {}
        backend_coverage = {"file1.py": {1, 2, 3}}

        result = processor.merge_coverage_sources(local_coverage, backend_coverage)

        expected = {"file1.py": {1, 2, 3}}
        assert result == expected

    def test_merge_coverage_sources_empty_backend(self):
        """Test merging with empty backend coverage."""
        processor = CoverageDataProcessor()

        local_coverage = {"file1.py": {1, 2, 3}}
        backend_coverage = {}

        result = processor.merge_coverage_sources(local_coverage, backend_coverage)

        expected = {"file1.py": {1, 2, 3}}
        assert result == expected

    def test_normalize_file_paths_removes_leading_slashes(self):
        """Test that leading slashes are removed from file paths."""
        processor = CoverageDataProcessor()

        coverage_data = {
            "/src/file1.py": {1, 2},
            "/app/src/file2.py": {3, 4},
            "already/normalized.py": {5, 6},
        }

        result = processor.normalize_file_paths(coverage_data)

        expected = {
            "src/file1.py": {1, 2},
            "app/src/file2.py": {3, 4},
            "already/normalized.py": {5, 6},
        }
        assert result == expected

    def test_normalize_file_paths_with_base_path(self):
        """Test path normalization with a base path."""
        processor = CoverageDataProcessor()

        coverage_data = {
            "file1.py": {1, 2},
            "utils/file2.py": {3, 4},
        }

        result = processor.normalize_file_paths(coverage_data, base_path="src")

        expected = {
            "src/file1.py": {1, 2},
            "src/utils/file2.py": {3, 4},
        }
        assert result == expected

    def test_normalize_file_paths_no_duplicate_base_path(self):
        """Test that base path is not duplicated if already present."""
        processor = CoverageDataProcessor()

        coverage_data = {
            "src/file1.py": {1, 2},
            "other/file2.py": {3, 4},
        }

        result = processor.normalize_file_paths(coverage_data, base_path="src")

        expected = {
            "src/file1.py": {1, 2},  # No duplication
            "src/other/file2.py": {3, 4},  # Base path added
        }
        assert result == expected
