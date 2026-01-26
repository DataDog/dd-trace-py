"""
Coverage data processing and parsing utilities.

This module handles parsing, decoding, and processing of coverage data from various sources
including backend API responses with BitSet-encoded coverage information.
"""

from __future__ import annotations

import base64
import logging
import typing as t


log = logging.getLogger(__name__)


def bitset_to_line_numbers(bitset_bytes: bytes) -> t.Set[int]:
    """
    Convert a bitset (as bytes) to a set of line numbers.

    Args:
        bitset_bytes: The bitset encoded as bytes (from base64 decoding)

    Returns:
        Set of line numbers where bits are set (line numbers start from 1)
    """
    covered_lines: t.Set[int] = set()

    for byte_idx, byte_val in enumerate(bitset_bytes):
        if byte_val == 0:
            # Optimization: skip bytes with no bits set
            continue

        for bit_idx in range(8):
            if byte_val & (1 << bit_idx):
                line_number = byte_idx * 8 + bit_idx
                if line_number > 0:  # Skip line 0 (not a valid source line)
                    covered_lines.add(line_number)

    return covered_lines


def parse_coverage_data(coverage_data: t.Dict[str, str]) -> t.Dict[str, t.Set[int]]:
    """
    Parse coverage data from API response format.

    Args:
        coverage_data: Dictionary mapping file paths to base64-encoded BitSets

    Returns:
        Dictionary mapping normalized file paths to sets of covered line numbers
    """
    parsed_coverage: t.Dict[str, t.Set[int]] = {}

    if not coverage_data:
        return parsed_coverage

    log.debug("Parsing coverage data for %d files from skippable tests", len(coverage_data))

    for file_path, encoded_bitset in coverage_data.items():
        try:
            # Decode base64 to bytes
            decoded_bytes = base64.b64decode(encoded_bitset)
            # Convert bytes to set of line numbers
            covered_lines = bitset_to_line_numbers(decoded_bytes)
            # Normalize path (remove leading slash if present)
            normalized_path = file_path.lstrip("/")
            parsed_coverage[normalized_path] = covered_lines
            log.debug("Parsed coverage for %s: %d lines covered", normalized_path, len(covered_lines))
        except Exception:
            log.exception("Failed to parse coverage data for file: %s", file_path)
            continue

    return parsed_coverage


class CoverageDataProcessor:
    """
    Handles processing and transformation of coverage data from various sources.

    This class encapsulates the logic for:
    - Parsing backend coverage data (BitSet format)
    - Merging coverage data from different sources
    - Normalizing file paths and line numbers
    """

    def parse_backend_coverage(self, meta_data: t.Dict[str, t.Any]) -> t.Dict[str, t.Set[int]]:
        """
        Parse coverage data from API response metadata.

        Args:
            meta_data: Metadata dictionary from API response

        Returns:
            Dictionary mapping file paths to sets of covered line numbers
        """
        coverage_data = meta_data.get("coverage", {})
        return parse_coverage_data(coverage_data)

    def merge_coverage_sources(
        self, local_coverage: t.Dict[str, t.Set[int]], backend_coverage: t.Dict[str, t.Set[int]]
    ) -> t.Dict[str, t.Set[int]]:
        """
        Merge coverage data from local execution and backend sources.

        Args:
            local_coverage: Coverage from local test execution
            backend_coverage: Coverage from backend/skipped tests

        Returns:
            Merged coverage data (union of both sources)
        """
        merged: t.Dict[str, t.Set[int]] = {}

        # Start with local coverage
        for file_path, lines in local_coverage.items():
            merged[file_path] = lines.copy()

        # Merge backend coverage
        for file_path, backend_lines in backend_coverage.items():
            if file_path in merged:
                merged[file_path] |= backend_lines
            else:
                merged[file_path] = backend_lines.copy()

        return merged

    def normalize_file_paths(
        self, coverage_data: t.Dict[str, t.Set[int]], base_path: str = ""
    ) -> t.Dict[str, t.Set[int]]:
        """
        Normalize file paths in coverage data.

        Args:
            coverage_data: Coverage data with potentially unnormalized paths
            base_path: Base path to resolve relative paths against

        Returns:
            Coverage data with normalized file paths
        """
        normalized: t.Dict[str, t.Set[int]] = {}

        for file_path, lines in coverage_data.items():
            # Remove leading slashes and normalize
            normalized_path = file_path.lstrip("/")
            if base_path and not normalized_path.startswith(base_path):
                normalized_path = f"{base_path.rstrip('/')}/{normalized_path}"
            normalized[normalized_path] = lines

        return normalized
