"""
Fault-tolerant disk writer for runtime coverage reports.

All I/O errors are silently absorbed — a failing write must never crash the service.
"""

import base64
import gzip
import json
import os
from pathlib import Path
import time
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


log = get_logger(__name__)

_SCHEMA_VERSION = 2


def _dead_lines(all_lines: CoverageLines, covered: CoverageLines) -> list[int]:
    """Return line numbers present in all_lines but absent from covered."""
    result: list[int] = []
    all_bytes = all_lines._lines
    cov_bytes = covered._lines
    for idx in range(len(all_bytes)):
        cov_byte = cov_bytes[idx] if idx < len(cov_bytes) else 0
        # Bits set in all_bytes but not in cov_bytes
        dead_byte = all_bytes[idx] & (~cov_byte & 0xFF)
        for bit in range(8):
            if dead_byte & (0b1000_0000 >> bit):
                result.append(idx * 8 + bit)
    return result


def write_coverage_report(
    executable_lines: dict[str, CoverageLines],
    covered_lines: dict[str, CoverageLines],
    output_dir: Path,
    workspace_path: Path,
) -> None:
    """Write a dead-code coverage report to disk; swallows all I/O errors."""
    try:
        _do_write(executable_lines, covered_lines, output_dir, workspace_path)
    except Exception:
        log.debug("Failed to write runtime coverage report", exc_info=True)


def _do_write(
    executable_lines: dict[str, CoverageLines],
    covered_lines: dict[str, CoverageLines],
    output_dir: Path,
    workspace_path: Path,
) -> None:
    files: dict[str, t.Any] = {}

    for path_str, all_lines in executable_lines.items():
        path = Path(path_str)
        covered = covered_lines.get(path_str, CoverageLines())

        executable = all_lines.to_sorted_list()
        hit = covered.to_sorted_list()
        dead = _dead_lines(all_lines, covered)

        try:
            rel_path = str(path.relative_to(workspace_path))
        except ValueError:
            rel_path = path_str

        # Only store whichever is smaller: covered_lines or dead_lines.
        # The other can be derived from executable_lines.
        file_entry: dict[str, t.Any] = {"executable_lines": executable}
        if len(hit) <= len(dead):
            file_entry["covered_lines"] = hit
        else:
            file_entry["dead_lines"] = dead

        files[rel_path] = file_entry

    report: dict[str, t.Any] = {
        "schema_version": _SCHEMA_VERSION,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pid": os.getpid(),
        "files": files,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"dd-runtime-coverage-{os.getpid()}.json.b64"

    # Compress JSON with gzip and encode as base64
    json_bytes = json.dumps(report, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(json_bytes)
    b64_data = base64.b64encode(compressed)

    # Write via a temp file and atomic rename to avoid partial reads on POSIX
    tmp_path = output_path.with_suffix(".tmp")
    tmp_path.write_bytes(b64_data)
    tmp_path.replace(output_path)

    log.debug("Runtime coverage report written to %s (%d bytes)", output_path, len(b64_data))
