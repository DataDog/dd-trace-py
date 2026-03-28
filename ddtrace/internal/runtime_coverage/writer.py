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

_SCHEMA_VERSION = 5


def _encode_bitmap(cl: CoverageLines) -> str:
    """Encode a CoverageLines bitmap as base64, stripping trailing zero bytes."""
    raw = cl.to_bytes().rstrip(b"\x00")
    return base64.b64encode(raw).decode("ascii")


def write_coverage_report(
    executable_lines: dict[str, CoverageLines],
    covered_lines: dict[str, CoverageLines],
    output_dir: Path,
    workspace_path: Path,
    import_graph: t.Optional[list[dict[str, t.Any]]] = None,
    commit_sha: t.Optional[str] = None,
) -> None:
    """Write a dead-code coverage report to disk; swallows all I/O errors."""
    try:
        _do_write(
            executable_lines,
            covered_lines,
            output_dir,
            workspace_path,
            import_graph=import_graph or [],
            commit_sha=commit_sha,
        )
    except Exception:
        log.debug("Failed to write runtime coverage report", exc_info=True)


def _do_write(
    executable_lines: dict[str, CoverageLines],
    covered_lines: dict[str, CoverageLines],
    output_dir: Path,
    workspace_path: Path,
    import_graph: t.Optional[list[dict[str, t.Any]]] = None,
    commit_sha: t.Optional[str] = None,
) -> None:
    files: dict[str, t.Any] = {}

    for path_str, all_lines in executable_lines.items():
        path = Path(path_str)
        covered = covered_lines.get(path_str, CoverageLines())

        try:
            rel_path = str(path.relative_to(workspace_path))
        except ValueError:
            rel_path = path_str

        files[rel_path] = {
            "executable": _encode_bitmap(all_lines),
            "covered": _encode_bitmap(covered),
        }

    report: dict[str, t.Any] = {
        "schema_version": _SCHEMA_VERSION,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pid": os.getpid(),
        "files": files,
        "import_graph": import_graph or [],
    }
    if commit_sha is not None:
        report["commit_sha"] = commit_sha

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
