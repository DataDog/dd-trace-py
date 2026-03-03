"""
Reader for runtime coverage reports produced by writer.py (schema_version 3).

Decodes the gzip+base64 on-disk format back into per-file line sets.
"""

import base64
import dataclasses
import gzip
import json
from pathlib import Path
import typing as t


@dataclasses.dataclass(frozen=True)
class FileCoverage:
    """Decoded coverage data for a single source file."""

    path: str
    executable_lines: frozenset[int]
    covered_lines: frozenset[int]
    uncovered_lines: frozenset[int]


@dataclasses.dataclass(frozen=True)
class CoverageReport:
    """Full decoded coverage report."""

    schema_version: int
    timestamp: str
    pid: int
    files: dict[str, FileCoverage]


def _bitmap_to_lines(b64: str) -> frozenset[int]:
    """Decode a base64-encoded bitmap into a frozenset of line numbers."""
    raw = base64.b64decode(b64)
    lines: list[int] = []
    for idx, byte in enumerate(raw):
        for bit in range(8):
            if byte & (0b1000_0000 >> bit):
                lines.append(idx * 8 + bit)
    return frozenset(lines)


def read_coverage_report(path: Path) -> CoverageReport:
    """Read a single ``.json.b64`` coverage report file.

    Raises ``ValueError`` on unsupported schema versions.
    """
    compressed = base64.b64decode(path.read_bytes())
    data: dict[str, t.Any] = json.loads(gzip.decompress(compressed))

    version = data["schema_version"]
    if version != 3:
        raise ValueError(f"Unsupported schema version: {version}")

    files: dict[str, FileCoverage] = {}
    for rel_path, entry in data["files"].items():
        executable = _bitmap_to_lines(entry["executable"])
        covered = _bitmap_to_lines(entry["covered"])
        files[rel_path] = FileCoverage(
            path=rel_path,
            executable_lines=executable,
            covered_lines=covered & executable,
            uncovered_lines=executable - covered,
        )

    return CoverageReport(
        schema_version=version,
        timestamp=data["timestamp"],
        pid=data["pid"],
        files=files,
    )


def read_all_reports(directory: Path) -> list[CoverageReport]:
    """Read all ``.json.b64`` coverage reports from a directory."""
    return [read_coverage_report(p) for p in sorted(directory.glob("dd-runtime-coverage-*.json.b64"))]
