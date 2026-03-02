"""
Fault-tolerant disk writer for runtime coverage reports.

All I/O errors are silently absorbed — a failing write must never crash the service.
"""

import json
import os
from pathlib import Path
import time
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


log = get_logger(__name__)

_SCHEMA_VERSION = 1


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
    """Write a dead-code JSON report to disk; swallows all I/O errors."""
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

        files[rel_path] = {
            "executable_lines": executable,
            "covered_lines": hit,
            "dead_lines": dead,
        }

    report: dict[str, t.Any] = {
        "schema_version": _SCHEMA_VERSION,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pid": os.getpid(),
        "files": files,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"dd-runtime-coverage-{os.getpid()}.json"

    # Write via a temp file and atomic rename to avoid partial reads on POSIX
    tmp_path = output_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, indent=2))
    tmp_path.replace(output_path)

    log.debug("Runtime coverage report written to %s", output_path)
