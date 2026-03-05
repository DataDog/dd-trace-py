"""
Reader for runtime coverage reports produced by writer.py (schema_version 3-4).

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
    executable_lines: list[int]
    covered_lines: list[int]
    uncovered_lines: list[int]


@dataclasses.dataclass(frozen=True)
class ImportEdge:
    """A single directed edge in the import graph."""

    imported: str
    importer: str
    line: t.Optional[int]


@dataclasses.dataclass(frozen=True)
class CoverageReport:
    """Full decoded coverage report."""

    schema_version: int
    timestamp: str
    pid: int
    files: dict[str, FileCoverage]
    import_graph: list[ImportEdge] = dataclasses.field(default_factory=list)
    commit_sha: t.Optional[str] = None


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
    if version not in (3, 4, 5):
        raise ValueError(f"Unsupported schema version: {version}")

    files: dict[str, FileCoverage] = {}
    for rel_path, entry in data["files"].items():
        executable = _bitmap_to_lines(entry["executable"])
        covered = _bitmap_to_lines(entry["covered"])
        files[rel_path] = FileCoverage(
            path=rel_path,
            executable_lines=sorted(executable),
            covered_lines=sorted(covered & executable),
            uncovered_lines=sorted(executable - covered),
        )

    import_graph = [
        ImportEdge(imported=e["imported"], importer=e["importer"], line=e.get("line"))
        for e in data.get("import_graph", [])
    ]

    return CoverageReport(
        schema_version=version,
        timestamp=data["timestamp"],
        pid=data["pid"],
        files=files,
        import_graph=import_graph,
        commit_sha=data.get("commit_sha"),
    )


def read_all_reports(directory: Path) -> list[CoverageReport]:
    """Read all ``.json.b64`` coverage reports from a directory."""
    return [read_coverage_report(p) for p in sorted(directory.glob("dd-runtime-coverage-*.json.b64"))]
